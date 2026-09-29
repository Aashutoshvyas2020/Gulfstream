"""Antibody: an immune system for buildings. Specialist agents and one coordinator.

Every chat message triggers one scan of the building:
0. Decide: Tier 3 actions held by an earlier scan are approved or rejected by this
   message ("approve A1", {"approve": ["A1"]}). Approved ones run first.
1. Sense and act: each specialist assesses its own problem area and proposes at most one
   action from its own tools (actions.py). Tier 0 cleanups run at once; a Tier 1 fix
   is re-checked and retried at most twice, then escalates; Tier 3 is held for a human.
2. Rank: the coordinator ranks every report by risk x consequence x urgency (plain
   code, so the ranking is auditable) and computes the building health score. Health
   below 70 escalates.
3. Investigate: the coordinator uses Flower connectors: web_search / web_fetch for the
   governing safety standard, Slack and Notion (when the user binds them to the run)
   for tenant reports and open work orders. start_automation is held for approval.
4. Alert and remember: it streams the alert and keeps the building's health history,
   held approvals and the effects of past actions in the run series state.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import ConfigRecord, Context
from openai import OpenAI

from . import actions, building_domain
from .building_data import BUILDING, SNAPSHOT
from .governance import AgentRecord
from .specialists import SPECIALIST_INSTRUCTIONS, SPECIALISTS

# Flower's own Endeavor model via the Flower Runtime; set ANTIBODY_MODEL to run on a local model, e.g. gemma4:latest
MODEL = os.environ.get("ANTIBODY_MODEL", "flower-endeavor-v1.0")
# Built-in connectors, then account connectors (these only work when bound to the run)
CONNECTOR_REFS = ("web_search", "web_fetch", "start_automation", "slack", "notion")
MAX_TOOL_TURNS = 4
# Specialist calls in flight at once. A local Ollama answers one request at a time, so
# parallel calls only queue inside the SuperLink, where they can starve the task heartbeat.
MAX_PARALLEL_AGENTS = int(
    os.environ.get("ANTIBODY_PARALLEL", "1" if "ANTIBODY_MODEL" in os.environ else "6")
)
TOP_ALERTS = 3
MAX_ALERT_MEMORY = 3000  # characters of the previous alert kept in state for follow-ups
STATE_KEY = "antibody"
ACTIONS_KEY = "antibody_actions"  # held approvals and the effects of past actions
MAX_HISTORY = 20
# An agent that failed is unmonitored, not healthy: it counts at this risk in the health score
UNMONITORED_RISK = 0.5

# Structured run events for the Antibody web console (the chat CLI ignores them)
EVENT_SCAN_STARTED = "antibody.scan.started"
EVENT_AGENT_REPORT = "antibody.agent.report"
EVENT_SCAN_RANKED = "antibody.scan.ranked"
EVENT_TOOL = "antibody.tool"
EVENT_ACTION = "antibody.action"
EVENT_FUNNEL = "antibody.scan.funnel"
TEXT_DELTA = "response.output_text.delta"
EMIT_INTERVAL = 0.5  # seconds between batched text events

COORDINATOR_INVESTIGATE = """You are the coordinator of Antibody, an immune system for buildings: {count} AI agents that each hunt one kind of failure.
You receive the ranked reports from the specialist agents. Before the alert is written, investigate with the tools you have:
- For the top {top} problems, look up the governing safety code or standard (for example NFPA 70B, NFPA 25, NFPA 855, NFPA 110, NFPA 101, ASHRAE, IBC, ASCE) so the alert can cite it.
- If Slack or Notion tools are available, search them for tenant complaints, facilities messages or open work orders about the top problems (for example "leak", "water stain", "breaker", "battery", "crack"). A human report that matches a sensor finding raises confidence; an existing work order means say so instead of raising a duplicate.
- Only if the facility manager asks for ongoing monitoring, call start_automation. Set start_at to about two minutes after the current_time given below, keeping its timezone offset (ISO 8601), fixed_interval in seconds, and a bounded max_runs. It is held until the manager approves it; do not call it again once it is held.
Request all independent tool calls for a turn together. Stop calling tools once you have what you need."""

COORDINATOR_ANSWER = """You are the coordinator of Antibody, an immune system for buildings: {count} AI agents that each hunt one kind of failure.
Answer the facility manager's latest message using the scan and the investigation below as evidence. If they asked for a check or a scan, reply in this format:

**Building health: <score>/100** (and the change since the previous scan when a history is given)

| Rank | Area | Status | Risk | Action |
One row per problem area, in the ranked order given, all {count} rows.

**Top {top} alerts**
For each: what is wrong, the evidence (sensor readings plus any matching Slack or Notion report), who to call, the fix, the deadline, and the standard cited with a link when one was found.

**What the agents did**
One line per entry in the scan's actions list: fixes made and re-checked, re-checks that failed, escalations, and approved actions carried out. Omit this section when the list is empty.

**Needs your approval**
One line per entry in pending_approvals: its id, the action, the agent, why, and what it will do. End with: Reply "approve <id>" or "reject <id>" (or "approve all"). Omit this section when there are none.

If an automation was scheduled, confirm when it starts, how often it runs and how many times. If it is only held for approval, say so; never say it is running.

Rules: use only the readings, reports and tool results provided; do not invent results. Say plainly that the readings are simulated demo data. Mention any agent that failed or any source you could not reach."""


app = AgentApp()


def extract_json(text: str) -> dict[str, Any] | None:
    """Return the outermost JSON object in `text`, or None."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def readings_for_scan(prompt: str, persisted: dict[str, dict]) -> dict[str, dict]:
    """Demo snapshot, then the effects of past actions, then any JSON pasted into the chat."""
    readings = {code: dict(values) for code, values in SNAPSHOT.items()}
    for code, values in persisted.items():
        if code in readings:
            readings[code].update(values)
    pasted = extract_json(prompt) or {}
    for code, values in pasted.items():
        code = code.upper()
        if code in readings and isinstance(values, dict):
            readings[code].update(values)
    return readings


def clamp01(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def assess(
    client: OpenAI, record: AgentRecord, code: str, readings: dict, prompt: str
) -> dict[str, Any]:
    """One model call: the specialist's report on its area, as parsed JSON."""
    spec = SPECIALISTS[code]
    response = record.call(
        f"{code.lower()}.assess",
        lambda: client.responses.create(
            model=MODEL,
            instructions=SPECIALIST_INSTRUCTIONS.format(
                code=code, count=len(SPECIALISTS), tools=actions.tools_prompt(code), **spec
            ),
            input=(
                f"Building: {json.dumps(BUILDING)}\n"
                f"Readings for {code}: {json.dumps(readings)}\n"
                f"Facility manager's message: {prompt}"
            ),
        ),
    )
    parsed = extract_json(response.output_text)
    if parsed is None:
        raise ValueError("reply was not a JSON object")
    return parsed


def run_specialist(
    client: OpenAI,
    code: str,
    readings: dict,
    prompt: str,
    run_tag: str,
    approved: list[dict[str, Any]],
) -> dict[str, Any]:
    """One specialist runs its approved actions, assesses its area and acts on it.

    `readings` is this area's own copy; simulated effects are applied to it in place.
    """
    spec = SPECIALISTS[code]
    area = code.lower()
    # Governor: this agent's own session, scoped to its own area
    record = AgentRecord(
        f"antibody-{area}",
        objective=f"Watch the {spec['name']} ({code}); report risk; Tier 0/1 fixes only",
        scope=[area],
        run_tag=run_tag,
    )
    report: dict[str, Any] = {
        "agent_id": f"antibody-{area}",
        "subsystem": code,
        "name": spec["name"],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    done = actions.ActResult()
    proposal = None
    try:
        # Approved Tier 3 actions run before the assessment, so the report shows their effect
        for item in approved:
            step = actions.execute_approved(record, code, item, readings)
            done.outcomes += step.outcomes
            done.changes.update(step.changes)

        parsed = assess(client, record, code, readings, prompt)
        proposal = parsed.get("proposed_action")
        step = actions.act(record, code, proposal, readings)
        done.outcomes += step.outcomes
        done.changes.update(step.changes)
        done.held += step.held
        if step.fixed or done.changes:
            # Re-read after a fix: the report describes the area as it is now
            try:
                parsed = assess(client, record, code, readings, prompt)
            except Exception as exc:
                print(f"{code} re-assessment after its fix failed, keeping the first report: {exc}")
    except Exception as exc:  # one failed agent must not stop the swarm
        return {
            **report,
            "risk_score": 0.0,
            "confidence": 0.0,
            "finding": "Agent failed; area unmonitored this scan",
            "evidence": str(exc)[:200],
            "time_to_failure_days": None,
            "recommended_action": "Re-run the scan or check this area manually",
            "failed": True,
            "actions": done.outcomes,
            "held": done.held,
            "changes": done.changes,
        }
    finally:
        record.close()
    return {
        **report,
        "risk_score": clamp01(parsed.get("risk_score")),
        "confidence": clamp01(parsed.get("confidence")),
        "finding": str(parsed.get("finding", "")),
        "evidence": parsed.get("evidence", ""),
        "time_to_failure_days": parsed.get("time_to_failure_days"),
        "recommended_action": str(parsed.get("recommended_action", "")),
        "proposed_action": proposal,
        "failed": False,
        "actions": done.outcomes,
        "held": done.held,
        "changes": done.changes,
    }


def urgency(time_to_failure_days: Any) -> float:
    """1.0 when failure is far off or unknown, rising to 3.0 when it is a day away."""
    try:
        days = float(time_to_failure_days)
    except (TypeError, ValueError):
        return 1.0
    if days <= 1:
        return 3.0
    if days <= 7:
        return 2.0
    if days <= 30:
        return 1.5
    return 1.0


def rank_reports(reports: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Rank by risk x consequence x urgency and compute the building health score."""
    for r in reports:
        consequence = SPECIALISTS[r["subsystem"]]["consequence"]
        r["consequence"] = consequence
        r["urgency"] = urgency(r["time_to_failure_days"])
        r["priority"] = round(r["risk_score"] * consequence * r["urgency"], 3)
    ranked = sorted(reports, key=lambda r: (-r["priority"], r["subsystem"]))
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
    total = sum(SPECIALISTS[code]["consequence"] for code in SPECIALISTS)
    weighted_risk = sum(
        (UNMONITORED_RISK if r["failed"] else r["risk_score"]) * r["consequence"] for r in reports
    )
    health = round(100 * (1 - weighted_risk / total))
    return ranked, health


def load_memory(context: Context | None) -> dict[str, Any]:
    """Previous scans, the last exchange, held approvals and past action effects.

    Kept in run-series state rather than rebuilt from the event trace: a streamed alert
    is over a thousand events, and replaying them all on every follow-up is slow.
    """
    memory: dict[str, Any] = {
        "history": [],
        "last_prompt": "",
        "last_alert": "",
        "pending": [],
        "overrides": {},
        "next_id": 1,
    }
    if context is None:
        return memory
    if STATE_KEY in context.state:
        record = context.state[STATE_KEY]
        memory["history"] = [
            {"at": at, "health": health, "top": top}
            for at, health, top in zip(record["at"], record["health"], record["top"])
        ]
        memory["last_prompt"] = record.get("last_prompt", "")
        memory["last_alert"] = record.get("last_alert", "")
    if ACTIONS_KEY in context.state:
        record = context.state[ACTIONS_KEY]
        memory["pending"] = json.loads(record.get("pending", "[]"))
        memory["overrides"] = json.loads(record.get("overrides", "{}"))
        memory["next_id"] = int(record.get("next_id", 1))
    return memory


def save_memory(
    context: Context | None, memory: dict[str, Any], health: int, top: str, prompt: str, alert: str
) -> None:
    """Append this scan and keep this exchange so the next run can show trend and context."""
    if context is None:
        return
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    history = (memory["history"] + [{"at": at, "health": health, "top": top}])[-MAX_HISTORY:]
    context.state[STATE_KEY] = ConfigRecord(
        {
            "at": [h["at"] for h in history],
            "health": [h["health"] for h in history],
            "top": [h["top"] for h in history],
            "last_prompt": prompt,
            "last_alert": alert[:MAX_ALERT_MEMORY],
        }
    )
    save_actions(context, memory)


def save_actions(context: Context | None, memory: dict[str, Any]) -> None:
    """Held approvals and action effects, saved as soon as they change."""
    if context is None:
        return
    context.state[ACTIONS_KEY] = ConfigRecord(
        {
            "pending": json.dumps(memory["pending"]),
            "overrides": json.dumps(memory["overrides"]),
            "next_id": memory["next_id"],
        }
    )


class Approvals:
    """Held Tier 3 actions for this run series, with short ids the manager can type."""

    def __init__(self, memory: dict[str, Any], rejected: list[dict[str, Any]]) -> None:
        self.memory = memory
        self.rejected = rejected

    def hold(self, item: dict[str, Any]) -> tuple[str, str]:
        """Add a held action and return (approval id, status).

        An identical pending action keeps its id; one rejected in this message is skipped.
        """
        key = (item["agent"], item["action"], json.dumps(item.get("args", {}), sort_keys=True))
        for rejected in self.rejected:
            if key == (rejected["agent"], rejected["action"], json.dumps(rejected.get("args", {}), sort_keys=True)):
                return rejected["id"], "skipped"
        for existing in self.memory["pending"]:
            if key == (existing["agent"], existing["action"], json.dumps(existing.get("args", {}), sort_keys=True)):
                return existing["id"], "held"
        approval_id = f"A{self.memory['next_id']}"
        self.memory["next_id"] += 1
        self.memory["pending"].append(
            {**item, "id": approval_id, "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        )
        return approval_id, "held"


def connector_tools(agent: AgentSession) -> list[dict[str, Any]]:
    """Collect tools one connector at a time, so an unavailable one does not hide the rest."""
    tools: list[dict[str, Any]] = []
    for ref in CONNECTOR_REFS:
        try:
            tools.extend(agent.connectors.tools([ref]))
        except Exception as exc:  # unbound account connector or unsupported runtime
            print(f"Connector {ref} unavailable: {exc}")
    return tools


def tool_summary(call: dict[str, Any]) -> str:
    """Short human-readable description of a tool call for the console feed."""
    try:
        args = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    for key in ("query", "url", "input", "channel", "page_id"):
        if isinstance(args.get(key), str):
            return args[key][:120]
    return ""


def is_error_output(result: dict[str, Any]) -> bool:
    """True when a function_call_output carries an error object rather than a result."""
    try:
        value = json.loads(result.get("output") or "")
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and "error" in value


def investigate(
    agent: AgentSession,
    client: OpenAI,
    input_items: list[dict[str, Any]],
    record: AgentRecord,
    hold_connector: Callable[[dict[str, Any]], str],
) -> None:
    """Bounded connector loop: standards and Slack / Notion evidence.

    A Tier 3 connector (building_domain.tier, e.g. start_automation) is held for approval.
    """
    tools = connector_tools(agent)
    if not tools:
        return
    allowed = {t["name"] for t in tools if isinstance(t.get("name"), str)}
    print(f"Coordinator tools: {sorted(allowed)}")

    for _ in range(MAX_TOOL_TURNS):
        response = client.responses.create(
            model=MODEL,
            input=input_items,
            instructions=COORDINATOR_INVESTIGATE.format(count=len(SPECIALISTS), top=TOP_ALERTS),
            tools=tools,
            tool_choice="auto",
        )
        output = [item.to_dict() for item in response.output]
        tool_calls = [item for item in output if item.get("type") == "function_call"]
        if not tool_calls:
            return

        outputs = []
        for call in tool_calls:
            name = call.get("name")
            agent.events.emit({"type": EVENT_TOOL, "name": name, "status": "called", "detail": tool_summary(call)})
            try:
                if name not in allowed:
                    raise RuntimeError(f"Tool {name!r} was not exposed")
                if building_domain.tier(name) == actions.APPROVAL:
                    # Tier 3: record the attempt (flagged by Governor) and hold it for approval
                    record.call(name, lambda: None)
                    approval_id = hold_connector(call)
                    result = {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(
                            {"status": "held_for_approval", "approval_id": approval_id,
                             "note": "Not scheduled. It starts only after the facility manager approves it."}
                        ),
                    }
                    agent.events.emit({"type": EVENT_TOOL, "name": name, "status": "held", "detail": approval_id})
                    outputs.append(result)
                    continue
                result = record.call(name, lambda: agent.connectors.call(call))
                failed = is_error_output(result)
            except Exception as exc:  # connector errors go back to the model, not up
                result = {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps({"error": str(exc)}),
                }
                failed = True
            outputs.append(result)
            agent.events.emit({"type": EVENT_TOOL, "name": name, "status": "failed" if failed else "ok"})
        input_items.extend(output)
        input_items.extend(outputs)
        if all(is_error_output(o) for o in outputs):
            print("Every connector call failed, ending the investigation")
            return


def run_approved_connector(
    agent: AgentSession, record: AgentRecord, item: dict[str, Any]
) -> dict[str, Any]:
    """Make a connector call the manager approved; an automation starts shortly after approval."""
    name = item["action"]
    call = {
        "type": "function_call",
        "call_id": f"approved-{item['id']}",
        "name": name,
        "arguments": actions.rescheduled_automation(
            item["args"].get("arguments", "{}"), datetime.now().astimezone()
        ),
    }
    base = dict(approval_id=item["id"], approved_by=item.get("approved_by", ""))
    try:
        result = record.call(name, lambda: agent.connectors.call(call))
        if is_error_output(result):
            return actions.outcome("COORDINATOR", name, actions.APPROVAL, "failed",
                                   detail=str(result.get("output", ""))[:200], **base)
    except Exception as exc:
        return actions.outcome("COORDINATOR", name, actions.APPROVAL, "failed",
                               detail=str(exc)[:200], **base)
    return actions.outcome("COORDINATOR", name, actions.APPROVAL, "executed",
                           detail=call["arguments"], **base)


def stream_alert(agent: AgentSession, stream: Any) -> str:
    """Relay the alert to the frontend in text chunks and return the full text.

    Emitting every token as its own run event means over a thousand writes to the
    SuperLink per alert, which can starve the task heartbeat. Text deltas are batched
    every EMIT_INTERVAL seconds and hidden reasoning deltas are not relayed.
    """
    output_text: list[str] = []
    pending: list[str] = []
    last_flush = time.monotonic()

    def flush() -> None:
        nonlocal last_flush
        if pending:
            agent.events.emit({"type": TEXT_DELTA, "delta": "".join(pending)})
            pending.clear()
        last_flush = time.monotonic()

    for event in stream:
        if event.type in {TEXT_DELTA, "response.refusal.delta"}:
            output_text.append(event.delta)
            pending.append(event.delta)
            if time.monotonic() - last_flush >= EMIT_INTERVAL:
                flush()
            continue
        if event.type.startswith("response.reasoning"):
            continue
        flush()
        agent.events.emit(event.to_dict())
        if event.type in {"error", "response.failed", "response.incomplete"}:
            raise RuntimeError(f"Model response did not complete: {event}")
    flush()
    return "".join(output_text)


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Scan the building with the swarm, act within the tier rules, then answer as the coordinator."""
    client = OpenAI(
        base_url=os.environ["FLWR_RUNTIME_BASE_URL"],
        api_key=os.environ["FLWR_RUNTIME_API_KEY"],
        max_retries=0,
    )
    prompt = agent.prompt
    memory = load_memory(context)
    history = memory["history"]
    readings = readings_for_scan(prompt, memory["overrides"])

    # Governor records for this scan: one file per agent, named by run and time
    run_tag = f"{context.run_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    coordinator = AgentRecord(
        "antibody-coordinator",
        objective="Rank the specialists' reports, investigate the top problems, escalate, "
        "alert the facility manager, run only human-approved Tier 3 actions",
        scope=[*CONNECTOR_REFS, actions.COORDINATOR_AREA],
        run_tag=run_tag,
    )
    outcomes: list[dict[str, Any]] = []

    def emit_outcome(item: dict[str, Any]) -> None:
        outcomes.append(item)
        agent.events.emit({"type": EVENT_ACTION, **item})

    try:
        # 0. Decide: this message approves or rejects actions held by earlier scans
        approve, reject, by = actions.parse_decisions(prompt, extract_json(prompt))
        approved, rejected, memory["pending"] = actions.decide(memory["pending"], approve, reject, by)
        for item in rejected:
            emit_outcome(actions.outcome(item["agent"], item["action"], actions.APPROVAL, "rejected",
                                         approval_id=item["id"]))
        approved_by_agent: dict[str, list[dict[str, Any]]] = {}
        for item in approved:
            if item["agent"] == "COORDINATOR":
                emit_outcome(run_approved_connector(agent, coordinator, item))
            else:
                approved_by_agent.setdefault(item["agent"], []).append(item)
        if approved or rejected:
            save_actions(context, memory)
        approvals = Approvals(memory, rejected)

        # 1. Sense and act: specialists in parallel; each report is emitted as it lands
        agent.events.emit({"type": EVENT_SCAN_STARTED, "areas": list(SPECIALISTS)})
        reports = []
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_AGENTS) as pool:
            futures = [
                pool.submit(
                    run_specialist, client, code, readings[code], prompt, run_tag,
                    approved_by_agent.get(code, []),
                )
                for code in SPECIALISTS
            ]
            for future in as_completed(futures):
                report = future.result()
                code = report["subsystem"]
                if report["changes"]:
                    memory["overrides"].setdefault(code, {}).update(report["changes"])
                reports.append(report)
                agent.events.emit({"type": EVENT_AGENT_REPORT, "report": report})
                for item in report["actions"]:
                    if item["status"] == "escalated":
                        coordinator.call(actions.ESCALATE_ID, lambda: None)
                    emit_outcome(item)
                for item in report["held"]:
                    approval_id, status = approvals.hold(item)
                    emit_outcome(actions.outcome(code, item["action"], actions.APPROVAL, status,
                                                 approval_id=approval_id, reason=item.get("reason", "")))
        save_actions(context, memory)

        # 2. Rank and score; health below the threshold escalates
        ranked, health = rank_reports(reports)
        previous = history[-1]["health"] if history else None
        agent.events.emit(
            {"type": EVENT_SCAN_RANKED, "health": health, "previous_health": previous, "ranked": ranked}
        )
        print(f"Scan done: health {health}/100 (previous {previous}), top area {ranked[0]['subsystem']}")
        if health < actions.HEALTH_ESCALATE:
            coordinator.call(actions.ESCALATE_ID, lambda: None)
            emit_outcome(actions.outcome("COORDINATOR", actions.ESCALATE_ID, actions.ESCALATE, "escalated",
                                         detail=f"building health {health} is below {actions.HEALTH_ESCALATE}"))

        # 3. Investigate with connectors
        def hold_connector(call: dict[str, Any]) -> str:
            reason = ("Facility manager asked for ongoing monitoring"
                      if call.get("name") == actions.AUTOMATION_TOOL else "Tier 3 connector call")
            item = {"agent": "COORDINATOR", "action": call.get("name"),
                    "args": {"arguments": call.get("arguments") or "{}"}, "reason": reason}
            approval_id, status = approvals.hold(item)
            emit_outcome(actions.outcome("COORDINATOR", item["action"], actions.APPROVAL, status,
                                         approval_id=approval_id, reason=item["reason"]))
            save_actions(context, memory)
            return approval_id

        scan = {
            "building": BUILDING,
            "current_time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "health_score": health,
            "health_history": history,
            "escalation_rule": f"health below {actions.HEALTH_ESCALATE} escalates to a human",
            "agents_failed": [r["subsystem"] for r in ranked if r["failed"]],
            "ranked_reports": [
                {k: v for k, v in r.items() if k not in {"actions", "held", "changes"}} for r in ranked
            ],
            "actions": outcomes,
        }
        input_items: list[dict[str, Any]] = []
        if memory["last_prompt"] and memory["last_alert"]:
            input_items += [
                {"type": "message", "role": "user", "content": memory["last_prompt"]},
                {"type": "message", "role": "assistant", "content": memory["last_alert"]},
            ]
        input_items.append({"type": "message", "role": "user", "content": prompt})
        input_items.append(
            {
                "type": "message",
                "role": "user",
                "content": "Swarm scan results (JSON):\n" + json.dumps(scan, indent=1),
            }
        )
        investigate(agent, client, input_items, coordinator, hold_connector)
    finally:
        coordinator.close()

    # The funnel: actions -> autonomous fixes -> escalations -> human decisions
    counts = actions.funnel(outcomes, memory["pending"])
    agent.events.emit({"type": EVENT_FUNNEL, **counts, "pending": memory["pending"]})
    input_items.append(
        {
            "type": "message",
            "role": "user",
            "content": "Actions and approvals (JSON):\n"
            + json.dumps({"funnel": counts, "actions": outcomes, "pending_approvals": memory["pending"]}, indent=1),
        }
    )

    # 4. Alert
    stream = client.responses.create(
        model=MODEL,
        input=input_items,
        instructions=COORDINATOR_ANSWER.format(count=len(SPECIALISTS), top=TOP_ALERTS),
        stream=True,
    )
    alert = stream_alert(agent, stream)
    save_memory(context, memory, health, ranked[0]["subsystem"], prompt, alert)
    print(alert)
