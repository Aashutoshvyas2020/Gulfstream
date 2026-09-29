"""Sentience Governor recording for Antibody: observe-only and fail-open.

Every agent gets its own Governor session with an agent_id (antibody-<code>,
antibody-coordinator), a declared objective and a declared scope. Each action the
agent takes goes through `AgentRecord.call(tool_name, fn)`, which records it and runs
it. Governor records and flags; it never blocks, and nothing here can stop a scan:
if Governor is missing or fails, the action runs anyway.

Naming contract (the Governor profiles in governance/ rely on it):
  * Agent actions are named "<area>.<verb>_<object>", e.g. "h2o.shut_valve". Governor
    reads the part before the first dot as the target system.
  * Each specialist declares its own area as its scope, e.g. ["h2o"]. An action on
    another area's system is recorded as outside the declared scope.
  * Connector calls keep Flower's tool names (web_search, web_fetch, slack..., notion...,
    start_automation). The coordinator declares those as its scope.

Operation types come from the building domain adapter (building_domain.py), not from
Governor's guess from words in the tool name: "h2o.shut_valve" is recorded as EXECUTE,
"cyber.run_audit" as READ. This replaces one private Governor hook per session; if that
hook ever changes, recording carries on with Governor's own guess.

Profiles are chosen per agent_id from ~/.sentience/resolution.yaml (Sentience Governor
0.3.2+); see the README's Governance section. Records land in ANTIBODY_TRACE_DIR,
default ~/.sentience/traces/antibody/, one file per agent per scan.
Set ANTIBODY_GOVERNOR=0 to turn recording off.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from .building_domain import operation as domain_operation

TRACE_DIR = Path(
    os.environ.get("ANTIBODY_TRACE_DIR", Path.home() / ".sentience" / "traces" / "antibody")
)
ENABLED = os.environ.get("ANTIBODY_GOVERNOR", "1") != "0"

try:
    from sentience_governor.cache.cache import InProcessCache
    from sentience_governor.schema.events import OperationType
    from sentience_governor.session_manager.manager import SessionManager
    from sentience_governor.sink.writer import FileSink, SinkWriter
    from sentience_governor.wrapper.mcp import wrap_mcp_client
except Exception as exc:  # Governor not installed: scans run unrecorded
    print(f"Sentience Governor unavailable, recording off: {exc}")
    ENABLED = False

_shared: dict[str, Any] = {}
_shared_lock = threading.Lock()

# Summaries of closed sessions for this run, drained by the AgentApp (see summaries()).
# One AgentApp process runs one Flower run, so a module-level list is per run.
_closed: list[dict[str, Any]] = []
# Flags worth showing; POL-003 (context unclassified) fires on every call and is omitted.
NOTABLE_FLAGS = ("HIGH_CONSEQUENCE_DETECTED", "SCOPE_INTENT_MISMATCH", "TASK_BOUNDARY_CROSSED",
                 "SCOPE_OPERATION_UNEXPECTED")


def _collaborators() -> tuple[Any, Any]:
    """One SessionManager and cache for the whole run, created on first use."""
    with _shared_lock:
        if not _shared:
            _shared["sm"] = SessionManager()
            _shared["cache"] = InProcessCache()
        return _shared["sm"], _shared["cache"]


class _Runner:
    """The tool boundary Governor wraps: runs whatever action is pending."""

    def __init__(self) -> None:
        self.pending: Callable[[], Any] | None = None

    def send_tool_call(self, tool_name: str, arguments: dict) -> Any:
        return self.pending() if self.pending else None


class AgentRecord:
    """One agent's Governor session for one scan. Use one record per thread."""

    def __init__(
        self,
        agent_id: str,
        objective: str,
        scope: list[str],
        run_tag: str,
        authorization: str | None = None,
        file_suffix: str = "",
    ) -> None:
        self.agent_id = agent_id
        self.ok = False
        self.domain_typed = False
        self._operation: str | None = None
        if not ENABLED:
            return
        try:
            TRACE_DIR.mkdir(parents=True, exist_ok=True)
            session_manager, cache = _collaborators()
            self._runner = _Runner()
            self.path = TRACE_DIR / f"{run_tag}-{agent_id}{file_suffix}.jsonl"
            sink = SinkWriter(FileSink(str(self.path)))
            self._session = wrap_mcp_client(
                target=self._runner,
                session_manager=session_manager,
                cache=cache,
                sink_writer=sink,
                agent_id=agent_id,
                stated_objective=objective,
                declared_capabilities=scope,
                # Recorded as the authorization claim on this session's declared intent
                owner_claim=authorization,
            )
            # Governor's public entry is `async with`; the scan loop is synchronous and
            # threaded, so the session is opened and closed explicitly.
            self._session._start()
            self.ok = True
            self.domain_typed = self._use_domain_operations()
        except Exception as exc:
            print(f"Governor session for {agent_id} not started: {exc}")

    def _use_domain_operations(self) -> bool:
        """Make this session's proxy take the operation type from the domain adapter.

        Governor infers the type in a private proxy method; it is replaced on this
        session's proxy only (sessions run in parallel threads). Any surprise leaves
        Governor's own guess in place.
        """
        try:
            proxy = self._session._proxy
            fallback = proxy._infer_operation_type

            def infer(tool_name: str, arguments: dict) -> Any:
                if self._operation:
                    return OperationType(self._operation)
                return fallback(tool_name, arguments)

            proxy._infer_operation_type = infer
            return True
        except Exception as exc:
            print(f"Governor operation types for {self.agent_id} fall back to name guessing: {exc}")
            return False

    def call(self, tool_name: str, fn: Callable[[], Any], operation: str | None = None) -> Any:
        """Record `tool_name` and run `fn`. Fail-open: `fn` always runs exactly once.

        `operation` (READ, WRITE, DELETE, EXECUTE) overrides the domain adapter's entry.
        """
        if not self.ok:
            return fn()
        self._operation = operation or domain_operation(tool_name)
        state: dict[str, Any] = {}

        def run() -> Any:
            state["started"] = True
            state["result"] = fn()
            return state["result"]

        self._runner.pending = run
        try:
            return self._session.send_tool_call(tool_name, {})
        except Exception:
            if "result" in state:  # the action ran; only recording after it failed
                return state["result"]
            if state.get("started"):  # the action itself raised
                raise
            return fn()  # recording failed before the action ran
        finally:
            self._runner.pending = None
            self._operation = None

    def close(self) -> None:
        if self.ok:
            try:
                self._session._end()
            except Exception as exc:
                print(f"Governor session for {self.agent_id} not closed cleanly: {exc}")
            self.ok = False
            summary = self.summary()
            if summary:
                with _shared_lock:
                    _closed.append(summary)

    def summary(self) -> dict[str, Any] | None:
        """What this session's Governor record says, read back from its own file.

        Returned small enough to travel as a run event, so records written on a hosted
        machine (SuperGrid) can still be shown on the operator's screen.
        """
        try:
            events = [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        except Exception:
            return None
        out: dict[str, Any] = {"agent": self.agent_id, "record": self.path.name,
                               "approval": None, "profile": None, "actions": []}
        for e in events:
            payload = e.get("payload") or {}
            if e.get("event_type") == "AGENT_REGISTERED":
                out["profile"] = payload.get("profile_binding")
            elif e.get("event_type") == "INTENT_DECLARED":
                out["approval"] = payload.get("authorization_claim")
            elif e.get("event_type") == "SCOPE_ASSERTED":
                flags = [f for f in (e.get("advisory_flags") or []) if f in NOTABLE_FLAGS]
                policies = [v for v in (e.get("policy_violations") or []) if v != "POL-003"]
                out["actions"].append({"tool": payload.get("tool_id"),
                                       "operation": payload.get("operation_type"),
                                       "flags": flags + policies})
        return out


def summaries() -> list[dict[str, Any]]:
    """Take the summaries of every session closed so far in this run."""
    with _shared_lock:
        taken = list(_closed)
        _closed.clear()
    return taken


def pane_line(summary: dict[str, Any]) -> str:
    """One log line per recorded action, prefixed [governor], for the Governor pane."""
    lines = []
    for action in summary["actions"]:
        note = ", ".join(action["flags"]) or "ok"
        if summary["approval"]:
            note += f" | {summary['approval']}"
        lines.append(f"[governor] {summary['agent']:<22} {action['tool']:<34} "
                     f"{action['operation'] or '?':<8} {note}")
    return "\n".join(lines)


def approved_record(agent_id: str, scope: list[str], item: dict[str, Any], run_tag: str) -> AgentRecord:
    """A session for one human-approved Tier 3 action, carrying the approval in the record.

    The proposal was recorded (and flagged) in the agent's own session when it was held.
    The execution runs in this separate session, whose authorization claim names the
    approval id and the approver, so the Governor record tells a held proposal apart from
    an approved execution.
    """
    approver = item.get("approved_by") or "facility manager"
    return AgentRecord(
        agent_id,
        objective=f"Carry out approved action {item['id']}: {item['action']}",
        scope=scope,
        run_tag=run_tag,
        authorization=f"approved by {approver} ({item['id']})",
        file_suffix=f"-approved-{item['id']}",
    )
