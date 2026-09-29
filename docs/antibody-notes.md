# Antibody: Flower and Sentience Governor notes

> 2026-09-29. **Antibody is a placeholder name.**
>
> This file covers:
> - the human-supervision model;
> - how Antibody maps onto Flower;
> - what Sentience Governor records;
> - claims to verify before the pitch.
>
> Governor profiles: [`../governance/`](../governance/).

## 1. The product in one line

- An immune system for buildings: **11 agents** each hunt one kind of failure, and **1 coordinator** decides what to fix first.
- It runs on Flower inside the building, so building data never leaves.
- Lessons spread across buildings without sharing data.

The agents: PWR, ELEC, WIRE, FIRE, STR, LIFT, HVAC, H2O, ENV, AIR, CYBER.

## 1a. Pipeline on Flower (team mapping)

| Step | Built with | What happens | Governor's record |
| :-- | :-- | :-- | :-- |
| **Sense** | **Flower AgentApp** | The 11 agents check their systems in parallel, each on its own sensor data | Reads within each agent's declared scope; Tier 0/1 fixes |
| **Rank** | **Deterministic costing** (plain Python, no LLM) | Each problem is scored by risk × consequence × urgency and costed; the building health score is computed out of 100 | Not a tool call. If health falls below 70, the escalation (Tier 2) is recorded |
| **Investigate** | **Automation** | The top problems are investigated automatically via connectors: `web_search` / `web_fetch` for the safety rule, read-only Slack for tenant complaints, read-only Notion for existing work orders | Each connector call is recorded. Connector content is untrusted data |
| **Alert** | *(to confirm)* | What is wrong, the evidence, who to call, the fix and the deadline | Tier 3 actions it proposes are flagged, and the record shows the human approval |
| **Remember** | **SuperLink** | The health trend and past alerts persist in `context.state` across the run series on the SuperLink. `start_automation` schedules scans every 10 minutes | `start_automation` is Tier 3: the record shows who turned on 24/7 scanning |

## 2. Human supervision: tiered autonomy (team decision)

People are brought in by **building-health escalation**. Small problems are fixed by the bots in self-correcting loops, and cleanups need no human at all.

| Tier | Who acts | Examples | What Governor records |
| :-- | :-- | :-- | :-- |
| **0: Cleanup** | Bots, no human | Clear stale alerts, close duplicate work orders, re-baseline a sensor after recalibration | In declared scope; nothing flagged |
| **1: Self-correction loop** | Bots, no human; the loop checks its own fix | Reset a stuck HVAC damper and re-read; retry an elevator levelling cycle; raise ventilation within set limits | The action and the re-check. **After N = 2 failed attempts the loop stops and escalates** instead of retrying |
| **2: Escalate** | The coordinator alerts a human | Health score below the threshold, a risk × consequence × urgency score over the line, or a failed Tier 1 loop | The escalation, its evidence and the health score at that moment |
| **3: Human approval** | Only after a person approves | Trip a breaker, shut a sprinkler or water valve, isolate a zone, take an elevator out of service, dispatch a contractor, message tenants, **any CYBER change**, `start_automation` ("Watch 24/7") | Flagged as high-consequence (profile), plus the recorded approval |

**Deterministic numbers**, fixed in code and not left to the LLM:
- escalation threshold: health **below 70** (placeholder);
- self-correction attempts: **N = 2**;
- autonomous setpoint limits per agent (e.g. ventilation ±15%).

**Copy rule for the pitch:**
- The tiers and the approval gate are Antibody's code.
- Governor **records and flags; it never blocks**.
- Say "Governor's record shows every Tier 3 action had a human approval." Never say "Governor prevented."

## 3. Sentience Governor integration

### One session per agent

- Twelve sessions in total, each with its own `agent_id`: `antibody-pwr`, …, `antibody-cyber`, `antibody-coordinator`.
- Profiles are bound in [`governance/resolution.yaml`](../governance/resolution.yaml), which needs Governor 0.3.2+.

| Profile | For | Key settings |
| :-- | :-- | :-- |
| [`agent.yaml`](../governance/profiles/agent.yaml) | The 10 system agents | Intent required before the first write; Tier 3 tools flagged as high-consequence |
| [`cyber.yaml`](../governance/profiles/cyber.yaml) | CYBER | Intent required from session start. **Audit-only**: every control-system change (`close_port`, `reset_password`, `change_firewall_rule`, …) is flagged |
| [`coordinator.yaml`](../governance/profiles/coordinator.yaml) | The coordinator | Flags dispatch, tenant messages, `start_automation`, `share_lesson` and approved-action execution |

All three validate: `sentience profile validate governance/profiles/<file>`.

### Lanes come from declarations, not profiles

- A profile cannot say "HVAC may only touch HVAC."
- Instead, at session start each agent **declares its intent and scope**:
  - intent: e.g. "watch rooftop chiller; Tier 0/1 fixes only";
  - scope: its own target system, e.g. `harbor-point/hvac`.
- Governor compares each action's target system with the declared scope.
- An agent acting on another system is recorded as outside its declared scope.

**Tool naming contract** (so the profile regexes match):
- tool id is `<verb>_<object>`, e.g. `reset_damper`, `shut_valve`;
- target system is `harbor-point/<area>`, e.g. `harbor-point/h2o`.
- The regexes match `<tool_id>:<target_system>`.

| Agent | Declared scope (target) | Tier 0/1 tools (in scope) |
| :-- | :-- | :-- |
| PWR | `harbor-point/pwr` | `clear_alert`, `rebaseline_sensor`, `adjust_ventilation` (battery room, within limits) |
| ELEC | `harbor-point/elec` | `clear_alert`, `rebaseline_sensor`, `request_thermal_rescan` |
| WIRE | `harbor-point/wire` | `clear_alert`, `rebaseline_sensor`, `request_arc_rescan` |
| FIRE | `harbor-point/fire` | `clear_alert`, `rebaseline_sensor`, `recheck_riser_pressure` |
| STR | `harbor-point/str` | `clear_alert`, `rebaseline_sensor`, `request_crack_remeasure` |
| LIFT | `harbor-point/lift` | `clear_alert`, `retry_levelling` |
| HVAC | `harbor-point/hvac` | `clear_alert`, `reset_damper`, `adjust_setpoint` (within limits) |
| H2O | `harbor-point/h2o` | `clear_alert`, `enable_night_flow_isolation_mode`, `rebaseline_sensor` |
| ENV | `harbor-point/env` | `clear_alert`, `request_facade_rescan` |
| AIR | `harbor-point/air` | `clear_alert`, `adjust_ventilation` (within limits) |
| CYBER | `harbor-point/bms` (read-only audit) | `clear_alert`, `run_audit` (no changes) |
| Coordinator | `harbor-point/*` (read), work orders | `close_duplicate_work_order`, `rank`, `escalate` |

### What Governor adds to the demo

1. **Every Tier 3 action has an approval on record.** The console shows each high-consequence flag next to the human decision.
2. **CYBER oversteps.** It finds an open port and calls `close_port` without approval. Governor flags it (cyber profile). The coordinator routes it to a human, and the record shows the attempt.
3. **The supervision funnel:** "Last scan: N agent actions → X autonomous fixes → Y escalations → 1 human decision." Counts come from the Governor records.
4. **Cost per agent:** Governor attributes tokens to each agent's turns, e.g. "a full scan costs X tokens; STR is most of it." This supports the pricing story.

### Honest limits (say them if asked)

- **Payloads.** Governor records **which tool was called on which system, not the contents**. So "building data never leaves" cannot be proven from Governor's record today. The app must check what crosses buildings, or tag outgoing message fields so Governor can flag sensitive data leaving (POL-005).
- **No Flower integration.** Governor needs a thin adapter around each agent's tool calls. If agents use Pydantic AI, `pydantic-ai-governor` captures tool calls with no adapter.
- **Records must reach the console.** Run a local SuperLink and SuperNodes so sessions write to local disk. The console is a terminal pane:
  - `sentience open --latest --summary` after each scan;
  - or `tail -f <trace> | sentience-cli`, which is unverified as a live stream.
- **Version.** Per-agent profile binding needs Governor **0.3.2+**. The CLI on the operator's Mac is 0.3.1.2 and must be upgraded:

  ```bash
  pipx upgrade sentience-governor
  ```

## 4. Flower: make the part Flower is essential for real

- **The risk.** Inside one building, 11 agents plus a coordinator could run in one process without Flower. Flower is **essential** for cross-building "network immunity," and today that is simulated (roadmap). Judges score "integration depth with Agents and SuperGrid."
- **Feasible fix (P0 if Coder 1's grid round trip works):**
  - Run **2–3 buildings as real SuperNodes** on a local SuperLink.
  - Each building's agents run on its node, and its data stays there.
  - The hub is the portfolio view.
- **Network immunity for real.** When one building beats a failure, its node sends a **lesson** to the hub: a detection rule or threshold, **never readings**. The hub pushes it to the other nodes with `push_messages`, and they apply it.
- **The "Play" moment becomes a real grid broadcast.** Keep the 7-building animation only if clearly labelled as illustrative.
- **Governor on the lesson.** `share_lesson` is high-consequence on the coordinator, so the record shows which lesson went out and who approved it.
- **Grid facts to respect:**
  - only the SuperLink AgentApp can initiate;
  - nodes reply with `push_messages` / `push_message_reply`, depending on the `flwr` version;
  - nodes never talk to each other.
- **Model.** Endeavor earns the judges' bonus. Consider Endeavor for the coordinator and a local Ollama model for the per-building agents, to keep the privacy story.

## 5. Claims to verify before saying them on stage

| Claim | Why check |
| :-- | :-- |
| ~39,000 US fires a year from electrical faults | Published figures differ widely by source and by residential vs all fires. Cite the exact source or drop the number |
| ~10% of US commercial buildings have building automation | Confirm the source and what it counts |
| NFPA 70B is "yearly" | The 2023 standard sets maintenance intervals by equipment condition, not a flat year |
| Florida condo inspections at 30 years | The rules changed after the original law; confirm the current trigger |
| "Competitors can't copy that" | Strong claim, not yet measured. Consider "compounds with every building" |
| TAM / SAM / SOM, $15,792/year savings | Present as the team's estimates, with the assumptions ready |
| "All 12 agents running on Flower" | True only for what runs in the demo; keep the real-vs-simulated table on the slide |

## 6. Demo beats with governance (about 2 minutes)

1. **Scan.** 11 agents run. Tier 0 cleanups and one Tier 1 damper reset happen silently, each re-checked.
2. **Inject "Leak spreads."**
   - Health drops below 70.
   - H2O's night-flow isolation mode is tried twice and fails, so it escalates (Tier 2).
   - The coordinator proposes `shut_valve` (Tier 3), and the human approves. The console shows the flag and the approval.
3. **CYBER oversteps.** It tries `close_port` unapproved, and Governor flags it.
4. **Watch 24/7.** `start_automation` needs approval, and the record shows who turned it on.
5. **Network immunity.** The lesson from this building is pushed over the grid to the other building nodes (real, if the grid works).
6. **The funnel.** Actions → autonomous fixes → escalations → **1 human decision**.
