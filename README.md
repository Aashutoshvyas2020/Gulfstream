# Antibody

An immune system for buildings, built on [Flower](https://flower.ai/docs/agent/). 11 AI agents each hunt one kind of building failure, and a coordinator ranks what to fix first, looks up the governing safety standard and alerts the facility manager. It runs on a local Flower SuperLink with a local model, so building data can stay on site.

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
  agent/specialists.py  the 11 agents' job descriptions and consequence weights
  agent/building_data.py simulated demo building ("Harbor Point Tower")
antibody-web/           web backend + operator console
  server.py             FastAPI: starts real Flower runs, relays run events over SSE
  static/index.html     operator console (served at / and /console)
```

## Quick start (local, no SuperGrid access needed)

Needs [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com) with a model (tested with `gemma4`), and Python 3.11+.

```powershell
cd agent
uv sync
```

Then follow [`antibody-web/README.md`](antibody-web/README.md): three terminals for Ollama, the local SuperLink and the console. Open http://127.0.0.1:8600 and press **Scan building**. A scan takes about 3–4 minutes on a laptop GPU.

Terminal only, without the web console:

```powershell
cd agent
$env:PYTHONUTF8 = "1"; $env:FLWR_CHAT_SUPERLINK = "local-agent"
uv run flwr chat        # then: /load .   and   Check the building.
```

## How a scan works

1. **Sense.** The 11 specialists each read their system and return a JSON report (risk 0–1, confidence, finding, evidence, time to failure, fix).
2. **Rank.** Plain code scores `risk × consequence × urgency` and computes building health out of 100.
3. **Investigate.** The coordinator calls Flower connectors: `web_search` / `web_fetch` for the safety standard, `slack` / `notion` for tenant reports and work orders (when bound), `start_automation` for "Watch 24/7".
4. **Alert.** The alert streams to the console or `flwr chat`.
5. **Remember.** Health history and the last exchange are kept in Flower run-series state (`context.state`), so the next scan reports the trend.

The agent emits `antibody.scan.started`, `antibody.agent.report`, `antibody.scan.ranked` and `antibody.tool` run events. `server.py` relays them to the browser. This event contract is the boundary between the three lanes below.

## Three lanes (one owner each)

| Lane | Owns | Starts from |
| :-- | :-- | :-- |
| **1. Agents** | `agent/` | Tier 0–3 actions: add `<verb>_<object>` tools per agent (e.g. `reset_damper`, `shut_valve`), fixed thresholds in code (health < 70 escalates, 2 retries), an approval gate for Tier 3 actions and `start_automation` |
| **2. Flower integration** | `antibody-web/server.py`, SuperLink / SuperGrid setup | Get SuperGrid access working; confirm the heartbeat fix; real network immunity with 2–3 buildings as SuperNodes using `agent.grid` (`get_nodes`, `push_messages`, `pull_messages`): lessons travel, readings never do |
| **3. Frontend** | `antibody-web/static/` | Tier 3 approval prompt, the "actions → auto-fixes → escalations → 1 human decision" funnel, a multi-building view for network immunity |

If you change the shape of a run event, change it in all three lanes in the same pull request.

## Status

| Works (tested locally) | Not tested yet |
| :-- | :-- |
| 11 agents + coordinator on a local SuperLink with Ollama | SuperGrid: account returns `Entitlement error ... Deployment Runtime is not allowed` |
| Ranking, health score, trend memory across scans | `web_search` locally: needs `TAVILY_API_KEY`, `BRAVE_API_KEY`, `EXA_API_KEY` or `FLWR_WEB_SEARCH_ENDPOINT` on the SuperLink |
| `start_automation` (SuperLink log shows `start-automation` 200) | Slack / Notion with real accounts (SuperGrid personal workspace only in flwr 1.39) |
| Web console streaming a full scan | `agent.grid` / SuperNodes |

## Known issue: "No heartbeat received from the task"

On the local SuperLink, some scans are killed 1–4 minutes in. Fixes so far: alert text is sent in 0.5 s batches (it was about 1,300 events per alert), and follow-ups use run-series state instead of `get_trace()`. The latest fix is **not verified yet**: specialists run one at a time when `ANTIBODY_MODEL` is set, because a local Ollama is serial and parallel calls only queue inside the SuperLink. Run 3–5 scans in a row to confirm. Override with `ANTIBODY_PARALLEL`.
