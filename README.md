# Antibody

An immune system for buildings, built on [Flower](https://flower.ai/docs/agent/). 14 AI agents each hunt one kind of building failure, and a coordinator ranks what to fix first, looks up the governing safety standard and alerts the facility manager. It runs on a Flower SuperLink using Flower's own model, Endeavor. Building data stays on-site; the model calls go to Flower's model service. For a fully offline run, it can use a local model through Ollama instead.

| Agent | Watches | Agent | Watches |
| :-- | :-- | :-- | :-- |
| PWR | Battery bank | HVAC | Rooftop chiller |
| ELEC | Switchgear | H2O | Pipe leaks |
| WIRE | Wall wiring | ENV | Facade |
| FIRE | Sprinkler riser | AIR | Air quality |
| STR | Concrete column | CYBER | Building control system (BMS) |
| LIFT | Elevator | | |

Sensor readings are simulated (`agent/agent/building_data.py`). Agent reasoning, ranking, connector calls and alerts are live.

## Repo layout

```
agent/                  Flower AgentApp (flwr 1.39)
  agent/agent_app.py    scan flow: sense → rank → investigate → alert → remember
  agent/specialists.py  the 14 agents' job descriptions and consequence weights
  agent/building_data.py simulated demo building ("Harbor Point Tower")
antibody-web/           web backend + operator console
  server.py             FastAPI: starts real Flower runs, relays run events over SSE
  static/index.html     operator console (served at / and /console)
agent/agent/governance.py  Sentience Governor record per agent (see Governance)
governance/             Governor profiles + resolution.yaml (which profile governs which agent)
docs/                   design notes and Flower architecture reference
```

## Quick start (local Flower SuperLink, no SuperGrid access needed)

Needs [uv](https://docs.astral.sh/uv/), Python 3.11+ and a Flower API key (flower.ai → Profile → Settings → API Keys). Commands are for macOS/Linux; PowerShell equivalents are in [`antibody-web/README.md`](antibody-web/README.md).

**1. Install** (also installs Sentience Governor):

```bash
cd agent
uv sync
```

**2. Governor setup** (once per machine, from the repo root):

```bash
mkdir -p ~/.sentience && cp -R governance/profiles ~/.sentience/ && cp governance/resolution.yaml ~/.sentience/
```

**3. Local SuperLink with Endeavor**, from `agent/`, in its own terminal. Model calls go to Flower's model service, and the default model is `flower-endeavor-v1.0`:

```bash
unset FLWR_MODEL_API_ENDPOINT ANTIBODY_MODEL
export PYTHONUTF8=1 FLWR_MODEL_API_KEY="<your Flower API key>"
uv run flower-superlink --insecure
```

Add a `local-agent` connection to `~/.flwr/config.toml`:

```toml
[superlink.local-agent]
address = "127.0.0.1:8000"
insecure = true
```

**4. Scan.** Either run the web console (see [`antibody-web/README.md`](antibody-web/README.md)), open http://127.0.0.1:8600 and press **Scan building**, or use the terminal:

```bash
cd agent
export PYTHONUTF8=1 FLWR_CHAT_SUPERLINK=local-agent
uv run flwr chat        # then: /load .   and   Check the building.
```

**5. Governor console** (another terminal): see [Governance](#governance-sentience-governor).

### Offline mode (Ollama)

To keep every model call on the laptop, run [Ollama](https://ollama.com) with a local model (tested with `gemma4`) and point the SuperLink at it:

```bash
export OLLAMA_CONTEXT_LENGTH=32768 && ollama serve     # terminal 1
```

```bash
cd agent                                               # terminal 2
export PYTHONUTF8=1 FLWR_MODEL_API_ENDPOINT="http://127.0.0.1:11434/v1/responses" ANTIBODY_MODEL="gemma4:latest"
uv run flower-superlink --insecure
```

With `ANTIBODY_MODEL` set, the 14 specialists run one at a time (Ollama answers one request at a time), so a scan takes about 3–4 minutes on a laptop GPU. With Endeavor they run six at a time; scan time not measured yet.

## How a scan works

1. **Sense and act.** The specialists each read their system and return a JSON report (risk 0–1, confidence, finding, evidence, time to failure, fix) and at most one proposed action from their own tools, run under the tier rules below. Each specialist's assessment and actions are recorded in its own Governor session.
2. **Rank.** Plain code scores `risk × consequence × urgency` and computes building health out of 100.
3. **Investigate.** The coordinator calls Flower connectors: `web_search` / `web_fetch` for the safety standard, `slack` / `notion` for tenant reports and work orders (when bound), `start_automation` for "Watch 24/7", held until the manager approves it. Every connector call is recorded in the coordinator's Governor session, and `start_automation` is flagged as high-consequence.
4. **Alert.** The alert streams to the console or `flwr chat`.
5. **Remember.** Health history and the last exchange are kept in Flower run-series state (`context.state`), so the next scan reports the trend.

The agent emits `antibody.scan.started`, `antibody.agent.report`, `antibody.scan.ranked`, `antibody.tool`, `antibody.action` and `antibody.scan.funnel` run events. `server.py` relays them to the browser. This event contract is the boundary between the three lanes below.

### Actions and approvals (`agent/agent/actions.py`)

Each specialist may propose one action from its own tools. The tier decides what happens; the model never does.

| Tier | What happens | `antibody.action` status |
| :-- | :-- | :-- |
| 0 Cleanup | Runs at once | `done` |
| 1 Self-correction | Runs, re-checks its own fix; after 2 failed re-checks it escalates | `fixed`, `recheck_failed`, then `escalated` |
| 2 Escalate | Also fired when building health is below 70, or an agent asks for another area's tool | `escalated`, `refused` |
| 3 Human approval | Held with an id (`A1`, `A2`, …); `start_automation` is held the same way | `held`, then `executed` or `rejected` in a later message |

A Flower run is one chat message, so a held action is decided in the next message of the run series: `approve A1`, `reject A2`, `approve all`, or JSON `{"approve": ["A1"], "by": "Dana"}`. Held actions and the simulated effects of past actions are kept in run-series state.

`antibody.action` carries `agent`, `action` (e.g. `h2o.shut_valve`), `tier`, `status` and, when present, `attempt`, `approval_id`, `approved_by`, `reason`, `detail`. `antibody.scan.funnel` carries `actions`, `auto_fixes`, `escalations`, `human_decisions` and `pending` (the held actions, for the approval prompt).

Tests: `cd agent && python -m unittest discover -s tests -v` (fake model, no SuperLink).

## Three lanes (one owner each)

| Lane | Owner | Owns | Starts from |
| :-- | :-- | :-- | :-- |
| **1. Agents** | Roansh Desai | `agent/` | Tier 0–3 actions: add `<area>.<verb>_<object>` tools per agent (e.g. `hvac.reset_damper`, `h2o.shut_valve`), fixed thresholds in code (health < 70 escalates, 2 retries), an approval gate for Tier 3 actions and `start_automation` |
| **2. Flower integration** | Aashutosh Vyas | `antibody-web/server.py`, SuperLink / SuperGrid setup | Publish the Flower Hub app (required for submission); get SuperGrid access working; confirm the heartbeat fix; real network immunity with 2–3 buildings as SuperNodes using `agent.grid` (`get_nodes`, `push_messages`, `pull_messages`): lessons travel, readings never do |
| **3. Frontend** | Rikin Shah | `antibody-web/static/` | Tier 3 approval prompt, the "actions → auto-fixes → escalations → 1 human decision" funnel, a multi-building view for network immunity |
| **Sentience Governor** | Roansh Desai (Claude supporting) | `agent/agent/governance.py`, `governance/` | One Governor session per agent (`antibody-<code>`, `antibody-coordinator`); route every new action through `AgentRecord.call` with its `operation` (READ / WRITE / EXECUTE, so fixes are recorded as writes); keep the naming contract below |

If you change the shape of a run event, change it in all three lanes in the same pull request.

## Governance (Sentience Governor)

Every agent keeps its own [Sentience Governor](https://github.com/crescerelabs/sentience-governor) record: what it declared it would do (objective and scope), each action it took, and flags where the two diverge. Governor records and flags; it never blocks. The approval gate for Tier 3 actions is Antibody's own code.

- **Where:** `agent/agent/governance.py`, wired into `run_specialist()` (each specialist) and `investigate()` (the coordinator's connector calls).
- **Naming contract:** agent actions are `<area>.<verb>_<object>` (e.g. `h2o.shut_valve`); each specialist declares its own area as its scope. An action on another area's system is flagged as outside declared scope.
- **Flagged as high-consequence** (profiles in `governance/profiles/`):
  - Tier 3 actions: `trip_breaker`, `shut_valve`, `isolate_zone`, `dispatch_contractor`, `notify_tenants`;
  - `start_automation`;
  - every CYBER change: `close_port`, `reset_password`, and others.

**Setup** (once, on the machine running the SuperLink; needs Sentience Governor 0.3.2.1+, installed with the agent's dependencies):

```bash
mkdir -p ~/.sentience && cp -R governance/profiles ~/.sentience/ && cp governance/resolution.yaml ~/.sentience/
```

**Governor console** (second terminal): records land in `~/.sentience/traces/antibody/`, one file per agent per scan.

```bash
sentience open ~/.sentience/traces/antibody/<file>.jsonl --summary
```

Set `ANTIBODY_GOVERNOR=0` to turn recording off. If Governor is missing or fails, scans run unrecorded; nothing stops.

## Status

| Works (tested locally) | Not tested yet | Not built yet |
| :-- | :-- | :-- |
| The original 11 agents + coordinator on a local SuperLink with Ollama | Endeavor (`flower-endeavor-v1.0`) through Flower's model service, including the model ID | Flower Hub app (not published yet) |
| Ranking, health score, trend memory across scans | Governor records in a full Flower scan (tested against Governor 0.3.2.1 directly, not end to end) | Multi-building network immunity (`agent.grid` / SuperNodes) |
| `start_automation` (SuperLink log shows `start-automation` 200) | SuperGrid: account returns `Entitlement error ... Deployment Runtime is not allowed` | |
| Web console streaming a full scan | `web_search` locally: needs `TAVILY_API_KEY`, `BRAVE_API_KEY`, `EXA_API_KEY` or `FLWR_WEB_SEARCH_ENDPOINT` on the SuperLink | |
| Tier 0–3 actions, approval gate and the GEN / GAS / EGRESS agents with a fake model (`agent/tests`, 19 tests) | Slack / Notion with real accounts (SuperGrid personal workspace only in flwr 1.39) | |
| | Tier 0–3 actions and approvals in a real Flower scan; approved `start_automation` on a real SuperLink | |
| | Console display of `antibody.action` / `antibody.scan.funnel` (needs both in `RELAYED_EVENTS` in `server.py`) | |

## Known issue: "No heartbeat received from the task"

On the local SuperLink with Ollama, some scans are killed 1–4 minutes in. Endeavor mode (six specialists at a time) has not been checked for this yet. Fixes so far: alert text is sent in 0.5 s batches (it was about 1,300 events per alert), and follow-ups use run-series state instead of `get_trace()`. The latest fix is **not verified yet**: specialists run one at a time when `ANTIBODY_MODEL` is set, because a local Ollama is serial and parallel calls only queue inside the SuperLink. Run 3–5 scans in a row to confirm. Override with `ANTIBODY_PARALLEL`.
