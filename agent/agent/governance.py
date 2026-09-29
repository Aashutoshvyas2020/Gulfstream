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

    def __init__(self, agent_id: str, objective: str, scope: list[str], run_tag: str) -> None:
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
            sink = SinkWriter(FileSink(str(TRACE_DIR / f"{run_tag}-{agent_id}.jsonl")))
            self._session = wrap_mcp_client(
                target=self._runner,
                session_manager=session_manager,
                cache=cache,
                sink_writer=sink,
                agent_id=agent_id,
                stated_objective=objective,
                declared_capabilities=scope,
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
