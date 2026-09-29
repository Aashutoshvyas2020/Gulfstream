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
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any, Callable

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import ConfigRecord, Context
from openai import APIStatusError, BadRequestError, OpenAI

from . import actions, building_domain, notion_reports, slack_reports
from .building_data import BUILDING, SNAPSHOT
from .governance import AgentRecord, approved_record, pane_line, summaries
from .specialists import SPECIALIST_INSTRUCTIONS, SPECIALISTS

# Flower's own Endeavor model via the Flower Runtime; set ANTIBODY_MODEL to run on a local model, e.g. gemma4:latest
MODEL = os.environ.get("ANTIBODY_MODEL", "flower-endeavor-v1.0")
# Built-in connectors, then account connectors (these only work when bound to the run)
CONNECTOR_REFS = ("web_search", "web_fetch", "start_automation", "slack", "notion")
# The coordinator always gets the safety-standard lookup. Slack / Notion and
# start_automation are only offered when the message asks for them: every extra tool
# makes each investigation call slower, and Endeavor already needs most of its time limit.
STANDARD_REFS = ("web_search", "web_fetch")
ASKS_FOR_ACCOUNTS = re.compile(r"\b(slack|notion|tenants?|complaints?|work ?orders?|tickets?)\b", re.I)
ASKS_FOR_MONITORING = re.compile(
    r"\b(watch(ing)?|monitor(ing)?|schedule|automation|recurring|every \d+|24/?7|around the clock)\b", re.I
)
# Tunable settings. Their defaults for a run come from Flower run config
# ([tool.flwr.app.config] in pyproject.toml, read from context.run_config), which also
# reaches SuperGrid runs; ANTIBODY_* environment variables override them on a local
# SuperLink. configure() applies both at the start of every run.
MAX_TOOL_TURNS = 2
# The investigation is best effort: each of its model calls gets this many seconds, and a
# slow or failed call ends it so the alert still goes out. A call that blocks for about a
# minute can starve the Flower task heartbeat, and the run is then killed.
INVESTIGATE_TIMEOUT = 30.0
# The connector investigation (safety standards via web_search / web_fetch, Slack,
# Notion). Off by default: on SuperGrid Endeavor has not finished it inside its time
# limit, so it only added 30 s per scan. Turn it on to show the connectors.
INVESTIGATE = False
# The alert call gets this many seconds (between streamed chunks) before the plain-code
# alert takes over. Flower's own model timeout is about 5 minutes, and a chat or console
# connection that hears nothing for that long is dropped.
ALERT_TIMEOUT = 60.0
# Specialists only return a small JSON report: cap their output and ask for low reasoning
# effort. If the model service rejects either, the call is retried without them.
SPECIALIST_MAX_OUTPUT_TOKENS = 2000
REASONING_EFFORT = "low"  # "" sends no reasoning setting
ALERT_MAX_OUTPUT_TOKENS = 1800
# After a Tier 1 fix passes its re-check, the report is updated in code; True asks the
# model to re-assess the area instead (one more call per fixed agent)
REASSESS_AFTER_FIX = False
# True has the model write the top alerts; False (default) has code write them from the
# specialists' own findings, evidence and fixes (also model-written). On SuperGrid the
# alert call has not finished inside ALERT_TIMEOUT, so it only added a minute.
MODEL_ALERT = False
# Search Notion (Flower's Notion connector) once per scan for tenant reports and work
# orders, and match their titles to the areas. Skipped quietly when Notion is not connected.
NOTION_REPORTS = True
# The same for Slack messages (Flower's Slack connector), searched by at-risk area
SLACK_REPORTS = True
# Propose Tier 3 writes back to the humans: a Notion work order for the most urgent
# problem, and a Slack reply to a matching tenant message. After approval they are
# carried out by the web console on the operator's laptop (antibody-web/local_writes.py),
# which holds the Notion and Slack tokens; Flower's connectors are read-only in 1.39.
WRITE_ACTIONS = True
WORK_ORDER_ACTION = "coordinator.create_work_order"
SLACK_REPLY_ACTION = "coordinator.notify_tenants"
LOCAL_WRITE_ACTIONS = {WORK_ORDER_ACTION, SLACK_REPLY_ACTION}
# A small antibody.progress event this often keeps the run's event stream from going quiet
KEEPALIVE_SECONDS = 15
EVENT_PROGRESS = "antibody.progress"
# Model stream events that end a response; stream_alert never relays them
MODEL_END_EVENTS = {"response.completed", "response.incomplete", "response.failed", "error"}
# Report fields the coordinator does not need; left out to keep its model calls small
COORDINATOR_SKIP_FIELDS = {"actions", "held", "changes", "agent_id", "timestamp", "proposed_action"}
# Specialist calls in flight at once. A local Ollama answers one request at a time, so
# parallel calls only queue inside the SuperLink, where they can starve the task heartbeat.
MAX_PARALLEL_AGENTS = 1 if "ANTIBODY_MODEL" in os.environ else 14
TOP_ALERTS = 2

# run config key -> (module setting, type); env var is ANTIBODY_<KEY with - as _>
SETTINGS: dict[str, tuple[str, type]] = {
    "parallel-agents": ("MAX_PARALLEL_AGENTS", int),
    "max-tool-turns": ("MAX_TOOL_TURNS", int),
    "top-alerts": ("TOP_ALERTS", int),
    "investigate": ("INVESTIGATE", bool),
    "investigate-timeout": ("INVESTIGATE_TIMEOUT", float),
    "alert-timeout": ("ALERT_TIMEOUT", float),
    "specialist-max-output-tokens": ("SPECIALIST_MAX_OUTPUT_TOKENS", int),
    "alert-max-output-tokens": ("ALERT_MAX_OUTPUT_TOKENS", int),
    "reasoning-effort": ("REASONING_EFFORT", str),
    "reassess-after-fix": ("REASSESS_AFTER_FIX", bool),
    "model-alert": ("MODEL_ALERT", bool),
    "notion-reports": ("NOTION_REPORTS", bool),
    "slack-reports": ("SLACK_REPORTS", bool),
    "write-actions": ("WRITE_ACTIONS", bool),
}
# Legacy env names kept working
ENV_ALIASES = {"parallel-agents": "ANTIBODY_PARALLEL"}


def _coerce(value: Any, kind: type) -> Any:
    if kind is bool:
        return value if isinstance(value, bool) else str(value).strip().lower() not in {"0", "false", "no", "off", ""}
    return kind(value)


def configure(context: Any) -> dict[str, Any]:
    """Apply run config, then env overrides, to the module settings; return what is in effect.

    A local Ollama (ANTIBODY_MODEL set) keeps one specialist at a time unless the
    parallel setting is given explicitly in the environment.
    """
    run_config = getattr(context, "run_config", None) or {}
    applied: dict[str, Any] = {}
    for key, (name, kind) in SETTINGS.items():
        env = os.environ.get(ENV_ALIASES.get(key, ""), os.environ.get("ANTIBODY_" + key.upper().replace("-", "_")))
        try:
            if env is not None:
                globals()[name] = _coerce(env, kind)
            elif key in run_config and not (key == "parallel-agents" and "ANTIBODY_MODEL" in os.environ):
                globals()[name] = _coerce(run_config[key], kind)
        except (TypeError, ValueError) as exc:
            log(f"setting {key} ignored: {exc}")
        applied[key] = globals()[name]
    return applied


_UNSUPPORTED: set[str] = set()  # request options the model service rejected in this run


def model_options(max_output_tokens: int) -> dict[str, Any]:
    """Speed options for a model call, minus any the model service has rejected."""
    options: dict[str, Any] = {}
    if max_output_tokens and "max_output_tokens" not in _UNSUPPORTED:
        options["max_output_tokens"] = max_output_tokens
    if REASONING_EFFORT and "reasoning" not in _UNSUPPORTED:
        options["reasoning"] = {"effort": REASONING_EFFORT}
    return options


def create_response(client: OpenAI, max_output_tokens: int, **kwargs: Any) -> Any:
    """client.responses.create with the speed options; drop an option the service rejects and retry."""
    for _ in range(3):
        options = model_options(max_output_tokens)
        try:
            return client.responses.create(**kwargs, **options)
        except BadRequestError as exc:
            rejected = [o for o in options if o in str(exc)] or list(options)
            if not options:
                raise
            _UNSUPPORTED.update(rejected)
            log(f"model service rejected {rejected}; retrying without")
    return client.responses.create(**kwargs)
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
EVENT_GOVERNANCE = "antibody.governance"  # one per Governor session: what its record says
TEXT_DELTA = "response.output_text.delta"
EMIT_INTERVAL = 0.5  # seconds between batched text events

COORDINATOR_INVESTIGATE = """You are the coordinator of Antibody, an immune system for buildings: {count} AI agents that each hunt one kind of failure.
You receive the ranked reports from the specialist agents. Before the alert is written, investigate with the tools you have:
- For the top {top} problems, look up the governing safety code or standard (for example NFPA 70B, NFPA 25, NFPA 855, NFPA 110, NFPA 101, ASHRAE, IBC, ASCE) so the alert can cite it.
- If Slack or Notion tools are available, search them for tenant complaints, facilities messages or open work orders about the top problems (for example "leak", "water stain", "breaker", "battery", "crack"). A human report that matches a sensor finding raises confidence; an existing work order means say so instead of raising a duplicate.
- Only if the facility manager asks for ongoing monitoring, call start_automation. Set start_at to about two minutes after the current_time given below, keeping its timezone offset (ISO 8601), fixed_interval in seconds, and a bounded max_runs. It is held until the manager approves it; do not call it again once it is held.
Request all independent tool calls for a turn together. Stop calling tools once you have what you need."""

COORDINATOR_ANSWER = """You are the coordinator of Antibody, an immune system for buildings: {count} AI agents that each hunt one kind of failure.
The facility manager already sees the building health, the ranked table of all areas, what the agents did and what needs approval; that part is written by code. You write only the part below, from the top reports and investigation results given.

If the facility manager's latest message is a question rather than a request to check the building, first answer it in at most two sentences.

**Top {top} alerts**
For each top report, in the order given, at most four short lines: what is wrong; the evidence (sensor readings plus any matching Slack or Notion result); who to call and the fix, with its deadline; and the standard cited with a link, only if the investigation results include one. If there are no top reports, write one line saying nothing needs urgent attention.

If a start_automation entry is given, add one line: held for approval, or scheduled with when it starts, how often and how many times.

Rules: use only what is given; do not invent readings, standards or links. Keep it short."""


app = AgentApp()


def log(message: str) -> None:
    """Timestamped line in the run log, so a killed run shows the step it died in."""
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}", flush=True)


def compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


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
    """The specialist's report on its area, as parsed JSON.

    Retried once when the model service fails (5xx, e.g. "Flower Endeavor providers
    failed" under load) or the reply is cut off by the output cap, then without the cap.
    """
    for attempt in (1, 2):
        cap = SPECIALIST_MAX_OUTPUT_TOKENS if attempt == 1 else 0
        try:
            parsed = _assess_once(client, record, code, readings, prompt, cap)
        except APIStatusError as exc:
            if attempt == 2 or exc.status_code < 500:
                raise
            log(f"{code}: model service error {exc.status_code}, retrying once")
            time.sleep(random.uniform(2, 5))
            continue
        if parsed is not None:
            return parsed
        if attempt == 1:
            log(f"{code}: reply was not a JSON object (cut off?), retrying without the output cap")
    raise ValueError("reply was not a JSON object")


def _assess_once(
    client: OpenAI, record: AgentRecord, code: str, readings: dict, prompt: str, cap: int
) -> dict[str, Any] | None:
    spec = SPECIALISTS[code]
    response = record.call(
        f"{code.lower()}.assess",
        lambda: create_response(
            client,
            cap,
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
    return extract_json(response.output_text)


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
            # Its own Governor session, carrying the approval (id and approver)
            approval = approved_record(f"antibody-{area}", [area], item, run_tag)
            try:
                step = actions.execute_approved(approval, code, item, readings)
            finally:
                approval.close()
            done.outcomes += step.outcomes
            done.changes.update(step.changes)

        parsed = assess(client, record, code, readings, prompt)
        proposal = parsed.get("proposed_action")
        urgent = actions.is_urgent(parsed.get("risk_score"), parsed.get("time_to_failure_days"))
        step = actions.act(record, code, proposal, readings, urgent=urgent)
        done.outcomes += step.outcomes
        done.changes.update(step.changes)
        done.held += step.held
        if step.fixed and REASSESS_AFTER_FIX:
            # Re-read after a fix: the report describes the area as it is now
            try:
                parsed = assess(client, record, code, readings, prompt)
            except Exception as exc:
                log(f"{code} re-assessment after its fix failed, keeping the first report: {exc}")
        elif step.fixed:
            # The fix passed its re-check in code: say so without another model call
            fixed = next(o["action"] for o in step.outcomes if o["status"] == "fixed")
            parsed = {
                **parsed,
                "risk_score": min(clamp01(parsed.get("risk_score")), 0.1),
                "finding": f"Fixed by {fixed}; re-check passed. Before the fix: {parsed.get('finding', '')}",
                "time_to_failure_days": None,
            }
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


def connector_refs(prompt: str) -> tuple[str, ...]:
    """The connectors this message needs: standards always, the rest only when asked for."""
    refs = list(STANDARD_REFS)
    if ASKS_FOR_ACCOUNTS.search(prompt):
        refs += ["slack", "notion"]
    if ASKS_FOR_MONITORING.search(prompt):
        refs.append(actions.AUTOMATION_TOOL)
    return tuple(refs)


def connector_tools(agent: AgentSession, refs: tuple[str, ...] = CONNECTOR_REFS) -> list[dict[str, Any]]:
    """Collect tools one connector at a time, so an unavailable one does not hide the rest."""
    tools: list[dict[str, Any]] = []
    for ref in refs:
        try:
            tools.extend(agent.connectors.tools([ref]))
        except Exception as exc:  # unbound account connector or unsupported runtime
            log(f"Connector {ref} unavailable: {exc}")
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
    refs: tuple[str, ...] = CONNECTOR_REFS,
) -> None:
    """Bounded connector loop: standards and Slack / Notion evidence.

    A Tier 3 connector (building_domain.tier, e.g. start_automation) is held for approval.
    """
    tools = connector_tools(agent, refs)
    if not tools:
        return
    allowed = {t["name"] for t in tools if isinstance(t.get("name"), str)}
    log(f"Coordinator tools: {sorted(allowed)}")

    for turn in range(1, MAX_TOOL_TURNS + 1):
        log(f"investigate: turn {turn} model call")
        started = time.monotonic()
        response = create_response(
            client,
            0,
            model=MODEL,
            input=input_items,
            instructions=COORDINATOR_INVESTIGATE.format(count=len(SPECIALISTS), top=TOP_ALERTS),
            tools=tools,
            tool_choice="auto",
            timeout=INVESTIGATE_TIMEOUT,
        )
        output = [item.to_dict() for item in response.output]
        tool_calls = [item for item in output if item.get("type") == "function_call"]
        log(f"investigate: turn {turn} answered in {time.monotonic() - started:.0f}s, "
            f"{len(tool_calls)} tool call(s)")
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
            log("Every connector call failed, ending the investigation")
            return


# Account-connector lookups (Notion, Slack) get this long per call; a connector that hangs
# (seen with Slack on SuperGrid) is abandoned and the scan carries on without it
CONNECTOR_TIMEOUT = 20.0
_connector_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="connector")


def with_timeout(fn: Callable[[], Any], seconds: float) -> Any:
    """Run fn in a worker thread; TimeoutError if it takes longer (the thread is left to finish)."""
    future = _connector_pool.submit(fn)
    try:
        return future.result(timeout=seconds)
    except FutureTimeout:
        raise TimeoutError(f"no answer in {seconds:.0f} s") from None


def has_tool(agent: AgentSession, ref: str, tool: str) -> bool:
    """Whether an account connector is bound to this run and offers `tool`."""
    try:
        tools = with_timeout(lambda: agent.connectors.tools([ref]), CONNECTOR_TIMEOUT)
    except Exception as exc:  # not connected on this account, or not on this SuperLink
        log(f"{ref}: not connected ({str(exc)[:120]})")
        return False
    if not any(t.get("name") == tool for t in tools):
        log(f"{ref}: {tool} not available")
        return False
    return True


def connector_output(agent: AgentSession, record: AgentRecord, call: dict[str, Any], detail: str) -> Any:
    """One connector call, shown as an antibody.tool call and recorded; its output, or None."""
    agent.events.emit({"type": EVENT_TOOL, "name": call["name"], "status": "called", "detail": detail})
    try:
        result = record.call(call["name"], lambda: with_timeout(lambda: agent.connectors.call(call), CONNECTOR_TIMEOUT))
        failed = is_error_output(result)
    except Exception as exc:
        result, failed = {"output": json.dumps({"error": str(exc)})}, True
    agent.events.emit({"type": EVENT_TOOL, "name": call["name"], "status": "failed" if failed else "ok"})
    if failed:
        log(f"{call['name']}: failed ({str(result.get('output', ''))[:160]})")
        return None
    return result.get("output")


def lookup_notion(agent: AgentSession, record: AgentRecord) -> dict[str, list[dict[str, str]]] | None:
    """One Notion search through Flower's connector; area -> matching report titles.

    None when Notion is not connected to this run (or the search failed).
    """
    if not has_tool(agent, "notion", notion_reports.SEARCH_TOOL):
        return None
    output = connector_output(agent, record, notion_reports.search_call(), "tenant reports and work orders")
    if output is None:
        return None
    found = notion_reports.pages(output)
    matched = notion_reports.match(found)
    log(f"notion: {len(found)} page(s) shared, matched {sorted(matched) or 'none'}")
    return matched


def lookup_slack(
    agent: AgentSession, record: AgentRecord, ranked: list[dict[str, Any]]
) -> dict[str, list[dict[str, str]]]:
    """Slack searches for the at-risk areas' words; area -> matching messages."""
    words = slack_reports.queries(ranked)
    if not words or not has_tool(agent, "slack", slack_reports.SEARCH_TOOL):
        return {}
    found: list[dict[str, str]] = []
    for index, word in enumerate(words):
        output = connector_output(agent, record, slack_reports.search_call(word, index), word)
        if output is None:
            return {}  # a failed or timed-out search: Slack is not usable this scan
        found += slack_reports.messages(output) if output is not None else []
    matched = slack_reports.match_messages(found)
    log(f"slack: searched {words}, {len(found)} message(s), matched {sorted(matched) or 'none'}")
    return matched


def write_proposals(
    ranked: list[dict[str, Any]],
    notion_matches: dict[str, list[dict[str, str]]] | None,
    slack_matches: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Tier 3 writes to propose: a Notion work order for the most urgent problem (only when
    Notion is connected), and a Slack reply to the first tenant message that matches an
    at-risk area."""
    proposals: list[dict[str, Any]] = []
    notion_connected = notion_matches is not None
    urgent = [r for r in ranked if not r["failed"]
              and actions.is_urgent(r["risk_score"], r["time_to_failure_days"])]
    if urgent and notion_connected:
        r = urgent[0]
        existing = [p["title"] for p in (notion_matches or {}).get(r["subsystem"], [])]
        proposals.append({
            "agent": "COORDINATOR",
            "action": WORK_ORDER_ACTION,
            "args": {
                "title": f"Antibody {r['subsystem']} ({r['name']}): {r['finding']}"[:180],
                "details": f"Evidence: {r['evidence']}\nFix: {r['recommended_action']}"[:1800],
                "area": r["subsystem"],
                "related": existing,
            },
            "reason": f"Open a work order in Notion for the most urgent problem ({r['subsystem']}, risk {r['risk_score']:.2f})",
        })
    for r in ranked:
        if r["failed"] or r["risk_score"] < 0.3:
            continue
        message = next((m for m in slack_matches.get(r["subsystem"], []) if m.get("channel_id") and m.get("ts")), None)
        if message:
            proposals.append({
                "agent": "COORDINATOR",
                "action": SLACK_REPLY_ACTION,
                "args": {
                    "channel": message["channel_id"],
                    "thread_ts": message["ts"],
                    "text": (f"Thanks for reporting this. Antibody's {r['name'].lower()} agent confirms it: "
                             f"{r['finding']} A work order is being opened and facilities is on it."),
                    "area": r["subsystem"],
                    "replying_to": message["title"],
                },
                "reason": f"Reply in Slack to the tenant message about {r['subsystem']}",
            })
            break
    return proposals


def hand_off_approved(item: dict[str, Any], run_tag: str) -> dict[str, Any]:
    """An approved Notion or Slack write: recorded here, carried out by the console.

    The console holds the tokens on the operator's laptop and reports the result as an
    antibody.action with status executed or failed.
    """
    record = approved_record("antibody-coordinator", [*CONNECTOR_REFS, actions.COORDINATOR_AREA], item, run_tag)
    try:
        record.call(item["action"], lambda: None)
    finally:
        record.close()
    return actions.outcome("COORDINATOR", item["action"], actions.APPROVAL, "approved",
                           approval_id=item["id"], approved_by=item.get("approved_by", ""),
                           args=item.get("args", {}),
                           detail="handed to the console on the operator's laptop to carry out")


def run_approved_connector(
    agent: AgentSession, item: dict[str, Any], run_tag: str
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
    # Its own Governor session, carrying the approval (id and approver)
    record = approved_record(
        "antibody-coordinator", [*CONNECTOR_REFS, actions.COORDINATOR_AREA], item, run_tag
    )
    try:
        result = record.call(name, lambda: agent.connectors.call(call))
        if is_error_output(result):
            return actions.outcome("COORDINATOR", name, actions.APPROVAL, "failed",
                                   detail=str(result.get("output", ""))[:200], **base)
    except Exception as exc:
        return actions.outcome("COORDINATOR", name, actions.APPROVAL, "failed",
                               detail=str(exc)[:200], **base)
    finally:
        record.close()
    return actions.outcome("COORDINATOR", name, actions.APPROVAL, "executed",
                           detail=call["arguments"], **base)


def status_of(report: dict[str, Any]) -> str:
    """Same thresholds as the console's statusOf()."""
    if report["failed"]:
        return "Offline"
    if report["risk_score"] >= 0.6:
        return "Infected"
    if report["risk_score"] >= 0.3:
        return "Watch"
    return "Healthy"


def top_reports(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The reports the alert explains in words: highest priority, at risk, not failed."""
    return [r for r in ranked if not r["failed"] and r["risk_score"] >= 0.3][:TOP_ALERTS]


def alert_head(health: int, previous: int | None, ranked: list[dict[str, Any]]) -> str:
    """Health and the ranked table, written by code (instant, and never lost to a model timeout)."""
    change = f" ({health - previous:+d} since the previous scan)" if previous is not None else ""
    lines = [
        f"**Building health: {health}/100**{change}",
        "",
        "_Readings are simulated demo data._",
        "",
        "| Rank | Area | Status | Risk | Action |",
        "| :-- | :-- | :-- | :-- | :-- |",
    ]
    for r in ranked:
        action = " ".join(str(r.get("recommended_action") or r.get("finding") or "").split())[:120]
        lines.append(f"| {r['rank']} | {r['subsystem']} {r['name']} | {status_of(r)} | {r['risk_score']:.2f} | {action} |")
    return "\n".join(lines) + "\n\n"


def code_top_alerts(top: list[dict[str, Any]]) -> str:
    """The top alerts written by code, when the model cannot write them."""
    if not top:
        return "Nothing needs urgent attention.\n"
    lines = [f"**Top {len(top)} alert{'s' if len(top) > 1 else ''}**"]
    for r in top:
        parts = [f"- **{r['subsystem']} {r['name']}**: {r['finding']}"]
        if r.get("evidence"):
            parts.append(f"Evidence: {r['evidence']}")
        if r.get("recommended_action"):
            parts.append(f"Fix: {r['recommended_action']}")
        lines.append(" · ".join(parts))
    return "\n".join(lines) + "\n"


def alert_tail(outcomes: list[dict[str, Any]], pending: list[dict[str, Any]]) -> str:
    """What the agents did and what needs approval, written by code."""
    lines: list[str] = []
    if outcomes:
        lines += ["**What the agents did**"]
        for o in outcomes:
            extra = f" (attempt {o['attempt']})" if o.get("attempt") else ""
            detail = f": {o['detail']}" if o.get("detail") else ""
            lines.append(f"- {o['agent']} `{o['action']}`: {o['status'].replace('_', ' ')}{extra}{detail}")
    if pending:
        lines += ["", "**Needs your approval**"] if lines else ["**Needs your approval**"]
        for p in pending:
            why = f": {p['reason']}" if p.get("reason") else ""
            lines.append(f"- **{p['id']}** {p['agent']} `{p['action']}`{why}")
        lines.append('Reply "approve <id>" or "reject <id>" (or "approve all").')
    return "\n".join(lines) + "\n" if lines else ""


def investigation_results(input_items: list[dict[str, Any]], limit: int = 1500) -> list[dict[str, Any]]:
    """Each connector call from the investigation with its (trimmed) result, for the alert."""
    calls = {i.get("call_id"): i for i in input_items if i.get("type") == "function_call"}
    results = []
    for item in input_items:
        if item.get("type") == "function_call_output" and item.get("call_id") in calls:
            call = calls[item["call_id"]]
            results.append({"tool": call.get("name"), "arguments": call.get("arguments"),
                            "result": str(item.get("output", ""))[:limit]})
    return results


def emit_governance(agent: AgentSession) -> None:
    """Send what each closed Governor session recorded: a run event and [governor] log lines."""
    for summary in summaries():
        agent.events.emit({"type": EVENT_GOVERNANCE, **summary})
        line = pane_line(summary)
        if line:
            print(line, flush=True)


def stream_alert(agent: AgentSession, stream: Any, output_text: list[str] | None = None) -> str:
    """Relay the alert to the frontend in text chunks and return the full text.

    `output_text` collects the text as it streams, so a caller still has what was sent
    if the stream fails part way.

    Emitting every token as its own run event means over a thousand writes to the
    SuperLink per alert, which can starve the task heartbeat. Text deltas are batched
    every EMIT_INTERVAL seconds and hidden reasoning deltas are not relayed.
    """
    if output_text is None:
        output_text = []
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
        if event.type in MODEL_END_EVENTS:
            # Not relayed: the chat CLI and the console end the response on these, and the
            # alert still has code-written parts to come. run_scan sends the one final
            # response.completed; a failure falls back to the code-written alert.
            if event.type == "response.completed":
                continue
            raise RuntimeError(f"Model response did not complete: {event}")
        agent.events.emit(event.to_dict())
    flush()
    return "".join(output_text)


class _CodeAlert(Exception):
    """Internal: model-alert is off, so code writes the top alerts."""


class KeepAlive:
    """Emit a small progress event every KEEPALIVE_SECONDS while a scan runs.

    Model calls to Endeavor can take minutes with nothing streamed back; a `flwr chat`
    or console connection that hears nothing for that long is dropped, even though the
    run itself carries on. The chat CLI and the console ignore these events.
    """

    def __init__(self, agent: AgentSession) -> None:
        self.agent = agent
        self.started = time.monotonic()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(KEEPALIVE_SECONDS):
            try:
                self.agent.events.emit(
                    {"type": EVENT_PROGRESS, "elapsed": round(time.monotonic() - self.started)}
                )
            except Exception as exc:  # a missed keep-alive must never stop the scan
                log(f"keep-alive event not sent: {exc}")

    def __enter__(self) -> "KeepAlive":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Scan the building with the swarm, act within the tier rules, then answer as the coordinator."""
    with KeepAlive(agent):
        run_scan(agent, context)


def run_scan(agent: AgentSession, context: Context) -> None:
    """One scan: decide approvals, sense and act, rank, investigate, alert, remember."""
    log(f"settings: {configure(context)}")
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
    notes: list[str] = []  # facts the alert must mention, e.g. an investigation that stopped

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
            if item["agent"] == "COORDINATOR" and item["action"] in LOCAL_WRITE_ACTIONS:
                emit_outcome(hand_off_approved(item, run_tag))
            elif item["agent"] == "COORDINATOR":
                emit_outcome(run_approved_connector(agent, item, run_tag))
            else:
                approved_by_agent.setdefault(item["agent"], []).append(item)
        if approved or rejected:
            save_actions(context, memory)
        approvals = Approvals(memory, rejected)

        # 1. Sense and act: specialists in parallel; each report is emitted as it lands
        agent.events.emit({"type": EVENT_SCAN_STARTED, "areas": list(SPECIALISTS)})
        log(f"sense: {len(SPECIALISTS)} specialists, {MAX_PARALLEL_AGENTS} at a time")
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
                log(f"sense: {code} reported ({len(reports)}/{len(SPECIALISTS)})"
                    + (" FAILED" if report["failed"] else ""))
                agent.events.emit({"type": EVENT_AGENT_REPORT, "report": report})
                for item in report["actions"]:
                    if item["status"] == "escalated":
                        coordinator.call(actions.ESCALATE_ID, lambda: None)
                    emit_outcome(item)
                # Governor: this agent's record, live, so the console can badge it as it reports
                emit_governance(agent)

        # 2. Rank and score; health below the threshold escalates
        ranked, health = rank_reports(reports)

        # Urgent Tier 3 proposals, highest priority first: at most MAX_HELD_PER_SCAN are held
        held_count = 0
        for report in ranked:
            for item in report["held"]:
                if held_count >= actions.MAX_HELD_PER_SCAN:
                    emit_outcome(actions.outcome(
                        report["subsystem"], item["action"], actions.APPROVAL, "advised",
                        reason=item.get("reason", ""),
                        detail=f"lower priority than the {actions.MAX_HELD_PER_SCAN} actions held this scan"))
                    continue
                approval_id, status = approvals.hold(item)
                held_count += status == "held"
                emit_outcome(actions.outcome(report["subsystem"], item["action"], actions.APPROVAL, status,
                                             approval_id=approval_id, reason=item.get("reason", "")))
        save_actions(context, memory)
        previous = history[-1]["health"] if history else None
        agent.events.emit(
            {"type": EVENT_SCAN_RANKED, "health": health, "previous_health": previous, "ranked": ranked}
        )
        log(f"Scan done: health {health}/100 (previous {previous}), top area {ranked[0]['subsystem']}")
        if health < actions.HEALTH_ESCALATE:
            coordinator.call(actions.ESCALATE_ID, lambda: None)
            emit_outcome(actions.outcome("COORDINATOR", actions.ESCALATE_ID, actions.ESCALATE, "escalated",
                                         detail=f"building health {health} is below {actions.HEALTH_ESCALATE}"))

        # Tenant reports and work orders from Notion, matched to the areas
        notion_found = lookup_notion(agent, coordinator) if NOTION_REPORTS else None
        notion_matches = notion_found or {}
        slack_matches = lookup_slack(agent, coordinator, ranked) if SLACK_REPORTS else {}
        if WRITE_ACTIONS:
            for item in write_proposals(ranked, notion_found, slack_matches):
                coordinator.call(item["action"], lambda: None)  # the attempt, flagged by Governor
                approval_id, status = approvals.hold(item)
                emit_outcome(actions.outcome("COORDINATOR", item["action"], actions.APPROVAL, status,
                                             approval_id=approval_id, reason=item["reason"]))
            save_actions(context, memory)

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
                {k: v for k, v in r.items() if k not in COORDINATOR_SKIP_FIELDS} for r in ranked
            ],
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
                "content": "Swarm scan results (JSON):\n" + compact(scan),
            }
        )
        if INVESTIGATE:
            try:
                investigate(agent, client, input_items, coordinator, hold_connector, connector_refs(prompt))
            except Exception as exc:  # best effort: the alert goes out without it
                log(f"investigate: stopped ({type(exc).__name__}: {str(exc)[:200]})")
                notes.append("The investigation stopped early (the model service was slow or unreachable), "
                             "so standards, Slack and Notion could not be checked. Say so in one line.")
        else:
            log("investigate: skipped (ANTIBODY_INVESTIGATE=0)")
    finally:
        coordinator.close()

    # Governor: what each agent's record says, as run events and [governor] log lines,
    # so records written on a hosted SuperGrid machine can still be shown to the operator
    emit_governance(agent)

    # The funnel: actions -> autonomous fixes -> escalations -> human decisions
    counts = actions.funnel(outcomes, memory["pending"])
    agent.events.emit({"type": EVENT_FUNNEL, **counts, "pending": memory["pending"]})

    # 4. Alert. Code writes the health, the table, the actions and the approvals; the model
    # only writes the top alerts, from a small input. If it cannot, code writes those too.
    top = top_reports(ranked)
    head = alert_head(health, previous, ranked)
    tail = alert_tail(outcomes, memory["pending"])
    agent.events.emit({"type": TEXT_DELTA, "delta": head})
    automation = [o for o in outcomes if o["action"] == actions.AUTOMATION_TOOL]
    alert_input: list[dict[str, Any]] = []
    if memory["last_prompt"] and memory["last_alert"]:
        alert_input += [
            {"type": "message", "role": "user", "content": memory["last_prompt"]},
            {"type": "message", "role": "assistant", "content": memory["last_alert"][:1500]},
        ]
    alert_input.append({"type": "message", "role": "user", "content": prompt})
    alert_input.append({
        "type": "message",
        "role": "user",
        "content": "Top reports and investigation (JSON):\n" + compact({
            "top_reports": [{k: v for k, v in r.items() if k not in COORDINATOR_SKIP_FIELDS} for r in top],
            "investigation_results": investigation_results(input_items),
            "notes": notes,
            "start_automation": automation,
            "notion_reports": notion_matches,
            "slack_messages": slack_matches,
        }),
    })
    streamed: list[str] = []
    try:
        if not MODEL_ALERT:
            raise _CodeAlert()
        log(f"alert: model call ({len(top)} top reports)")
        stream = create_response(
            client,
            ALERT_MAX_OUTPUT_TOKENS,
            model=MODEL,
            input=alert_input,
            instructions=COORDINATOR_ANSWER.format(count=len(SPECIALISTS), top=len(top) or TOP_ALERTS),
            stream=True,
            timeout=ALERT_TIMEOUT,
        )
        middle = stream_alert(agent, stream, streamed)
        log(f"alert: done, {len(middle)} characters from the model")
    except _CodeAlert:
        log("alert: written by code from the specialists' findings (model-alert = false)")
        extra = code_top_alerts(top)
        agent.events.emit({"type": TEXT_DELTA, "delta": extra})
        middle = extra
    except Exception as exc:
        reason = "the model service timed out" if "timeout" in str(exc).lower() else type(exc).__name__
        log(f"alert: model failed ({str(exc)[:200]}), code writes the top alerts")
        extra = ("\n\n" if streamed else "") + (
            f"_The coordinator's model call did not finish ({reason}), so these alerts were "
            "written by plain code from the scan._\n\n") + code_top_alerts(top)
        agent.events.emit({"type": TEXT_DELTA, "delta": extra})
        middle = "".join(streamed) + extra
    notion = notion_reports.section(notion_matches, ranked)
    slack = notion_reports.section(slack_matches, ranked, "Slack: tenant and facilities messages")
    closing = "".join("\n\n" + part for part in (notion, slack, tail) if part)
    if closing:
        agent.events.emit({"type": TEXT_DELTA, "delta": closing})
    alert = head + middle + closing
    # The one terminal event: flwr chat and the console show the alert as complete on it
    agent.events.emit({"type": "response.completed", "response": {"status": "completed"}})
    save_memory(context, memory, health, ranked[0]["subsystem"], prompt, alert)
    print(alert)
