# Continuity: Flower and Governor integration notes

> Integration notes for [continuity-spec.md](continuity-spec.md), 2026-09-29. They record decisions and open questions about Flower and Sentience Governor; they do not edit the spec. Flower facts: [flower-architecture.md](flower-architecture.md).

## Update (2026-09-29): spec Revision 1

- **Agents are now Personal, Flight and Traffic.** The Business Agent was removed; the Personal Agent holds meeting priorities. See spec §R1.
- **Layout 1 is adopted.**
  - The Personal Agent is the SuperLink AgentApp and the chat agent.
  - Flight and Traffic are SuperNode AgentApps.
  - Every arrow in spec §R4 is a direct grid message (hub → node → reply). Nothing is relayed.
- **Where this file still says so:**
  - "Business" in §1 now means **Traffic**, with Personal → Traffic ground-time queries.
  - In §2's high-consequence list, `reschedule_event` stays (the internal call). Add `book_ground_transport` (mock).
- **Arrival buffer flag (§3 item 4) is resolved.** The Traffic Agent now supplies ground time per airport, time and mode. The buffer rules are deterministic (spec §R2).
- **Carrier-name flag is still open.** The spec still uses UA212.

## 0. Team direction (2026-09-29, overrides spec §24 TUI)

- **The whole app is a chat interface.** No Rich or Textual TUI.
- **The Sentience Governor console runs in the background,** on a second screen or pane.
- **Flower is definite.**

What this implies:

- **Use Flower's own chat as the interface.**
  - That means Flower Chat in the terminal, or the flower.ai browser chat.
  - A conversation is a run series, and each user message starts a run.
  - It deepens "use of Flower" for the judges and removes a UI build.
- **The chat agent is the Personal Agent.**
  - The traveler talks to their own agent.
  - This makes Layout 1 (§1: Personal on the SuperLink) the natural fit.
- **Approval becomes a chat message.**
  - Run 1 ends with the recovery proposal, saved in `context.state`.
  - The traveler replies "approve" (run 2) to execute.
  - This is the run-boundary design below. It keeps each run inside the 5-minute limit.
- **The collaboration becomes visible in the chat.**
  - The Personal Agent emits events via `agent.events.emit()`, e.g. "→ Business: can the 16:30 call move?" and "← Business: movable; dinner critical."
  - These show up in the chat's run trace.
  - Mark messages relayed by the hub as relayed.
- **Disruption injection.** Treat it as a clearly labelled demo control, e.g. the presenter types `/disrupt BL212`.
  - The Personal Agent passes it to the Mobility node as a demo-control message.
  - Mobility flips its own `disruptions.json` state and answers the status check from its own data.
  - Alternative: set the scenario with a `run_config` override on the mobility node.
- **The Governor console is a terminal pane.** There is no live console in 0.3.2.1. What exists:
  - `sentience-cli` (full-fidelity NDJSON viewer; reads a file or stdin);
  - `sentience open --latest --summary`;
  - `sentience pulse`.
  - For a live view, try `tail -f <trace>.jsonl | sentience-cli`. **Unverified:** it isn't known whether `sentience-cli` renders a stream incrementally. The fallback is re-running `sentience open --latest --summary` after each run.
  - The console must be able to reach the records. Run the SuperLink and SuperNodes locally so sessions write to local disk.

## 1. Flower topology: the spec's arrows versus what the grid allows

**Grid facts** (flwr 1.38/1.39; see [flower-architecture.md](flower-architecture.md)):
- Flower Agent messaging is hub-and-spoke.
- Only the **SuperLink** AgentApp can initiate: `get_nodes`, `push_messages`, `pull_messages`.
- A **SuperNode** AgentApp can only reply to the hub (`push_messages`).
- SuperNodes never talk to each other.

The spec's demo arrows need mapping onto this:

| Spec arrow | Possible directly? |
| :-- | :-- |
| Mobility → Personal ("4 alternatives available"; Mobility initiates on disruption) | Only if Personal is the hub and Mobility's message is a **reply** to Personal's status request |
| Personal → Business | Only if Personal is the hub |
| Personal → Mobility (minimal constraints) | Only if Personal is the hub |

### Two layouts for the team to choose between

**Layout 1: the Personal Agent is the SuperLink AgentApp.** Mobility and Business run on SuperNodes.
- Every spec arrow is a real, direct grid message.
- Needs only 2 SuperNodes.
- Disruption flow:
  1. `disrupt UA212` sets the mobility node's state and starts a Personal run.
  2. Personal asks Mobility for trip status.
  3. Mobility replies with the `disruption_notice` and options.
- Framing: the traveler's agent convenes the federation.
- Cost: the traveler's private data sits on the hub machine. That's defensible, since the traveler's device is the hub, but say it.
- Fewest hops and the most build time left over. **Fits 3.5 hours.**

**Layout 2: a neutral Recovery coordinator on the SuperLink,** with 3 SuperNodes (matches the spec §18 diagram).
- The personal data never touches the hub.
- Every Personal↔Business and Personal↔Mobility exchange is **relayed** by the coordinator, so the event log must say "Personal → Business (via hub)."
- More hops and more code, but the strongest privacy story.

Either way, **label relayed messages honestly** in the event stream. Spec §31 says do not fake Flower communication.

### Human approval under the 5-minute task limit

Make approval a **run boundary**:
- **Run 1:** disruption → collaboration → `recovery_proposal`, saved in `context.state`.
- **Run 2:** prompt `approve`/`reject` → execution → summary.

This avoids blocking a run while waiting for a human. It also gives Flower's run history a clean "proposal → decision" pair.

### How the TUI gets its feed (superseded by §0: chat plus the Governor console)

The TUI sits outside the AgentApp processes. Options:
- the AgentApps call `agent.events.emit()` and the TUI reads the run trace (`get_trace` / `flwr` log);
- or the agents write to a local event file the TUI tails.

Choose in the first 30 minutes.

## 2. Sentience Governor: the spec does not mention it yet

Proposed role, in one line: **Continuity makes two promises; Governor's record is the evidence that each held, not the app's own say-so.**

### Promise 1: no consequential action without human approval (spec §12). Native to Governor today.

- Each agent session declares intent and scope. Profiles are bound per `agent_id` (`personal`, `mobility`, `business`) via `resolution.yaml`.
- `high_consequence.tools` covers `rebook_flight`, `charge`, `reschedule_event`, `cancel_commitment` and `send_message`.
- The record shows each of those calls, when it happened (run 2, after approval), and POL-001 if something mutated without a declaration.
- Candidate final-screen line, sourced from the Governor record: `Consequential actions before approval: 0`.
- **Copy rule:** the approval gate is Continuity's code. Governor records and flags; it never blocks. Never say Governor stopped or prevented anything.

### Promise 2: share constraints, not context (spec §32). Not native; needs a decision.

- Governor records tool **identity, target system and operation type, not arguments or payloads** (`ScopeAssertedPayload` in 0.3.2.1).
- So the headline metric `Private calendar events revealed to mobility provider: 0` **cannot be read from Governor's record today**.
- Options, from smallest up:
  - (a) **App-only.** Continuity computes the privacy trace (spec Stretch 4) from its own message log. Governor is not involved. Honest and quick.
  - (b) **Existing Governor path.** Continuity classifies each outgoing message's fields (`constraint`, `calendar_event`, `contact`, `medical`, `payment`) and supplies the classifications as a context snapshot. POL-005 (sensitive data escalating without authorization) then flags a sensitive class sent to Mobility. Needs a small integration; feasible.
  - (c) **Governor extension.** A declared disclosure allowlist per recipient, checked against push payload fields. Most insight, most time. Only if P0 is done early.

### Capture path

- If Pydantic AI runs inside the AgentApp, `pydantic-ai-governor` captures every tool call with no new capture code. Structured outputs and fallbacks (spec §20, §31) suit Pydantic AI anyway.
- Otherwise, use a thin adapter around the tool loop.
- Either way, check early that records can leave the executor process.

### Where Governor fits the P-list (proposal)

- **P1:** the Promise 1 record and the `Consequential actions before approval: 0` line.
- **P2:** privacy options (b) or (c).
- **Never on the P0 critical path.** Governor is fail-open and must not be able to break the demo.

## 3. Flags for the team

1. **Real carrier and flight number.**
   - The spec uses "United" and `UA212` with an invented maintenance cancellation.
   - Suggest a fictional carrier (e.g. "Bayline Air BL212") so the demo doesn't pin a fabricated incident on a real airline.
   - **Team's call.**
2. **Endeavor.** The judges give a bonus for it. Use Endeavor for the LLM tasks in spec §20. Keep Kimi or MiniMax as the fallback.
3. **Submission requirements are missing from P0.** Required:
   - a published **Flower Hub app**;
   - a GitHub repo;
   - a project description.
   - Suggest adding them as P0 items 14–15 so they aren't left until code freeze.
4. **Arrival buffer.** `must_arrive_by 17:30 ET` for an 18:00 dinner implies a 30-minute airport-to-venue transfer. Make it an explicit deterministic rule (dinner time minus the buffer from the profile), not an LLM judgment, so Options A and B classify the same way every run.

## 4. Day-of checks (first 30 minutes)

1. Can the account's federation use 2–3 SuperNodes, or do we use a local SuperLink plus SuperNodes?
2. Does a SuperNode AgentApp receive a hub message and reply within the same run?
3. Does Pydantic AI plus `pydantic-ai-governor` run inside an AgentApp against `FLWR_RUNTIME_BASE_URL`?
4. Can Governor records be exported from the executor process?
5. Is Endeavor available to the account?
