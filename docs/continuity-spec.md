# Continuity

> Team product specification for the Flower Collaborative Agent Hackathon @ Stanford (2026-09-29). Integration notes: [integration-notes.md](integration-notes.md).

## Hackathon Product Specification

**Event:** Collaborative Agent Hackathon @ Stanford  
**Core technology:** Flower / Flower Agent / SuperGrid  
**Build window:** ~3.5 hours of implementation, with code freeze around 4:45 PM for demo preparation  
**Prototype type:** CLI/TUI-first, open-source, multi-agent, human-supervised  
**Working product name:** Continuity

---

# Revision 1 (2026-09-29): three agents, with ground traffic at both ends

> **This revision overrides the sections below wherever they conflict.** The body is left intact for reference.
>
> **Summary:** the Business Agent is removed and a Traffic Agent is added. The three agents are **Personal**, **Flight** and **Traffic**. Every agent runs as its own Flower agent and appears in the multi-agent flow.

## R1. Agent lineup

| Agent | Represents | Knows (private to it) | Where it runs on Flower |
| :-- | :-- | :-- | :-- |
| **Personal Agent** | The traveler; **the chat agent** | Preference profile, **calendar and meeting priorities** (absorbed from the former Business Agent), budget, risk tolerance, current location, memory | SuperLink AgentApp (the hub; the traveler chats with it) |
| **Flight Agent** | The airline (spec's "Mobility Agent", flights only) | Reservation, flight status, disruptions, replacement options, fares, connections, departure and arrival times | SuperNode AgentApp |
| **Traffic Agent** | Ground transport, like a multimodal maps route | Door-to-door times by **rideshare/car**, **public transit** and **rail** at a given departure time, both **to the departure airport** and **from the arrival airport** | SuperNode AgentApp |

- The Personal Agent now owns "which meetings can move": internal call movable (medium), client dinner fixed (critical), networking event movable (low).
- Moving the internal call is a mock calendar action included in the single approval.
- No agent receives another agent's raw data. Flight and Traffic receive only constraints and queries.

## R2. Why the Traffic Agent matters: it changes the answer

The Flight Agent sees **airport arrival**. The traveler cares about **arriving at the dinner**.

Ground time differs by airport, by time of day and by mode. So a flight that lands earlier can get the traveler to the dinner later. Only the Traffic Agent knows this. Only the Personal Agent knows where the traveler must be, and by when.

**Traffic data** (deterministic mock, `data/traffic/routes.json`). The traveler is at Stanford at 07:00 PT.

| Leg | Depart | Rideshare / car | Public transit | Rail |
| :-- | :-- | :-- | :-- | :-- |
| Stanford → SFO | 07:00 PT | **35 min** | 70 min (Caltrain + BART) | n/a |
| Stanford → SJC | 07:00 PT | **25 min** | 55 min (Caltrain + bus) | n/a |
| EWR → Midtown | 17:15 ET | 45 min (rush hour) | n/a | **35 min** (AirTrain + NJ rail) |
| JFK → Midtown | 16:55 ET | 80 min (rush hour) | 65 min (AirTrain + subway) | 65 min (AirTrain + LIRR) |
| JFK → Midtown | 17:45 ET | 90 min | 70 min | 70 min |
| JFK → Midtown | 18:20 ET | 85 min | 70 min | 70 min |

**Rules** (deterministic Python, not the LLM):
- Deplaning to curb: 15 minutes.
- Airport check-in and security before departure: 45 minutes.
- "At the dinner" target: **17:50 ET** (18:00 minus 10 minutes of slack for low risk tolerance).

## R3. Options re-evaluated with ground time at both ends

Departure times are added so the origin end can be checked.

| Option | Flight | Origin end (from Stanford 07:00) | Destination end | At dinner by | Result |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **A** | SFO 08:40 → ORD → JFK 18:05, $0 | Rideshare at SFO 07:35; needs 07:55 ✓ | Already after 18:00 at the airport | ~19:30 | **REJECT**: misses the critical dinner |
| **B** | SFO 08:50 → **EWR 17:00** nonstop, +$165 | Rideshare at SFO 07:35; needs 08:05 ✓ (transit 08:10 ✗) | Curb 17:15 + **rail 35** = **17:50** (rideshare would be 18:00) | **17:50** | **RECOMMEND**: rideshare to SFO, rail into Midtown |
| **C** | SJC 08:10 → JFK 16:40 nonstop, +$95 | Rideshare at SJC 07:25; needs 07:25 ✓ (zero slack) | Curb 16:55 + best mode 65 = **18:00** | 18:00 | **REJECT**: no slack at either end; avoid-SJC preference |
| **D** | SFO 08:30 → DEN → JFK 17:30, +$45 | Rideshare at SFO 07:35; needs 07:45 ✓ | Curb 17:45 + 70 = 18:55 | 18:55 | **REJECT**: 47-minute connection is under the 50-minute minimum; also late |

**The demo moment:** Option C *lands 20 minutes earlier than B*, at the traveler's preferred airport. A flight-only system would rank it first. The Traffic Agent's rush-hour data at JFK rejects it, and its rail option from EWR is what makes B work. **No single agent could reach B.**

## R4. Collaborative workflow (replaces §9 steps 3–6)

1. `/disrupt UA212` (demo control). The Personal Agent asks the Flight Agent for trip status.
2. **Flight → Personal:** cancelled, with options A–D, structured and deterministic.
3. **Personal → Traffic:** the origin legs "Stanford → SFO, Stanford → SJC, departing 07:00 PT" and the destination legs "EWR → Midtown at 17:15; JFK → Midtown at 16:55, 17:45 and 18:20."
   - It sends **locations and times only**. No dinner, no client, no calendar.
4. **Traffic → Personal:** minutes per leg and mode, from its own data.
5. **Personal (private):** applies the calendar (dinner is fixed; the internal call can move) and the preferences. It runs the deterministic checks in R3.
6. **Personal → Flight:** minimal constraints plus a request to hold Option B.
7. **Chat → traveler:** one consolidated proposal.
   - Rebook B (+$165).
   - Rideshare to SFO (mock).
   - Rail into Midtown (mock).
   - Move the internal call to tomorrow 19:00 ET.
   - Prompt: *Approve? (reply "approve")*
8. **Traveler replies "approve"**, which starts the next run and executes the mock state changes.

## R5. Updated metrics and privacy line

```text
Critical commitments lost: 0
Human approvals: 1
Agents consulted: Flight, Traffic
Private calendar events revealed to Flight or Traffic agents: 0
```

## R6. Updated P0 (replaces §28 items 2, 6, 7)

- 2. Three agents exist: **Personal (hub/chat), Flight, Traffic**, each a separate Flower agent.
- 6. The Personal Agent queries the Traffic Agent for **both ends** of each option.
- 7. The Traffic Agent responds from its own data. Option C is rejected on destination ground time.

**Also P0:**
- the chat interface (Flower Chat);
- a published Flower Hub app;
- the GitHub repo.

## R7. Out of scope (unchanged spirit)

- Traffic is mock data shaped like a multimodal maps result.
- No live Google Maps, rideshare or rail APIs, and no real bookings of rides or trains.
- This is consistent with §26 and §30.

---

# 1. One-Sentence Product

**Continuity is a federated recovery system where a mobility provider agent, a private personal agent, and a business agent collaborate to repair a disrupted trip around the traveler’s real-life constraints without any one party gaining access to all of the traveler’s private context.**

---

# 2. The Problem

Airlines, rail operators, transit agencies, employers, calendars, and travelers all possess different pieces of information needed to recover from a disruption.

Today, these systems are disconnected.

When a flight is delayed or cancelled:

- the airline knows schedules, availability, fares, and operational constraints;
- the traveler knows personal preferences, commitments, health/logistical needs, willingness to spend, loyalty benefits, and risk tolerance;
- the employer or office knows which meetings are critical, which can move, and what downstream business obligations exist;
- the traveler becomes the integration layer between all of them.

Existing airline operations-research systems are sophisticated at recovering the **airline’s operation**. They can reposition aircraft, crews, passengers, and gates.

They do not optimize the traveler’s entire life.

A rebooking system may see two replacement flights as equivalent even when:

- one destroys a critical client meeting;
- one lands at an airport the traveler strongly avoids;
- one creates an unacceptable short connection;
- one eliminates lounge access the traveler values;
- one costs slightly more but preserves the entire purpose of the trip;
- one requires rescheduling an internal meeting that could easily move;
- one conflicts with a personal constraint unknown to the airline.

The underlying problem is therefore not simply:

> “Find another flight.”

It is:

> **Repair the traveler’s complete downstream plan when one part of the transportation system breaks.**

---

# 3. Product Thesis

Infrastructure providers optimize their own systems.

Continuity optimizes the **person moving across those systems**.

The system does this without centralizing all private information.

The airline should not receive the traveler’s entire calendar.

The employer should not receive the traveler’s personal preference profile.

The personal agent should not need unrestricted access to the airline’s operational systems.

Each agent owns a different information boundary and reveals only the minimum necessary information required to collaboratively produce a recovery plan.

---

# 4. Why This Requires Multiple Agents

This product must not be implementable as “one LLM with a large prompt.”

Each agent should possess information that the others do not possess and should not automatically receive.

The final recovery decision must require collaboration among those separate information domains.

## Agent 1: Mobility Agent

Represents an airline or broader transportation provider.

Knows:

- original reservation;
- flight status;
- cancellation/delay state;
- available replacement flights;
- departure and arrival airports;
- departure and arrival times;
- connection duration;
- seat availability;
- incremental fare;
- basic airport metadata;
- possible rerouting options.

Does **not** know:

- complete personal calendar;
- detailed personal preferences;
- private work obligations;
- private health information;
- personal contacts;
- full business schedule.

---

## Agent 2: Personal Agent

Represents the traveler.

Knows:

- preferred airports;
- preference for nonstop vs. connecting travel;
- willingness to pay;
- connection tolerance;
- seat preference;
- loyalty / lounge benefits;
- personal calendar;
- travel risk tolerance;
- relevant personal constraints;
- learned preference history;
- current trip priorities.

The Personal Agent acts as the traveler’s private decision layer.

It may reveal constraints to other agents, but it should not blindly disclose its entire memory or calendar.

Example disclosure:

```text
Must arrive before 5:30 PM ET.
Nonstop strongly preferred.
EWR is acceptable.
Additional cost up to $200 is acceptable.
Avoid alternate Bay Area departure airports.
```

This is preferable to sending:

```text
Here is the traveler’s entire calendar, personal profile, credit-card list,
travel history, contacts, and private notes.
```

---

## Agent 3: Business Agent

Represents the traveler’s office, employer, secretary, or business scheduling system.

Knows:

- business calendar;
- meeting importance;
- internal vs. external meetings;
- attendee availability;
- whether meetings may move;
- downstream business commitments;
- communication targets.

Does **not** know:

- full personal preference profile;
- full airline reservation system;
- unrelated personal calendar data.

The Business Agent answers questions such as:

```text
Is the 4:30 PM internal call movable?
Is the 6:00 PM client dinner critical?
What alternate times are available?
Who needs to be notified?
```

---

# 5. The Core Insight

The demo should prove:

> **No single agent possesses enough information to select the best recovery plan.**

The Mobility Agent can generate feasible replacement options.

The Personal Agent can determine which options fit the traveler’s preferences and private constraints.

The Business Agent can determine which work obligations are movable and which must be preserved.

Only after these agents exchange limited, purpose-specific information can the system create a high-quality recovery plan.

---

# 6. Primary Demo Scenario

The prototype should have one highly polished scenario.

Do not build many mediocre scenarios.

## Traveler

Demo traveler: Aashu or a fictional business traveler.

Original itinerary:

```text
SFO → JFK
United nonstop
Departure: 8:00 AM PT
Arrival: 4:25 PM ET
```

---

## Personal Preference Profile

Example profile:

```yaml
preferred_departure_airports:
  - SFO

acceptable_arrival_airports:
  - JFK
  - EWR

nonstop_preference: strong

max_extra_cost_usd: 200

minimum_connection_minutes: 50

alternate_bay_area_airports:
  avoid:
    - SJC
    - OAK

seat_preference: window

lounge_preferences:
  - centurion_lounge

credit_cards:
  - amex_platinum

travel_risk_tolerance: low

trip_priority:
  preserve_critical_business_commitments: true
```

Optional personal constraint:

```yaml
medication:
  time: "14:00 PT"
  tolerance_minutes: 30
```

Only include this if it genuinely affects the scenario. Do not add complexity merely because it exists.

---

## Business Schedule

```text
4:30 PM ET
Internal team call
Priority: MEDIUM
Movable: YES

6:00 PM ET
Client dinner
Priority: CRITICAL
Movable: NO unless no viable recovery exists

7:30 PM ET
Networking event
Priority: LOW
Movable: YES
```

---

# 7. Negative Event

The system begins in a stable state.

Then the demo injects a disruption.

Command:

```bash
inject disruption UA212 cancellation
```

or:

```bash
disrupt UA212
```

Result:

```text
UA212
SFO → JFK
CANCELLED
Reason: Aircraft maintenance
```

This is the trigger for collaboration.

---

# 8. Replacement Options

The Mobility Agent returns deterministic mock options.

Example:

## Option A

```text
SFO → ORD → JFK
Arrival: 6:05 PM ET
Incremental cost: $0
Connection: 52 minutes
```

## Option B

```text
SFO → EWR
Nonstop
Arrival: 5:00 PM ET
Incremental cost: $165
```

## Option C

```text
SJC → JFK
Nonstop
Arrival: 4:40 PM ET
Incremental cost: $95
Requires ground transfer from current location to SJC
```

## Option D

```text
SFO → DEN → JFK
Arrival: 5:30 PM ET
Incremental cost: $45
Connection: 47 minutes
```

These options should be stored as deterministic structured data.

The LLM should not invent flight times or prices during the demo.

---

# 9. Collaborative Workflow

## Step 1: Disruption Detection

Mobility Agent detects:

```text
UA212 CANCELLED
```

The Mobility Agent determines the original itinerary is no longer feasible.

---

## Step 2: Mobility Agent Generates Alternatives

Mobility Agent searches its local dataset and identifies Options A-D.

It does not choose the final option.

It sends a structured request to the Personal Agent:

```json
{
  "type": "recovery_request",
  "trip_id": "trip_001",
  "reason": "flight_cancelled",
  "alternatives": ["A", "B", "C", "D"],
  "requested_constraints": [
    "latest_acceptable_arrival",
    "airport_flexibility",
    "maximum_extra_cost",
    "connection_tolerance"
  ]
}
```

---

## Step 3: Personal Agent Determines Relevant Constraints

The Personal Agent examines:

- preference profile;
- private calendar;
- trip priorities.

It identifies that the 6:00 PM client dinner is potentially threatened.

However, it does not independently know whether that business commitment can move.

Therefore it queries the Business Agent.

---

## Step 4: Personal Agent Queries Business Agent

Request:

```json
{
  "type": "schedule_constraint_query",
  "events": [
    "internal_call_1630",
    "client_dinner_1800"
  ],
  "question": "Which commitments can move if travel recovery requires it?"
}
```

---

## Step 5: Business Agent Responds

Example:

```json
{
  "internal_call_1630": {
    "priority": "medium",
    "movable": true,
    "alternate_time": "tomorrow 19:00 ET"
  },
  "client_dinner_1800": {
    "priority": "critical",
    "movable": false
  }
}
```

The Personal Agent learns that preserving the client dinner should dominate preserving the internal call.

---

## Step 6: Personal Agent Shares Minimal Constraints

The Personal Agent sends only relevant constraints to the Mobility Agent.

Example:

```json
{
  "must_arrive_by": "17:30 ET",
  "nonstop_preference": "strong",
  "arrival_airports": ["JFK", "EWR"],
  "max_extra_cost_usd": 200,
  "avoid_departure_airports": ["SJC", "OAK"],
  "connection_risk_tolerance": "low"
}
```

Notice what is missing:

- calendar event names;
- client identity;
- employer;
- personal contacts;
- entire travel profile;
- unrelated preferences.

This privacy boundary should be explicitly mentioned in the demo.

---

# 10. Option Evaluation

Use deterministic checks first.

## Option A

```text
Arrival: 6:05 PM
Critical commitment begins: 6:00 PM
Result: REJECT
```

Reason:

```text
Fails hard arrival constraint.
```

---

## Option B

```text
Arrival: 5:00 PM
Nonstop: YES
Departure airport: SFO
Arrival airport: EWR
Extra cost: $165
```

Result:

```text
VALID
```

---

## Option C

```text
Arrival: 4:40 PM
Nonstop: YES
Departure airport: SJC
Extra cost: $95
```

Result:

```text
VALID BUT DISFAVORED
```

Reason:

```text
Requires alternate Bay Area airport and additional ground-transfer risk.
```

---

## Option D

```text
Arrival: 5:30 PM
Connection: 47 minutes
Traveler minimum: 50 minutes
```

Result:

```text
REJECT
```

Reason:

```text
Fails minimum connection threshold.
```

---

# 11. Recommended Recovery

The system converges on Option B.

Example output:

```text
RECOMMENDED RECOVERY

Rebook:
SFO → EWR
Nonstop
Arrival: 5:00 PM ET
Incremental cost: $165

Business adjustment:
Move 4:30 PM internal team call
→ Tomorrow, 7:00 PM ET

Preserved:
✓ Critical 6:00 PM client dinner
✓ Nonstop preference
✓ SFO departure
✓ Cost threshold
✓ Connection-risk preference

Tradeoff:
JFK replaced with EWR
```

---

# 12. Human Supervision Model

The system should operate autonomously up to the point where a consequential external action would occur.

Agents may autonomously:

- detect disruption;
- request information from other agents;
- generate alternatives;
- filter impossible options;
- rank feasible options;
- simulate downstream consequences;
- propose schedule changes;
- draft communications;
- update temporary reasoning state.

Agents should **not** autonomously execute high-impact irreversible actions without approval.

Require approval before:

- rebooking a flight;
- spending money;
- cancelling a commitment;
- sending an external message;
- changing a major calendar event.

The demo should present one consolidated approval:

```text
Approve recovery plan? [Y/N]
```

This reduces the human’s burden from many manual tasks to one informed decision.

---

# 13. Execution After Approval

For the hackathon, execution should update mock state.

Do not integrate actual airline purchasing.

After approval:

```text
✓ Replacement flight confirmed
✓ Internal call rescheduled
✓ Calendar updated
✓ Notification drafted
```

Optionally add:

```text
Send update to internal attendees? [Y/N]
```

If time is tight, keep this simulated.

---

# 14. Human Value Proposition

Before Continuity:

```text
Flight cancelled

Traveler:
1. Open airline app
2. Search replacement flights
3. Compare airports
4. Check calendar
5. Determine which meetings matter
6. Message office
7. Reschedule meeting
8. Rebook flight
9. Check ground transportation
10. Notify attendees
```

After Continuity:

```text
Flight cancelled

Continuity:
1. Detects disruption
2. Agents collaborate
3. Produces complete recovery plan

Traveler:
Approve? [Y]
```

The human remains in control while the coordination burden disappears.

---

# 15. Demo Success Metric

Use a simple before/after summary.

Example:

```text
ORIGINAL DISRUPTION

Flight cancelled
Potential commitments affected: 3
Manual systems involved: 4
Expected arrival under default rebooking: 6:05 PM


AFTER CONTINUITY

Critical commitments lost: 0
Manual systems handled by traveler: 0
Human approvals required: 1
Extra cost: $165
Private calendar events revealed to mobility provider: 0
```

The strongest metric is:

```text
Private calendar events revealed to mobility provider: 0
```

It reinforces why federated collaboration matters.

---

# 16. Self-Improvement / Preference Learning

This should be intentionally small.

Do not build a complex training system.

Use a Markdown or structured memory file.

Example:

`personal_memory.md`

```markdown
# Learned Travel Preferences

## Observation 001

Context:
A critical business commitment was threatened by a flight cancellation.

Decision:
Traveler approved a $165 more expensive nonstop flight into EWR instead
of taking a cheaper connecting itinerary to JFK.

Inferred preference:
When an important commitment is at risk, traveler values schedule
reliability and nonstop travel over moderate cost savings.

Confidence:
Medium
```

---

# 17. Explicit Preference Correction

Optional second mini-demo:

Agent says:

```text
Recommended EWR because it preserves the meeting.
```

Traveler says:

```text
Next time, prefer JFK if it arrives within 30 minutes of the EWR option.
```

Memory becomes:

```markdown
## Airport Preference

Prefer JFK over EWR when the JFK itinerary arrives no more than
30 minutes later.

Source:
Explicit traveler correction.

Confidence:
High
```

This demonstrates that the Personal Agent evolves over time.

---

# 18. Technical Architecture

High-level:

```text
                   FLOWER FEDERATION
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼

  MOBILITY NODE      PERSONAL NODE      BUSINESS NODE

  airline.json       profile.yaml       business.json
  flights.json       calendar.json      meetings.json
  disruptions.json   memory.md          contacts.json

        │                │                │
        └────────────────┼────────────────┘
                         │
                         ▼
                  RECOVERY WORKFLOW
                         │
                         ▼
                   HUMAN APPROVAL
```

Each node owns separate data.

The prototype should preserve this separation even if all nodes ultimately run on one laptop for demo convenience.

---

# 19. Flower's Role

Flower must be structurally important.

Flower should handle communication among the agent nodes.

Use Flower messaging / AgentApp primitives for:

- requesting mobility alternatives;
- querying business constraints;
- exchanging limited traveler requirements;
- returning structured responses;
- coordinating the recovery workflow.

Do not simply call three Python functions and mention Flower in the README.

The demo should visibly show that separate Flower agents/nodes are communicating.

---

# 20. LLM vs Deterministic Code

Use LLM reasoning only where semantic interpretation is useful.

## Deterministic Logic

Use normal Python for:

- time comparisons;
- cost thresholds;
- connection durations;
- arrival constraints;
- airport allow/deny lists;
- flight feasibility;
- itinerary validation;
- option sorting;
- state updates.

## LLM Reasoning

Use the model for:

- interpreting vague preferences;
- identifying which constraints matter;
- explaining tradeoffs;
- deciding what minimum information should be disclosed;
- summarizing the recovery recommendation;
- learning preferences from explicit human feedback.

Do not ask the LLM to calculate basic times if Python can calculate them exactly.

---

# 21. Data Layout

Suggested repository:

```text
continuity/
├── README.md
├── pyproject.toml
├── continuity/
│   ├── __init__.py
│   ├── app.py
│   │
│   ├── agents/
│   │   ├── mobility.py
│   │   ├── personal.py
│   │   └── business.py
│   │
│   ├── core/
│   │   ├── models.py
│   │   ├── scoring.py
│   │   ├── constraints.py
│   │   ├── messages.py
│   │   └── recovery.py
│   │
│   ├── tui/
│   │   └── app.py
│   │
│   └── data/
│       ├── mobility/
│       │   ├── reservation.json
│       │   ├── flights.json
│       │   └── disruptions.json
│       │
│       ├── personal/
│       │   ├── profile.yaml
│       │   ├── calendar.json
│       │   └── memory.md
│       │
│       └── business/
│           ├── meetings.json
│           └── contacts.json
│
└── tests/
    ├── test_constraints.py
    ├── test_scoring.py
    └── test_recovery.py
```

---

# 22. Message Schema

Keep messages structured.

Example envelope:

```json
{
  "message_id": "msg_001",
  "sender": "mobility",
  "recipient": "personal",
  "type": "recovery_request",
  "trip_id": "trip_001",
  "payload": {}
}
```

Useful message types:

```text
disruption_notice
recovery_request
constraint_request
constraint_response
schedule_constraint_query
schedule_constraint_response
candidate_options
candidate_evaluation
recovery_proposal
approval_request
approval_response
execution_update
preference_feedback
```

Do not create dozens of message types.

---

# 23. Scoring Model

Use hard constraints first.

Example:

```python
if arrival_time > required_arrival:
    reject()

if extra_cost > max_extra_cost:
    reject()

if connection_minutes < minimum_connection:
    reject()
```

Then score soft preferences.

Example:

```text
+40 nonstop
+25 preferred departure airport
+20 preferred arrival airport
+15 lounge-compatible airport
-20 alternate Bay Area departure
-15 connection
-10 each additional $100
```

The exact weights are not important.

The important thing is that:

1. hard constraints are deterministic;
2. soft preferences produce explainable tradeoffs.

---

# 24. TUI / CLI Interface

Do not build a web frontend unless the core system is completely finished.

Preferred tools:

- Rich; or
- Textual.

Suggested screen:

```text
┌──────────────────────────────────────────────────────────────────────┐
│ CONTINUITY                                  INCIDENT #001      ● LIVE│
├──────────────────┬───────────────────────────────────────────────────┤
│ AGENTS           │ EVENT STREAM                                      │
│                  │                                                   │
│ ● Mobility       │ 10:42 UA212 cancelled                            │
│ ● Personal       │ 10:42 Mobility requested constraints             │
│ ● Business       │ 10:43 Personal queried business schedule         │
│                  │ 10:43 Client dinner marked CRITICAL              │
│                  │ 10:43 Option B ranked highest                    │
│                  │                                                   │
├──────────────────┴───────────────────────────────────────────────────┤
│ RECOMMENDED RECOVERY                                                 │
│                                                                      │
│ SFO → EWR | Nonstop | +$165 | Arrive 5:00 PM                        │
│ Move internal call → tomorrow 7:00 PM                               │
│                                                                      │
│ Preserved: Client dinner                                             │
│ Privacy: 0 calendar events shared with airline                       │
│                                                                      │
│                  [A] APPROVE       [R] REJECT                        │
└──────────────────────────────────────────────────────────────────────┘
```

The UI exists to expose the collaboration.

It is not the primary technical achievement.

---

# 25. Demo Script

## Opening

Say:

> When your flight is cancelled, the airline knows its schedule but not your life. Your calendar knows your life but not the airline's inventory. Your employer knows which commitments matter but shouldn't have access to your personal data. Continuity lets those agents collaborate without making any one of them omniscient.

---

## Show Stable State

```text
Trip healthy.
SFO → JFK
Arrival: 4:25 PM
Critical commitment: 6:00 PM
```

---

## Inject Disruption

```bash
disrupt UA212
```

Screen:

```text
FLIGHT CANCELLED
```

---

## Show Agent Collaboration

Let the event log visibly update:

```text
Mobility → Personal:
4 alternatives available.

Personal → Business:
Can 4:30 PM meeting move?
Is 6 PM client dinner movable?

Business → Personal:
4:30 PM movable.
6 PM critical.

Personal → Mobility:
Need arrival before 5:30 PM.
EWR acceptable.
Max extra cost $200.
Nonstop strongly preferred.

Mobility:
Option B satisfies constraints.
```

---

## Human Approval

Display:

```text
SFO → EWR
Nonstop
Arrival 5:00 PM
+$165

Move internal call to tomorrow.

Approve? [Y/N]
```

Press `Y`.

---

## Execution

```text
✓ Flight recovered
✓ Meeting rescheduled
✓ Calendar updated
✓ Draft notification prepared
```

---

## Final Screen

```text
RECOVERY COMPLETE

Critical commitments lost: 0
Human approvals: 1
Private calendar events sent to airline: 0
```

Pause here.

This is the demo endpoint.

---

# 26. What Not to Build

Strictly out of scope:

- real airline purchasing;
- real airline APIs;
- Google Calendar OAuth;
- Gmail integration;
- hotel booking;
- Uber integration;
- train APIs;
- weather APIs;
- payment handling;
- maps;
- React dashboard;
- mobile app;
- authentication system;
- multi-user account system;
- complex long-term memory;
- reinforcement learning;
- full travel planner;
- actual enterprise integration.

These are future extensions, not hackathon requirements.

---

# 27. Stretch Goals

Only attempt after the primary demo works end-to-end.

## Stretch 1: Second Disruption

After approving the replacement flight:

```text
EWR ground connection delayed.
```

Continuity reruns recovery.

---

## Stretch 2: Learned Preference

User corrects an airport preference.

Next recovery visibly uses that preference.

---

## Stretch 3: Additional Mobility Provider

Add a mock rail agent.

Example:

```text
Flight arrival changed.
Train connection now impossible.
Rail Agent offers alternate departure.
```

This proves the architecture generalizes beyond airlines.

---

## Stretch 4: Privacy Trace

Show exactly what every agent received.

Example:

```text
MOBILITY AGENT RECEIVED

✓ latest acceptable arrival
✓ maximum extra cost
✓ airport flexibility

✗ calendar titles
✗ business contacts
✗ full preference profile
✗ medication details
```

This is an excellent demo feature if easy to implement.

---

# 28. Build Priority

## P0 — Must Work

1. Repository / Flower project runs.
2. Three agents exist.
3. Data is separated by agent.
4. Disruption can be injected.
5. Mobility alternatives load.
6. Personal Agent queries Business Agent.
7. Business Agent responds.
8. Personal constraints are sent to Mobility Agent.
9. Options are deterministically filtered.
10. Recovery recommendation appears.
11. Human approval works.
12. Mock state updates after approval.
13. Entire flow works live without manual code editing.

---

## P1 — Strongly Desired

14. TUI event stream.
15. Privacy disclosure summary.
16. Recovery metrics.
17. Clear explanation of why each option was rejected.
18. Preference memory update.

---

## P2 — Only If Everything Else Works

19. Second disruption.
20. Rail agent.
21. More advanced self-improvement.
22. Better animations / styling.
23. Additional user profiles.

---

# 29. Suggested Implementation Schedule

Assuming approximately 3.5 hours of build time.

## First 30 Minutes

- create repo;
- validate Flower;
- define shared data models;
- create deterministic demo datasets;
- define agent message schema.

Goal:

```text
Agents can send structured messages.
```

---

## 30–90 Minutes

Implement:

- Mobility Agent;
- Personal Agent;
- Business Agent;
- disruption event;
- option evaluation;
- recovery workflow.

Goal:

```text
Full workflow works with plain terminal logging.
```

---

## 90–150 Minutes

Implement:

- human approval;
- mock execution;
- privacy boundary checks;
- preference memory;
- tests.

Goal:

```text
End-to-end demo is reliable.
```

---

## 150–195 Minutes

Add:

- Rich/Textual TUI;
- clean event stream;
- recovery card;
- privacy metrics;
- better output formatting.

Goal:

```text
Demo looks intentional.
```

---

## Final Time Before Code Freeze

Do not add architecture.

Only:

- rehearse;
- eliminate crashes;
- reduce latency;
- prepare deterministic fallback;
- improve copy;
- test from a fresh terminal;
- record backup demo video if permitted.

---

# 30. Demo Reliability Rules

The live demo must not depend on:

- live flight APIs;
- external scheduling APIs;
- arbitrary web search;
- unpredictable airline inventory;
- external payment systems.

All critical scenario data should be local and deterministic.

Remote model calls may enhance reasoning, but the core scenario should remain recoverable if model output varies.

Use structured output schemas wherever possible.

---

# 31. Fallback Mode

Have a deterministic fallback if an LLM response fails.

Example:

```python
if agent_response_invalid:
    use_demo_fallback_response()
```

The judges care more about a working collaborative system than watching the application crash because one JSON field was malformed.

Do not fake Flower communication.

The collaboration should remain real even if semantic output has a fallback.

---

# 32. Privacy Principle

Use this rule throughout the codebase:

> **Share constraints, not context.**

Bad:

```text
Here is my complete work calendar.
```

Good:

```text
Must arrive in Manhattan before 6 PM.
```

Bad:

```text
Here is my medical profile.
```

Good:

```text
Travel option must allow access to medication by 2:30 PM PT.
```

Bad:

```text
Here are all my credit cards.
```

Good:

```text
Centurion Lounge access is available at SFO and JFK.
```

This principle is one of the strongest reasons the system should be federated.

---

# 33. Product Expansion Vision

The hackathon MVP uses flights.

The architecture should eventually support:

```text
Personal Agent
     │
     ├── Airline Agent
     ├── Rail Agent
     ├── Transit Agent
     ├── Traffic Agent
     ├── Hotel Agent
     ├── Rideshare Agent
     ├── Employer Agent
     └── Event / Venue Agent
```

A disruption anywhere in the chain can trigger coordinated recovery.

Example:

```text
Flight delay
→ missed train
→ changed ground route
→ meeting impact
→ hotel check-in impact
→ downstream recovery
```

Continuity becomes a coordination layer across fragmented mobility and scheduling systems.

---

# 34. Long-Term Product Vision

Today, the traveler is the middleware.

```text
Traveler
├── airline app
├── calendar
├── email
├── employer
├── rail app
├── rideshare
├── hotel
└── reservations
```

Continuity replaces the traveler as the manual integration layer.

```text
                  PERSONAL AGENT
                        │
              FLOWER / FEDERATION
                        │
       ┌────────────────┼─────────────────┐
       │                │                 │
       ▼                ▼                 ▼
   Mobility          Business         Services
   Agents            Agents           Agents
```

The traveler remains the decision authority.

The agents perform the coordination.

---

# 35. Positioning

Do not describe the product as:

> AI travel planner.

Do not describe it as:

> Multi-agent flight rebooking.

Do not describe it as:

> AI secretary.

Preferred framing:

> **Continuity is a federated recovery layer for disruptions across a person's transportation and schedule.**

Or:

> **Infrastructure optimizes its own systems. Continuity lets those systems coordinate around the person moving through them.**

Or:

> **When travel breaks, your itinerary shouldn't be the only thing that gets rerouted.**

---

# 36. Judge-Level Explanation

If asked, “Why does this need agents?”

Answer:

> Because no participant has enough information to make the correct decision. The airline has inventory but not the traveler’s private priorities. The personal agent understands those priorities but cannot determine which business commitments are movable. The business agent understands work constraints but should not receive the traveler’s private travel profile. The recovery plan emerges through collaboration across those boundaries.

If asked, “Why Flower?”

Answer:

> The product assumes these agents belong to different systems and potentially different organizations. Flower gives us the federation and communication layer so those agents can collaborate while remaining separately operated.

If asked, “Why not one central server?”

Answer:

> Centralizing all of the traveler’s calendar, employer data, transportation data, preference history, and potentially sensitive personal constraints creates both privacy and organizational-boundary problems. Our agents exchange the minimum constraints necessary for the decision rather than pooling all raw context.

If asked, “Where is the human supervision?”

Answer:

> Agents may inspect, negotiate, simulate, and propose autonomously. A human approval boundary sits before consequential actions such as spending money, rebooking transportation, cancelling important commitments, or sending external communications.

---

# 37. Definition of Done

The product is ready for demo when this exact sequence works from a clean launch:

```text
1. Start Continuity.
2. Display healthy itinerary.
3. Inject UA212 cancellation.
4. Mobility Agent detects disruption.
5. Mobility Agent produces alternatives.
6. Personal Agent evaluates private context.
7. Personal Agent queries Business Agent.
8. Business Agent returns schedule constraints.
9. Personal Agent shares minimal constraints.
10. Recovery options are filtered.
11. System recommends Option B.
12. Human presses Approve.
13. Mock flight changes.
14. Internal call moves.
15. Final recovery summary appears.
16. Privacy metric shows zero raw calendar events disclosed.
```

If this works reliably, stop adding features.

---

# 38. Core Rule for the Team

Whenever someone proposes a new feature, ask:

> Does this make the three-agent collaboration more convincing in the demo?

If the answer is no, do not build it.

The hackathon is not won by having the most features.

It is won by making one collaboration feel inevitable, useful, technically real, and immediately understandable.

---

# 39. Final Product Summary

**Continuity** demonstrates a future in which independently operated agents collaborate across transportation, personal, and business systems to repair disruptions around a human being.

The airline optimizes its flights.

The office optimizes its schedule.

The personal agent understands the traveler.

Flower lets those systems communicate without collapsing them into one omniscient platform.

When a flight disappears, the traveler should not spend the next hour manually rebuilding everything downstream.

The agents do the coordination.

The human makes the decision.
