---
tags: [agentapp]
dataset: []
framework: []
---

# Antibody: an immune system for buildings

14 AI agents each hunt one kind of building failure: battery bank (PWR), switchgear
(ELEC), wall wiring (WIRE), sprinkler riser (FIRE), concrete column (STR), elevator
(LIFT), rooftop chiller (HVAC), pipe leak (H2O), facade (ENV), air quality (AIR), the
building management system (CYBER), emergency generator (GEN), gas and CO (GAS) and
exits and fire doors (EGRESS). A coordinator ranks their reports by
risk x consequence x urgency, scores building health out of 100 and alerts the facility
manager.

**Human-supervised.** Agents fix small problems themselves in bounded loops (Tier 0-1) and
re-check the result; after two failed attempts, or when building health drops below 70,
they escalate. Consequential actions (shutting a valve, tripping a breaker, dispatching a
contractor, messaging tenants, any change to the building control system, turning on
24/7 monitoring) are held until a person approves them in the next message of the run
series.

**Governed.** Every agent's actions are recorded with
[Sentience Governor](https://github.com/crescerelabs/sentience-governor): each agent
declares its objective and scope, and Governor flags high-consequence actions and actions
outside an agent's lane. Approved actions carry who approved them. The Governor profiles
ship inside this app (`agent/governance_profiles/`), so hosted runs are governed too.

Sensor readings are simulated demo data (`agent/building_data.py`).

## Flower features used

| Feature | Where |
|---|---|
| AgentApp with 14 parallel specialist agents and a coordinator | `agent/agent_app.py` |
| Flower Runtime model (`FLWR_RUNTIME_BASE_URL`), or a local model via `ANTIBODY_MODEL` | `MODEL` |
| Built-in connectors `web_search`, `web_fetch` for safety standards (run config `investigate = true`) | `investigate()` |
| Account connectors `slack`, `notion` for tenant reports and work orders, when bound to the run | `investigate()` |
| `start_automation` for round-the-clock rescans when the manager asks | `investigate()` |
| Run-series state (`context.state`) for health trend, held approvals and follow-up memory | `load_memory()`, `save_memory()` |
| Structured run events (`antibody.*`) for the web console in `../antibody-web` | `main()` |
| Flower run config (`[tool.flwr.app.config]`) for scan settings on SuperGrid |  `configure()` |
| Local SuperLink + Ollama for a fully on-premises run | see `../antibody-web/README.md` |

## Files

| File | What it holds |
|---|---|
| `agent/agent_app.py` | The swarm: decide held approvals, sense and act (agents in parallel), rank, investigate, alert (coordinator) |
| `agent/actions.py` | Every agent's tools, their simulated effects and re-checks, the fixed rules (health < 70, 2 attempts, +/-15%) and approvals |
| `agent/specialists.py` | One job description per agent. Add an entry, its readings and its actions to add a building system |
| `agent/building_data.py` | The demo building and its sensor snapshot |
| `agent/governance.py` | Sentience Governor record per agent |
| `agent/building_domain.py` | Operation type and tier for every action (one row each), shared by the Governor record and the approval gate |
| `tests/test_actions.py` | Tier rules, approvals and two-message scans with a fake model: `python -m unittest discover -s tests -v` |

## Run

```shell
uv sync
uv run flwr build
uv run flwr login supergrid
uv run flwr chat
```

In the chat:

```
/load .
Check the building.
```

Paste JSON to change a reading and rescan, for example:

```
The leak got worse. {"H2O": {"night_flow_lpm_building_empty": 14}}
```
