"""Antibody: an immune system for buildings. 11 specialist agents and one coordinator.

Every chat message triggers one scan of the building:
1. Sense: the 11 specialist agents each assess their own problem area in parallel.
2. Rank: the coordinator ranks every report by risk x consequence x urgency (plain
   code, so the ranking is auditable) and computes the building health score.
3. Investigate: the coordinator uses Flower connectors: web_search / web_fetch for the
   governing safety standard, Slack and Notion (when the user binds them to the run)
   for tenant reports and open work orders, and start_automation when the manager asks
   for ongoing watch.
4. Alert and remember: it streams the alert and keeps the building's health history in
   the run series state, so every scan can report the trend.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import ConfigRecord, Context
from openai import OpenAI

from .building_data import BUILDING, SNAPSHOT
from .specialists import SPECIALIST_INSTRUCTIONS, SPECIALISTS

# Flower Runtime model on SuperGrid; set ANTIBODY_MODEL to run on a local model, e.g. gemma4:latest
MODEL = os.environ.get("ANTIBODY_MODEL", "openai/gpt-5.6-sol")
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
MAX_HISTORY = 20

# Structured run events for the Antibody web console (the chat CLI ignores them)
EVENT_SCAN_STARTED = "antibody.scan.started"
EVENT_AGENT_REPORT = "antibody.agent.report"
EVENT_SCAN_RANKED = "antibody.scan.ranked"
EVENT_TOOL = "antibody.tool"
TEXT_DELTA = "response.output_text.delta"
EMIT_INTERVAL = 0.5  # seconds between batched text events

COORDINATOR_INVESTIGATE = """You are the coordinator of Antibody, an immune system for buildings: 11 AI agents that each hunt one kind of failure.
You receive the ranked reports from the specialist agents. Before the alert is written, investigate with the tools you have:
- For the top {top} problems, look up the governing safety code or standard (for example NFPA 70B, NFPA 25, NFPA 855, ASHRAE, IBC, ASCE) so the alert can cite it.
- If Slack or Notion tools are available, search them for tenant complaints, facilities messages or open work orders about the top problems (for example "leak", "water stain", "breaker", "battery", "crack"). A human report that matches a sensor finding raises confidence; an existing work order means say so instead of raising a duplicate.
- Only if the facility manager asks for ongoing monitoring, call start_automation. Set start_at to about two minutes after the current_time given below, keeping its timezone offset (ISO 8601), fixed_interval in seconds, and a bounded max_runs.
Request all independent tool calls for a turn together. Stop calling tools once you have what you need."""

COORDINATOR_ANSWER = """You are the coordinator of Antibody, an immune system for buildings: 11 AI agents that each hunt one kind of failure.
Answer the facility manager's latest message using the scan and the investigation below as evidence. If they asked for a check or a scan, reply in this format:

**Building health: <score>/100** (and the change since the previous scan when a history is given)

| Rank | Area | Status | Risk | Action |
One row per problem area, in the ranked order given, all 11 rows.

**Top {top} alerts**
For each: what is wrong, the evidence (sensor readings plus any matching Slack or Notion report), who to call, the fix, the deadline, and the standard cited with a link when one was found.

If an automation was scheduled, confirm when it starts, how often it runs and how many times.

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


def readings_for_scan(prompt: str) -> dict[str, dict]:
    """Demo snapshot, with any area overridden by JSON pasted into the chat."""
    readings = {code: dict(values) for code, values in SNAPSHOT.items()}
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


def run_specialist(client: OpenAI, code: str, readings: dict, prompt: str) -> dict[str, Any]:
    """One specialist agent assesses its own problem area and returns a report."""
    spec = SPECIALISTS[code]
    report: dict[str, Any] = {
        "agent_id": f"antibody-{code.lower()}",
        "subsystem": code,
        "name": spec["name"],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        response = client.responses.create(
            model=MODEL,
            instructions=SPECIALIST_INSTRUCTIONS.format(code=code, **spec),
            input=(
                f"Building: {json.dumps(BUILDING)}\n"
                f"Readings for {code}: {json.dumps(readings)}\n"
                f"Facility manager's message: {prompt}"
            ),
        )
        parsed = extract_json(response.output_text)
        if parsed is None:
            raise ValueError("reply was not a JSON object")
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
        }
    return {
        **report,
        "risk_score": clamp01(parsed.get("risk_score")),
        "confidence": clamp01(parsed.get("confidence")),
        "finding": str(parsed.get("finding", "")),
        "evidence": parsed.get("evidence", ""),
        "time_to_failure_days": parsed.get("time_to_failure_days"),
        "recommended_action": str(parsed.get("recommended_action", "")),
        "failed": False,
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
    weighted_risk = sum(r["risk_score"] * r["consequence"] for r in reports)
    health = round(100 * (1 - weighted_risk / total))
    return ranked, health


def load_memory(context: Context | None) -> dict[str, Any]:
    """Previous scans and the last exchange in this run series (empty on the first scan).

    Kept in run-series state rather than rebuilt from the event trace: a streamed alert
    is over a thousand events, and replaying them all on every follow-up is slow.
    """
    memory: dict[str, Any] = {"history": [], "last_prompt": "", "last_alert": ""}
    if context is None or STATE_KEY not in context.state:
        return memory
    record = context.state[STATE_KEY]
    memory["history"] = [
        {"at": at, "health": health, "top": top}
        for at, health, top in zip(record["at"], record["health"], record["top"])
    ]
    memory["last_prompt"] = record.get("last_prompt", "")
    memory["last_alert"] = record.get("last_alert", "")
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


def investigate(
    agent: AgentSession, client: OpenAI, input_items: list[dict[str, Any]]
) -> None:
    """Bounded connector loop: standards, Slack / Notion evidence, automations."""
    tools = connector_tools(agent)
    if not tools:
        return
    allowed = {t["name"] for t in tools if isinstance(t.get("name"), str)}
    print(f"Coordinator tools: {sorted(allowed)}")

    for _ in range(MAX_TOOL_TURNS):
        response = client.responses.create(
            model=MODEL,
            input=input_items,
            instructions=COORDINATOR_INVESTIGATE.format(top=TOP_ALERTS),
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
                result = agent.connectors.call(call)
                failed = '"error"' in str(result.get("output", ""))[:400]
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
        if all('"error"' in str(o.get("output", ""))[:400] for o in outputs):
            print("Every connector call failed, ending the investigation")
            return


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
    """Scan the building with the swarm, then answer as the coordinator."""
    client = OpenAI(
        base_url=os.environ["FLWR_RUNTIME_BASE_URL"],
        api_key=os.environ["FLWR_RUNTIME_API_KEY"],
        max_retries=0,
    )
    prompt = agent.prompt
    readings = readings_for_scan(prompt)
    memory = load_memory(context)
    history = memory["history"]

    # 1. Sense: 11 specialist agents in parallel; each report is emitted as it lands
    agent.events.emit({"type": EVENT_SCAN_STARTED, "areas": list(SPECIALISTS)})
    reports = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_AGENTS) as pool:
        futures = [
            pool.submit(run_specialist, client, code, readings[code], prompt)
            for code in SPECIALISTS
        ]
        for future in as_completed(futures):
            report = future.result()
            reports.append(report)
            agent.events.emit({"type": EVENT_AGENT_REPORT, "report": report})

    # 2. Rank and score, then remember the result for the trend
    ranked, health = rank_reports(reports)
    previous = history[-1]["health"] if history else None
    agent.events.emit(
        {"type": EVENT_SCAN_RANKED, "health": health, "previous_health": previous, "ranked": ranked}
    )
    print(f"Scan done: health {health}/100 (previous {previous}), top area {ranked[0]['subsystem']}")

    # 3. Investigate with connectors
    scan = {
        "building": BUILDING,
        "current_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "health_score": health,
        "health_history": history,
        "agents_failed": [r["subsystem"] for r in ranked if r["failed"]],
        "ranked_reports": ranked,
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
    investigate(agent, client, input_items)

    # 4. Alert
    stream = client.responses.create(
        model=MODEL,
        input=input_items,
        instructions=COORDINATOR_ANSWER.format(top=TOP_ALERTS),
        stream=True,
    )
    alert = stream_alert(agent, stream)
    save_memory(context, memory, health, ranked[0]["subsystem"], prompt, alert)
    print(alert)
