"""Governor pane: what Sentience Governor recorded for the latest Antibody scan.

Follows the newest Flower run's log and prints the [governor] lines the agents write
when their Governor sessions close: one line per recorded action, its operation type,
the flags Governor raised, and the approval that authorized it. Works for runs on the
local SuperLink and on SuperGrid, where the record files themselves stay on the hosted
machine.

Run from this folder with the AgentApp's environment, next to the console:
    ../agent/.venv/bin/python governor_pane.py              # SuperLink from ANTIBODY_SUPERLINK
    ../agent/.venv/bin/python governor_pane.py supergrid    # or name it
It waits for a run, shows its lines, then waits for the next one. Ctrl+C to stop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SUPERLINK = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ANTIBODY_SUPERLINK", "local-agent")
FLWR = str(Path(sys.executable).with_name("flwr"))
ENV = {**os.environ, "PYTHONUTF8": "1"}


def latest_run() -> tuple[str, bool] | None:
    """(run id, finished) of the newest run on the SuperLink, or None."""
    try:
        out = subprocess.run([FLWR, "list", SUPERLINK, "--format", "json"], env=ENV,
                             capture_output=True, text=True, timeout=60).stdout
        runs = json.loads(out).get("runs", [])
    except Exception:
        return None
    if not runs:
        return None
    run = max(runs, key=lambda r: r.get("pending-at") or "")
    return run["run-id"], str(run.get("status", "")).startswith("finished")


def _print_lines(run_id: str, mode: str) -> int:
    proc = subprocess.Popen([FLWR, "log", run_id, SUPERLINK, mode], env=ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    count = 0
    for line in proc.stdout:
        if "[governor]" in line:
            print(line[line.index("[governor]") + len("[governor] "):].rstrip(), flush=True)
            count += 1
    proc.wait()
    return count


def show(run_id: str, finished: bool) -> None:
    print(f"\n=== Governor record, run {run_id} on {SUPERLINK} ({'finished' if finished else 'live'}) ===",
          flush=True)
    if finished:
        _print_lines(run_id, "--show")
        return
    # A live hosted log may not carry printed lines until the run ends: read it again then
    if not _print_lines(run_id, "--stream"):
        _print_lines(run_id, "--show")


def main() -> None:
    seen = None
    print(f"Governor pane: watching {SUPERLINK} for Antibody runs", flush=True)
    while True:
        found = latest_run()
        if found and found[0] != seen:
            seen = found[0]
            show(*found)
        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
