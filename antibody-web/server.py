"""Antibody web console: backend.

Starts real Flower runs of the Antibody AgentApp (the same thing `flwr chat` + `/load`
does) and relays the run's events to the browser as Server-Sent Events.

Run from this folder with the AgentApp's environment:
    ..\\agent\\.venv\\Scripts\\python server.py

Environment:
    ANTIBODY_SUPERLINK   SuperLink connection in ~/.flwr/config.toml (default: local-agent;
                         use "supergrid" once Flower Agent access is enabled)
    ANTIBODY_FEDERATION  Federation to run in (default: the personal one, else the first)
    ANTIBODY_APP_DIR     Path to the AgentApp project (default: ../agent)
    ANTIBODY_PORT        Port for this server (default: 8600)
"""

from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, Iterator

import click
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import local_writes
from agent.building_data import BUILDING, SNAPSHOT
from agent.specialists import SPECIALISTS
from flwr.cli.chat.chat_app import parse_task_event, start_chat_run
from flwr.cli.chat.chat_local_agent import build_local_agent
from flwr.cli.constant import CHAT_FAILURE_EVENTS, CHAT_TERMINAL_EVENTS
from flwr.cli.flower_config import read_superlink_connection
from flwr.cli.utils import flwr_cli_exc_handler, init_http_client_from_connection
from flwr.proto.control_pb2 import (  # pylint: disable=E0611
    ListFederationsRequest,
    ListRunsRequest,
    StopRunRequest,
    StreamRunEventsRequest,
)

HERE = Path(__file__).resolve().parent
local_writes.load_env()  # Notion / Slack tokens for approved writes (antibody-web/.env.local)
STATIC = HERE / "static"
SUPERLINK = os.environ.get("ANTIBODY_SUPERLINK", "local-agent")
APP_DIR = Path(os.environ.get("ANTIBODY_APP_DIR", HERE.parent / "agent")).resolve()
PORT = int(os.environ.get("ANTIBODY_PORT", "8600"))
WATCHDOG_SECONDS = 20  # after this long without events, check whether the run already ended

# Events relayed to the browser; everything else (reasoning deltas, lifecycle noise) is dropped
RELAYED_EVENTS = {
    "antibody.scan.started",
    "antibody.agent.report",
    "antibody.scan.ranked",
    "antibody.tool",
    "antibody.action",
    "antibody.scan.funnel",
    "antibody.governance",
    "response.output_text.delta",
}
# Account connectors a scan may bind (Flower 1.39: personal workspace on SuperGrid only)
ACCOUNT_CONNECTORS = {"slack", "notion"}

app = FastAPI(title="Antibody console")
_state: dict[str, Any] = {"federation": None, "series_id": None}
_lock = threading.Lock()


def control_client():
    return init_http_client_from_connection(read_superlink_connection(SUPERLINK))


def federation() -> str:
    """The federation runs go to: env override, else personal, else the first listed."""
    if _state["federation"]:
        return _state["federation"]
    wanted = os.environ.get("ANTIBODY_FEDERATION")
    if not wanted:
        stub = control_client()
        try:
            with flwr_cli_exc_handler():
                names = [f.name for f in stub.ListFederations(ListFederationsRequest()).federations]
        finally:
            stub.close()
        if not names:
            raise click.ClickException(f"SuperLink '{SUPERLINK}' has no federations")
        wanted = next((n for n in names if n.endswith("/personal")), names[0])
    _state["federation"] = wanted
    return wanted


class ScanRequest(BaseModel):
    prompt: str = "Check the building."
    overrides: dict[str, dict[str, Any]] = {}
    connectors: list[str] = []
    new_series: bool = False


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    try:
        fed = federation()
        error = None
    except click.ClickException as exc:
        fed, error = None, exc.format_message()
    return {
        "building": BUILDING,
        "snapshot": SNAPSHOT,
        "specialists": {
            code: {"name": s["name"], "consequence": s["consequence"]}
            for code, s in SPECIALISTS.items()
        },
        "superlink": SUPERLINK,
        "federation": fed,
        "error": error,
        # Account connectors the site can bind to a scan. They work only on SuperGrid, in the
        # personal federation, once connected at flower.ai > Settings > Connectors.
        "connectors": [] if SUPERLINK == "local-agent" else [
            {"ref": "slack", "name": "Slack", "connected": True,
             "description": "Search Slack for tenant and facilities messages"},
            {"ref": "notion", "name": "Notion", "connected": True,
             "description": "Search Notion for tenant reports and work orders"},
        ],
    }


@app.post("/api/scan")
def scan(req: ScanRequest) -> dict[str, Any]:
    """Build the local AgentApp and start one run; follow-ups continue the series."""
    prompt = req.prompt.strip() or "Check the building."
    if req.overrides:
        prompt += " " + json.dumps(req.overrides)
    with _lock:
        if req.new_series:
            _state["series_id"] = None
        stub = control_client()
        try:
            local = build_local_agent(APP_DIR)
            run_id, series_id = start_chat_run(
                stub,
                prompt,
                federation(),
                _state["series_id"],
                local.app_spec,
                local.fab_hash,
                local.fab_content,
                sorted(set(req.connectors) & ACCOUNT_CONNECTORS),
            )
        except click.ClickException as exc:
            raise HTTPException(status_code=502, detail=exc.format_message()) from None
        finally:
            stub.close()
        _state["series_id"] = series_id
    return {"run_id": str(run_id), "series_id": str(series_id), "prompt": prompt}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def run_status(run_id: int):
    """The run's RunStatus (status, sub_status, details), or None if it cannot be read."""
    stub = control_client()
    try:
        runs = stub.ListRuns(ListRunsRequest(run_id=run_id)).run_dict
        return runs[run_id].status if run_id in runs else None
    except Exception:  # status is best effort; the stream stays the source of truth
        return None
    finally:
        stub.close()


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: int) -> StreamingResponse:
    """Relay one run's events as SSE until it completes or fails."""

    events: queue.Queue = queue.Queue()

    def pump() -> None:
        """Read the Flower event stream on a thread so a stalled stream cannot hang the UI."""
        stub = control_client()
        try:
            with flwr_cli_exc_handler():
                for res in stub.StreamRunEvents(StreamRunEventsRequest(run_id=run_id)):
                    events.put(("event", res.task_event))
            events.put(("end", None))
        except click.ClickException as exc:
            events.put(("error", exc.format_message()))
        except Exception as exc:  # network errors end the relay, not the server
            events.put(("error", str(exc)))
        finally:
            stub.close()

    def stream() -> Iterator[str]:
        threading.Thread(target=pump, daemon=True).start()
        while True:
            try:
                what, item = events.get(timeout=WATCHDOG_SECONDS)
            except queue.Empty:
                # Quiet stream: ask the SuperLink whether the run already ended
                status = run_status(run_id)
                if status is not None and status.status == "finished":
                    detail = status.details or status.sub_status
                    yield _sse({"kind": "failed", "raw": {"message": f"Run {status.sub_status}: {detail}"}})
                    return
                yield ": keepalive\n\n"
                continue
            if what == "error":
                yield _sse({"kind": "failed", "raw": {"message": item}})
                return
            if what == "end":
                yield _sse({"kind": "failed", "raw": {"message": "Run ended without a response"}})
                return
            kind, payload = parse_task_event(item)
            if kind in RELAYED_EVENTS:
                yield _sse({"kind": kind, **payload})
                # An approved Notion / Slack write: carried out here with the laptop's tokens
                if (kind == "antibody.action" and payload.get("status") == "approved"
                        and payload.get("action") in local_writes.ACTIONS):
                    result = local_writes.perform(payload)
                    print(f"local write {payload.get('action')}: {result['status']} {result['detail']}")
                    yield _sse(result)
            elif kind in CHAT_FAILURE_EVENTS:
                yield _sse({"kind": "failed", "raw": payload})
                return
            elif kind in CHAT_TERMINAL_EVENTS:
                yield _sse({"kind": "done"})
                return

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: int) -> dict[str, bool]:
    stub = control_client()
    try:
        with flwr_cli_exc_handler():
            return {"stopped": stub.StopRun(request=StopRunRequest(run_id=run_id)).success}
    except click.ClickException as exc:
        raise HTTPException(status_code=502, detail=exc.format_message()) from None
    finally:
        stub.close()


@app.get("/")
def home() -> FileResponse:
    """The Antibody site (static/site/) if present, else the console."""
    site = STATIC / "site" / "index.html"
    return FileResponse(site if site.exists() else STATIC / "index.html")


@app.get("/console")
def console() -> FileResponse:
    """The full-screen operator console."""
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    print(f"Antibody console on http://127.0.0.1:{PORT}  (SuperLink: {SUPERLINK}, app: {APP_DIR})")
    uvicorn.run(app, host="127.0.0.1", port=PORT)
