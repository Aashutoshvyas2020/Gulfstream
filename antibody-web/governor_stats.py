"""Governor records across runs and agents, for the console's Governor view.

Reads the Sentience Governor record files the agents write on this machine
(ANTIBODY_TRACE_DIR, default ~/.sentience/traces/antibody/; one file per agent per scan,
named "<run id>-<UTC time>-<agent id>[-approved-<id>].jsonl") and summarises them per run
and per agent type. Local runs only: on SuperGrid the files stay on Flower's machine.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

TRACE_DIR = Path(os.environ.get("ANTIBODY_TRACE_DIR", Path.home() / ".sentience" / "traces" / "antibody"))
NAME = re.compile(r"^(?P<run>\d+)-(?P<time>\d{8}T\d{6}Z)-(?P<agent>antibody-[a-z0-9]+)(?:-approved-(?P<approval>[A-Za-z0-9]+))?\.jsonl$")
# Flags shown in the view; POL-003 (context unclassified) fires on every call and is left out
FLAGS = {
    "HIGH_CONSEQUENCE_DETECTED": "high_consequence",
    "SCOPE_INTENT_MISMATCH": "outside_lane",
    "TASK_BOUNDARY_CROSSED": "scan_to_fix",
    "SCOPE_OPERATION_UNEXPECTED": "undeclared",
    "POL-001": "undeclared",
}


def _read(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _session(path: Path, match: re.Match) -> dict[str, Any]:
    """One record file: its actions (tool, operation type, flags) and approval."""
    s: dict[str, Any] = {"run": match["run"], "time": match["time"], "agent": match["agent"],
                         "approval_id": match["approval"], "approved_by": None, "profile": None, "actions": []}
    for e in _read(path):
        kind, p = e.get("event_type"), e.get("payload") or {}
        if kind == "AGENT_REGISTERED":
            s["profile"] = p.get("profile_binding")
        elif kind == "INTENT_DECLARED" and p.get("authorization_claim"):
            s["approved_by"] = p["authorization_claim"]
        elif kind == "SCOPE_ASSERTED":
            raw = (e.get("advisory_flags") or []) + (e.get("policy_violations") or [])
            flags = sorted({FLAGS[f] for f in raw if f in FLAGS})
            s["actions"].append({"tool": p.get("tool_id"), "operation": p.get("operation_type"), "flags": flags})
    return s


def collect(trace_dir: Path = TRACE_DIR, max_runs: int = 30) -> dict[str, Any]:
    """Totals, runs (newest first), agent types, and an agent x run grid of flags."""
    sessions = []
    if trace_dir.is_dir():
        for path in trace_dir.glob("*.jsonl"):
            m = NAME.match(path.name)
            if m:
                sessions.append(_session(path, m))

    runs: dict[str, dict[str, Any]] = {}
    agents: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "runs": set(), "actions": 0, "operations": Counter(), "flags": Counter(), "approvals": 0, "tools": Counter(),
        "no_profile": 0})
    approvals = []
    for s in sessions:
        key = f"{s['run']}-{s['time']}"
        r = runs.setdefault(key, {"run": s["run"], "time": s["time"], "agents": set(), "actions": 0,
                                  "flags": Counter(), "approvals": 0, "by_agent": Counter()})
        r["agents"].add(s["agent"])
        a = agents[s["agent"]]
        a["runs"].add(key)
        if not s["profile"]:
            a["no_profile"] += 1
        if s["approved_by"]:
            r["approvals"] += 1
            a["approvals"] += 1
            approvals.append({"run": s["run"], "time": s["time"], "agent": s["agent"],
                              "approval": s["approved_by"], "actions": [x["tool"] for x in s["actions"]]})
        for x in s["actions"]:
            r["actions"] += 1
            a["actions"] += 1
            a["operations"][x["operation"] or "?"] += 1
            a["tools"][x["tool"]] += 1
            for f in x["flags"]:
                r["flags"][f] += 1
                a["flags"][f] += 1
            if "high_consequence" in x["flags"] or "outside_lane" in x["flags"]:
                r["by_agent"][s["agent"]] += 1

    ordered = sorted(runs, key=lambda k: runs[k]["time"], reverse=True)[:max_runs]
    agent_ids = sorted(agents)
    return {
        "learnings": learnings(sessions, agents, runs),
        "source": str(trace_dir),
        "totals": {
            "runs": len(runs), "sessions": len(sessions),
            "actions": sum(a["actions"] for a in agents.values()),
            "high_consequence": sum(a["flags"]["high_consequence"] for a in agents.values()),
            "outside_lane": sum(a["flags"]["outside_lane"] for a in agents.values()),
            "approvals": len(approvals),
        },
        "runs": [{
            "run": runs[k]["run"], "time": runs[k]["time"], "agents": len(runs[k]["agents"]),
            "actions": runs[k]["actions"], "flags": dict(runs[k]["flags"]), "approvals": runs[k]["approvals"],
        } for k in ordered],
        "agents": [{
            "agent": aid, "runs": len(agents[aid]["runs"]), "actions": agents[aid]["actions"],
            "operations": dict(agents[aid]["operations"]), "flags": dict(agents[aid]["flags"]),
            "approvals": agents[aid]["approvals"], "no_profile": agents[aid]["no_profile"],
            "top_tools": [t for t, _ in agents[aid]["tools"].most_common(3)],
        } for aid in agent_ids],
        # agent x run: count of high-consequence or outside-lane actions (runs oldest -> newest)
        "grid": {"runs": [f"{runs[k]['run']}-{runs[k]['time']}" for k in reversed(ordered)], "agents": agent_ids,
                 "cells": [[runs[k]["by_agent"][aid] if aid in runs[k]["agents"] else None
                            for k in reversed(ordered)] for aid in agent_ids]},
        "approvals": sorted(approvals, key=lambda x: x["time"], reverse=True)[:20],
    }


def learnings(sessions: list[dict[str, Any]], agents: dict[str, dict[str, Any]], runs: dict[str, Any]) -> list[dict[str, str]]:
    """Plain facts from the records, computed in code (no model), for the console."""
    out: list[dict[str, str]] = []
    actions = [x for s in sessions for x in s["actions"]]
    if not actions:
        return [{"kind": "info", "text": "No Governor records yet. Run a scan on the local SuperLink."}]
    n = len(actions)
    hc = sum("high_consequence" in x["flags"] for x in actions)
    out.append({"kind": "info", "text": f"{hc} of {n} recorded actions ({100 * hc // n}%) were high-consequence under the Governor profiles."})
    ops = Counter(x["operation"] or "?" for x in actions)
    out.append({"kind": "info", "text": "Operation mix: " + ", ".join(f"{k} {100 * v // n}%" for k, v in ops.most_common()) + "."})
    ranked = sorted(agents.items(), key=lambda kv: kv[1]["flags"]["high_consequence"], reverse=True)
    if ranked and ranked[0][1]["flags"]["high_consequence"]:
        aid, a = ranked[0]
        out.append({"kind": "focus", "text": f"{aid} proposed the most high-consequence actions: {a['flags']['high_consequence']} "
                                                f"across {len(a['runs'])} runs (mostly {', '.join([t for t, _ in a['tools'].most_common() if not t.endswith('.assess')][:2])})."})
    quiet = [aid for aid, a in agents.items() if a["actions"] and all(t.endswith(".assess") for t in a["tools"])]
    if quiet:
        out.append({"kind": "info", "text": f"Only assessed, never acted: {', '.join(sorted(quiet))}."})
    no_profile = sum(1 for s in sessions if not s["profile"])
    if no_profile:
        out.append({"kind": "warn", "text": f"{no_profile} agent sessions ran with no Governor profile: nothing was flagged for them."})
    else:
        out.append({"kind": "ok", "text": f"Every one of {len(sessions)} agent sessions was bound to a Governor profile."})
    outside = sum("outside_lane" in x["flags"] for x in actions)
    out.append({"kind": "warn" if outside else "ok",
                "text": f"{outside} actions outside an agent's declared lane." if outside else "No agent acted outside its declared lane."})
    approved = sum(1 for s in sessions if s["approved_by"])
    out.append({"kind": "info", "text": f"{approved} approved execution(s) recorded with the approver. "
                                        "Held proposals are recorded as the proposed action and are not executions."})
    return out
