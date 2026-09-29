# Flower Collaborative Agent Hackathon: idea register

> **Status:** idea register, 2026-09-28. **Not a plan and not a decision.**
> Nothing here is selected. A choice is made separately on 2026-09-29; the
> chosen idea then gets its own plan (with the verify pass from CLAUDE.md).
>
> **Event:** Collaborative Agent Hackathon @ Stanford, Tue 2026-09-29,
> 9:30–19:30, 389 Jane Stanford Way.

Two independent idea sets are recorded here:

- **O1–O5**: the operator's ideas (developed in conversation with another agent; the ideas are the operator's). **O6–O7**: operator speculation, added 2026-09-28 with grounding notes.
- **C1–C7**: ideas generated independently by Claude Code from primary Flower and Sentience sources.

§4 maps overlaps, complements and the one architectural question several ideas converge on.

---

## 1. Constraints that apply to every idea

### The challenge

- Brief: "Build an open-source agent or multi-agent system with Flower that puts safe, human-supervised collaboration at the heart of the solution."
- The Stanford track asks teams to "showcase the collaborative aspect of Flower Agents running on SuperGrid."
- **Judging:**
  - (1) Use of Flower: depth of Agents and SuperGrid.
  - (2) Impact and originality.
  - (3) Demo and delivery.
  - Bonus for using the Endeavor model.
- **Submission:**
  - Published Flower Hub app.
  - GitHub repo.
  - Project description.
  - 3–5 minute presentation plus Q&A.
- **Runtime limits:**
  - 5-minute timeout per task.
  - Nebius models are available: Kimi-K2.7-Code, MiniMax-M3.

### What Flower Agent gives us (docs as of flwr 1.38/1.39)

- **AgentApp shape.** An AgentApp is one `@app.main()` function receiving `AgentSession` and `Context`.
  - `AgentSession` exposes `agent.prompt`, `agent.connectors`, `agent.events` and `agent.grid`.
  - `Context` exposes `run_config`, `state` (persists across a run series) and `run_id`.
- **Model access.** Models are reached through an OpenAI-compatible Responses endpoint (`FLWR_RUNTIME_BASE_URL`, `FLWR_RUNTIME_API_KEY`).
- **The tool loop is ours.** `connectors.tools(refs)` returns definitions and `connectors.call(tool_call)` executes one. That loop is the natural execution boundary for a Governor adapter.
- **Grid messaging is hub-and-spoke:**
  - The SuperLink AgentApp gets `get_nodes`, `push_messages` and `pull_messages`.
  - SuperNode AgentApps get `push_messages` only, used to reply via `src_node_id` / `message_id`.
- **Connectors are read-only.**
  - Built-in: `web_search`, `web_fetch`, `start_automation`.
  - Account-based (read): Slack, Notion, GitHub, Attio.
  - Any "write", "refund" or "send externally" action must be a **simulated tool we build**.
- **FAB limit.** A FAB holds either one `agentapp` or a `serverapp`/`clientapp` pair, never both.

### What Sentience Governor does today (0.3.2.1)

- **Stance:** observe-only, fail-open, local-first, no telemetry.
- **What it evaluates:** captured tool calls against declared intent, scope and policy.
  - Five default rules: POL-001 (declare before mutating), POL-002 (registration), POL-003 (classification), POL-004 (memory), POL-005 (escalation).
  - Token usage is attributed to the turn where activity happened.
- **Per-agent profiles:** since 0.3.2, `resolution.yaml` binds a profile to a session by `agent_id`.
- **Does not:**
  - block or modify execution;
  - aggregate across sessions, machines or a hosted plane;
  - know whether a declaration was truthful.
- **No Flower integration exists.** Every idea needs a small adapter around `connectors.call` and the grid tools, using the library / custom-runtime path.
- **Current capability is not a limit.** Governor already does a lot, but if a compelling idea needs Governor extended and there is time, we extend it at the hackathon. The "does not" list above is where we start, not what we're held to.
  - **Feasible builds only.** An extension is in bounds only if it realistically fits the day's build time alongside the Flower work and the demo. If it doesn't, the idea is scoped down or set aside, not stretched.
  - Extensions are hackathon code first. Anything that later ships in `sentience-governor` still goes through the normal release gates.
- **Public-copy rule:** any hold or gate built at the hackathon is hackathon code prototyping the L1 rung. It must never be described as Governor blocking. See our internal strategy notes.

### Implementation note: Pydantic AI stays available

Pydantic AI (with `pydantic-ai-governor` 0.1.1) remains available wherever it helps. Neither approach is a requirement; the selected idea decides.

- **Option A: Pydantic AI inside or alongside the Flower AgentApp.** Point it at the runtime's OpenAI-compatible endpoint and govern it with `pydantic-ai-governor`.
- **Option B: a Flower-native loop.** Write the model and tool loop directly against `connectors.tools()` / `connectors.call()` and the grid tools, with a thin Governor adapter.
- **Option C: mixed team** (operator, 2026-09-28). A Pydantic AI agent governed by `pydantic-ai-governor` acts as one participant that coordinates with Flower-native agents.
  - **Most natural placement:** make it the SuperLink AgentApp, with `get_nodes`, `push_messages` and `pull_messages` wrapped as Pydantic AI tools.
    - Every delegation to another node then becomes a tool call that `pydantic-ai-governor` already captures, with no new capture code.
    - SuperNode agents can be Flower-native or Pydantic AI.
  - **Unverified:** whether an agent *outside* Flower can drive Flower agents. The grid tools exist only inside an AgentApp; the `use-openai-sdk` how-to has not been read.

### Shared technical risks (check first on the day)

1. **Multiple SuperNodes.**
   - Does our SuperGrid federation give us more than one SuperNode?
   - How are SuperNode AgentApps triggered on message arrival?
   - Every multi-agent idea needs three or more nodes. Fallback: a local SuperLink with several SuperNodes.
2. **Where Governor records live.**
   - Executor processes are isolated, so records written to local disk may vanish after the run.
   - Records likely have to leave through `agent.events` or `context.state`.
3. **The 5-minute task timeout.** Anything that waits on a human must fit inside it.

---

## 2. Operator ideas (O1–O7)

The operator's summary spectrum:

| Idea | Theme | Question |
| :-- | :-- | :-- |
| O1 | Evidence | Did the other agent really do what it claims? |
| O2 | Continuity | Did the mission survive across agents and changing context? |
| O3 | Human attention | When does the human actually need to intervene? |
| O4 | Authority | Who gave this agent permission to perform this action? |
| O5 | Composition | Can individually compliant agents collectively violate the mission? |

### O1. Evidence-grounded multi-agent collaboration

- **Question.** Can Agent B trust that Agent A actually did what it claims?
- **Build.**
  - Agents exchange assertions, conclusions and delegated work.
  - Governor independently captures the underlying execution evidence.
  - A receiving agent can tell "A says it checked the records" apart from "there is execution evidence that A checked the records."
- **Roles.**
  - Flower provides the collaboration.
  - Governor provides an independent evidence plane across all participants.
  - Human review sees the whole chain.
- **Core idea.** Agents should not have to trust other agents' self-reports.

### O2. Mission continuity across collaborative agents

- **Origin.** Extends Mission Continuity (Horizon hackathon, 2026-09-25) from temporal continuity within one agent to distributed continuity across a collaborative system.
- **Build.**
  - Different Flower agents hold different pieces of the task, or operate in separate contexts.
  - As context is summarized or compacted and responsibility moves between agents, we check whether the original mission, restrictions and critical evidence stay authoritative.
- **Governor's role.** Provides an execution record independent of all the changing contexts.

**O2 implementation option: fork Mission Continuity** (operator question, 2026-09-28; subject to team agreement)

- **Source:** `crescerelabs/mission-continuity` at `dd3a648`. Public, about 4.3k lines in `mission_continuity/`.
- **Why the case distributes naturally.** The billing-dispute case already spans three record systems. Each is a candidate SuperNode silo:
  - billing (the charge vs a released authorization);
  - support (the credit that was denied);
  - account admin (seat U-2 added the add-on).
  - All three answer-key findings need cross-silo reconciliation.
  - The single-agent weak spot (R3: governed runs never cited admin U-2) would sit on its own node.
- **Where Flower creates the phenomenon** (this is what keeps it from failing the §5 test):
  1. **Every grid hop is a forced compaction.** A SuperNode agent sees only its local records plus the pushed message. What reaches the coordinator is whatever that node chose to summarize, so the mission and its restrictions have to travel inside messages.
  2. **The 5-minute task limit forces run series.** AgentApp continuity is explicit: it goes through `context.state` and the trace. Long investigations must checkpoint and resume across runs, which was already a listed next experiment.
  3. **Prohibited tools become authority questions across nodes** (overlaps O4): refund, modify billing, contact the customer.
  4. Single-agent in-context compaction on its own would **not** pass the test; that is a Sentience demo with Flower attached.
- **Reuse vs rebuild:**
  - **Reuse (likely portable):**
    - synthetic dataset and answer key;
    - Mission Kernel;
    - evaluator and scoring;
    - retention and continuity logic;
    - fact and mission-term checks.
  - **Rebuild or drop:**
    - the Streamlit app, replays, BFL film and RawTree;
    - Claude-specific parts: `anthropic:claude-sonnet-5`, Keychain key handling, and compaction measurement through Anthropic's token-counting endpoint.
    - Flower models (Endeavor, Kimi, MiniMax) come through the runtime's OpenAI-compatible endpoint, so token measurement needs a new source.
- **Consequences to state plainly:**
  - A model change makes results **not comparable** to the published Claude numbers. Treat any Flower result as a new experiment.
  - The known caveats carry over unless fixed:
    - mission-term survival is partly by construction;
    - checks are keyword-based;
    - small n;
    - governed scored 3.0 vs baseline 4.0.
  - The 2×2 design (mission placement × pinning) is the fix already on file.
- **Feasibility check on the day:**
  - Does the Pydantic AI capability (compaction hook, `pydantic-ai-governor`) run inside an AgentApp against the Flower runtime endpoint?
  - If not, port the continuity logic into a Flower-native loop (see the implementation note in §1).
- **Operator stance (2026-09-28):** a fork is a new experiment by definition. Re-architecting whatever Flower requires is expected, not a limitation.

### O3. Human attention as a governance resource

- **Principle.** "Human in the loop" should not mean approving everything.
- **Build.**
  - Agents collaborate freely while Governor evaluates their actions.
  - Only specific governance conditions escalate to the human.
- **Question.** Can governance make human supervision sparse and meaningful rather than constant?
- **Measures:**
  - total agent actions;
  - governance events;
  - escalations;
  - human decisions;
  - successful autonomous actions.
- **Visual demo.** A funnel, e.g. 40 autonomous actions → 5 governance concerns → 2 needing human judgment.
- **Metric.** Human attention per unit of useful autonomous work.

### O4. Delegated authority: authority cannot silently expand

- **Setup.** Agents have different authority: A may investigate, B may modify, C may communicate externally.
- **Rule.** Agents may delegate work to one another, but delegation must not manufacture authority.
  - Example: if the investigator asks another agent to issue a refund, does the receiver inherit permission just because another agent asked?
- **Governor's role.** Tracks objective, actor, operation and target across delegation.
- **Human's role.** Involved when authority cannot be established.
- **Question.** Who authorized this agent to do this? This is authority provenance, distinct from continuity.

### O5. Govern the team, not just the agents

- **Premise.** Every agent can act within its own permitted scope while the team's combined behavior violates the human's intent.
  - Example: A may retrieve information and B may communicate externally. A hands sensitive information to B, and B sends it where it should not go.
- **Question.** Can every agent be locally compliant while the multi-agent system is globally unsafe?
- **Flower's role.** Makes the distributed collaboration fundamental to the experiment.
- **Governor's role.** Governor evaluates per agent/session today, so this may show whether governance needs a **team-level execution boundary** above individual agents.
- **Why it matters.** Most likely of the five to uncover a genuine architectural requirement rather than demonstrate an existing capability.

### O6. A shared execution ledger across the federation (operator speculation, 2026-09-28)

**Operator's framing:** use Flower's federated-learning architecture to spread each agent's Governor log across the federation, so that all agents stay in agreement ("consensus") about each other's execution records.

**Grounding notes** (Claude, from Flower's docs, §6):

1. **"Consensus" is the wrong word for what Flower can do; a shared tamper-evident ledger is the right one.**
   - Flower is strictly hub-and-spoke. SuperNodes only open outbound connections to the SuperLink and never talk to each other.
   - The hub already orders everything, so there is nothing for peers to vote on.
   - The realistic mechanism is a round-based transparency log, in the spirit of Certificate Transparency:
     - Each round, each node sends the head hash of its Governor record.
     - The hub combines the heads into one root (e.g. a Merkle root) and sends the root back.
     - Each node checks its own entries are included and writes the root into its own record.
   - What this buys:
     - No node can quietly rewrite its history later.
     - The hub cannot silently drop a node's entries.
2. **Flower's topology creates a real phenomenon to study: hub equivocation.**
   - Because nodes cannot talk to each other, they cannot detect a hub that shows different roots to different nodes.
   - Detecting it needs an out-of-band witness. The human supervisor is a natural candidate.
   - This passes the "does Flower create the phenomenon?" test: the trust problem comes from Flower's topology.
3. **A second, independent witness already exists.**
   - The SuperLink's event log (`--enable-event-log`) records metadata for every Fleet API push and pull: actor, node ID, action, run ID, status. It does not record message content.
   - The two sources can be cross-checked:
     - Governor records what the agent did and declared.
     - Flower's log records which messages actually crossed the hub.
   - Caveat: the log requires SuperLink account auth (OIDC), so whether it is available on SuperGrid is unknown. It can run locally.
4. **Node identity exists but is not content signing.**
   - Each SuperNode has an EC key pair and signs a timestamp for authentication. It does not sign message contents.
   - To make a record attributable to a node, the records would need their own signatures.
5. **Governor gap, feasible to close on the day.**
   - Events link by `previous_event_id`, which gives ordering, not a cryptographic hash chain.
   - Tamper-evidence would need per-event hashing. That is a small extension, in line with the feasible-builds rule.
6. **Where federated learning literally applies:**
   - **SecAgg+** can sum per-node governance counts (e.g. violations per rule) so that even the hub never sees an individual node's numbers.
   - **Mods** wrap a ClientApp and see every message in and out, which makes them a candidate Governor capture point at Flower's message boundary. Unverified: mods are documented for ClientApps only, not AgentApps.
   - A FAB cannot hold both an AgentApp and a ServerApp/ClientApp pair.
     - A companion governance-sync run would be a **separate run on the same federation**.
     - Whether it can read the agent run's records on the same host is unknown.
     - The simpler path is to carry record heads inside the agent run's own grid replies.
7. **Relation to other ideas:**
   - Supplies the evidence plane that O1 needs and the cross-node view that O5 needs.
   - Is the tamper-evident form of C3.
8. **Boundary.** Cross-node, attested chain of custody is the L3 (paid) rung in `enforcement-ladder.md`. It falls under open decision 2 (§5).
9. **Minimal feasible demo:**
   - Three nodes, several rounds, one published root per round.
   - Then three attacks:
     - (a) edit one node's past record: detected;
     - (b) the hub drops one node's entries: that node detects it;
     - (c) the hub equivocates: undetected until the human compares roots.
   - Case (c) is the finding.

### O7. Federated learning over governance records, as a standing part of the system (operator speculation, 2026-09-28)

**Operator's framing:**
- Use Flower's ServerApp/ClientApp split so every agent's Governor record takes part in federated learning.
- This runs as a continuous part of how the system exists, not a one-off analysis.

**Grounding notes** (Claude):

1. **Role mapping is inverted from the framing.** In Flower the data holder is the **client**: the SuperNode runs the ClientApp, and data stays there. So each agent's Governor record sits on a SuperNode and is read by a ClientApp. The **ServerApp** on the SuperLink aggregates. One aggregator, many Governor clients.
2. **Shape: two runs on one federation.**
   - A FAB cannot hold both an AgentApp and a ServerApp/ClientApp pair.
   - So the agents run in one run, and a governance-learning run executes periodically beside them (multi-run).
   - **Unverified:** whether the ClientApp can read records the agent run wrote on the same node (isolated SuperExec processes). The fallback is agent runs persisting records where the ClientApp can reach them.
3. **What is being "learned" decides which idea this is.** Three candidates:
   - (a) **Aggregate statistics:** violations per rule, undeclared-token share, optionally through SecAgg+. This is C3.
   - (b) **A trained model**, e.g. a drift or anomaly signal, fed back to agents as advice. This is C7, and it has C7's risks: labelled data volume, and tension with P1 (never treat inference as measurement).
   - (c) **Per-round commitments to each record** (ledger roots). This is O6.
   - The new element O7 adds to all three is **continuous rounds**: governance learning as a heartbeat of the running system, not a one-off analysis.
4. **Does Flower create the phenomenon?** Yes, if the question is "what can a federation learn about its own governance without anyone pooling execution records?"
5. **Boundary.** Fleet-level governance learning sits on the paid side of the boundary. It falls under open decision 2 (§5).

---

## 3. Claude Code ideas (C1–C7)

Each idea follows the same template: build, question, Flower's role, Governor's role, demo, learning, risk.

### C1. Intent that travels: governing delegation across the grid

- **Build:**
  - Each coordinator `push_messages` carries a declaration envelope (intent, scope, issuer).
  - Each SuperNode agent is evaluated against the declaration it was handed, not one it wrote itself.
  - Delegation is itself a governed tool call.
- **Question.** Does declared intent survive delegation, or does scope narrow, widen or change shape at each hop?
- **Flower's role.** Grid messaging is the delegation channel. Hub-and-spoke makes every hop explicit.
- **Demo.** The human approves a scoped delegation, then sees a delegated / declared / done diff per node.
- **Learning.** A declaration issued by another party makes truthfulness checkable, closing a limit Governor's README states.
- **Risk.** Governor may not cleanly accept an externally supplied declaration.

### C2. Contagion: does a prompt injection spread through a federation?

- **Build:**
  - An injection sits in content a node fetches via `web_fetch`.
  - It tries to make the coordinator push new instructions to the other nodes.
  - Run with and without envelopes.
- **Question.** How does compromise spread through hub-and-spoke? Can per-node records reconstruct the path?
- **Flower's role.** Role-scoped grid tools (SuperNodes can only reply) are a real topological defence under test.
- **Demo.** A live infection map. Each hop is backed by the first undeclared action on that node.
- **Learning.**
  - Whether the hub limits spread or concentrates it.
  - Whether per-session records can order events across machines.
- **Risk.** The model refuses the injection. Prepare several strengths and report the resistance rate honestly.

### C3. Governance without centralizing traces

- **Build:**
  - Records stay on each node.
  - Only aggregates reach the coordinator: violations per rule, undeclared-token share, optionally masked sums.
  - A human sees fleet posture and can request a disclosure from one node only.
- **Question.** Can an operator supervise a fleet without breaking local-first?
- **Flower's role.** Aggregate-don't-pool, Flower's founding idea, applied to governance evidence.
- **Learning.** How much signal survives aggregation, and where aggregates hide the one incident that matters.
- **Risk (strategic).** Overlaps the paid L3 fleet-visibility surface, and code goes in a public repo.
  - **Operator decision needed.**

### C4. Declaration inflation: a self-improving loop that learns to game its governor

- **Build:**
  - Multi-round loop: violations feed back and agents revise how they declare.
  - The coordinator merges proposed declaration or profile changes.
  - The human approves or rejects each merge.
- **Question.** When governance feedback is the optimization signal, do agents improve, or do they declare ever more broadly until the flags stop?
- **Demo.** Violations fall while declared scope width rises; the human rejects the inflating merge.
- **Learning.** Whether POL-001 and scope checks can be Goodharted. May reveal declaration breadth as a quantity worth governing.
- **Risk.** Too few rounds in a day. Keep rounds short and state the n.

### C5. The supervisor's bottleneck

- **Build:**
  - N nodes run concurrently.
  - HIGH_CONSEQUENCE and POL-001 flags feed one human queue through a session-local hold (hackathon code, L1 prototype).
  - Compare three policies: escalate everything, tuned profile, declare-first.
- **Question.** How does supervision scale with N? Does declaring up front reduce review load?
- **Demo.** A human working the queue live as N goes from 1 to 5: time-outs, rubber-stamping, queue depth.
- **Learning.** Hands-on evidence on whether L1 "prompt" mode is usable, and what false-positive rate a human tolerates.
- **Risk.**
  - Holding an action while it waits for approval inside a 5-minute task.
  - Copy discipline: never say Governor blocks.

### C6. Cross-examination: agents audit each other's execution records

- **Build:**
  - Each node receives another node's Governor record (not its data) and judges action against declaration.
  - Compare with Governor's deterministic verdicts on seeded cases, including in-scope prohibited writes.
- **Question.** Can LLM peer auditors catch what the rules miss, and how often do they invent violations?
- **Demo.** A 2×2 grid of auditor vs rules; the human resolves disagreements.
- **Learning.** Directly tests the parked gap: scope is evaluated per system, and in-scope prohibited writes are untested.
- **Risk.**
  - Seeding enough labelled cases in a day.
  - It exposes a Governor gap publicly. **Operator decision needed.**

### C7. Learning a governance signal from siloed execution records

- **Build:**
  - A Flower ServerApp/ClientApp trains a drift classifier on Governor records held by separate orgs, with no pooling.
  - An AgentApp uses it as an advisory signal.
- **Question.** Can organizations learn governance signal together without sharing agent traces?
- **Learning.** Whether execution records carry learnable signal. Tension with P1 (no inference as measurement; enforce only on declared facts).
- **Risk.**
  - Labelled data volume.
  - The demo is a training curve.
  - It leans on federated learning, while judging weights Agents and SuperGrid.

### C: tradeoffs at a glance

| | Flower fit | How visible the result is | Build risk | New learning for Governor |
| :-- | :-- | :-- | :-- | :-- |
| C1 Intent travels | High | Medium | Medium | High |
| C2 Contagion | High | Very high | Medium | Medium–High |
| C3 Private oversight | High | Medium | Low | Medium (strategic question) |
| C4 Declaration inflation | Medium–High | High | High | Very high |
| C5 Supervisor bottleneck | High | High | Medium–High | High |
| C6 Cross-examination | Medium | Medium | Medium | High |
| C7 FL governance signal | Low for this rubric | Low | High | Medium |

Considered and dropped: purpose-limited disclosure between private data holders. It is too close to PreventNet (2nd place, Berlin 2026).

---

## 4. Mapping: similarities, complements, surprises

### Direct overlaps (same core question, different framing)

| Operator | Claude | Relationship |
| :-- | :-- | :-- |
| **O3** Human attention | **C5** Supervisor bottleneck | **Near-identical core.** O3 frames success as sparsity: a funnel of actions → concerns → human decisions, and attention per useful work. C5 frames it as scaling and stress: N = 1→5, queue saturation, three escalation policies, L1 hold usability. They merge naturally: O3's funnel is the headline metric, C5's policy comparison is the experiment. |
| **O4** Delegated authority | **C1** Intent that travels | **Close cousins.** Both govern what crosses a delegation hop. C1 carries *intent and scope* (what the work is for); O4 carries *authority* (who may do it). O4 is the sharper question: the confused-deputy / authority-laundering case. One envelope could carry both. Governor's per-agent profile binding (0.3.2, keyed on `agent_id`) is the nearest existing hook for per-agent authority. |

### Partial overlaps

| Operator | Claude | Relationship |
| :-- | :-- | :-- |
| **O1** Evidence-grounded trust | **C6** Cross-examination | Both have one agent consult another's execution record. C6 is **after the fact** (audit, scored against rules). O1 is **at run time**: the receiving agent uses evidence to decide whether to rely on a claim before acting. O1 is the more novel product idea; C6 is the more measurable experiment. |
| **O5** Compositional safety | **C2** Contagion | Both are harms that only appear across agents. C2 is adversarial: an injection spreads. O5 needs no attacker: well-behaved agents compose into a violation. O5 is the more fundamental question. |
| **O1**, **O5** | **C3** Private oversight | O1 and O5 both need evidence to leave the node where it was produced. C3 asks how to do that without breaking local-first. |

### Unique to one set

- **O2 Distributed mission continuity.** No Claude counterpart.
  - Builds on verified Mission Continuity results and the parked finding that context mutation is not a governance event.
  - The only idea with a ready baseline from our own prior runs.
- **C4 Declaration inflation.** No operator counterpart. The only idea whose headline result is a failure of governance itself (Goodharting the governor).
- **C7 Federated learning signal.** No operator counterpart. The weakest fit to this rubric.

### The convergence (the notable finding)

Four ideas, arrived at independently from both sets, hit the same limit in Governor today:

- **O1** needs evidence of what another agent did.
- **O5** needs to see across agents to detect a composed violation.
- **C2** needs to order events across nodes.
- **C3** needs fleet posture across nodes.

In each case, Governor's own README says it does not aggregate across sessions or machines, and its rules evaluate per agent/session.

So the independent question under all four is: **does multi-agent governance need a team-level evidence plane or execution boundary above the individual session?** O5 asks it most directly.

Two consequences:

1. Any of these is likely to produce a genuine architectural finding, not a demonstration.
2. The answer sits on the open/paid boundary. L3 (distributed, cross-session) is the paid tier in `enforcement-ladder.md` and `open-core-boundary.md`. Building it in a public hackathon repo is an **operator call before build**, not an engineering one.

### Natural combinations (to test against a ~6.5-hour build)

- **O4 + C1, "authority and intent envelope."** The envelope is the base layer. O4's refund case is the demo. The human is involved only when authority cannot be established, which gives O3's funnel for free.
- **O3 + C5, "sparse supervision."** The funnel is the headline, the policy comparison is the experiment. Low novelty risk, strongest fit to "human-supervised."
- **O5 + O1, "local compliance, global violation, with evidence."** A→B exfiltration composed from individually compliant steps. It is detected only when records are joined across nodes, and it shows what a team-level boundary would need. Highest insight, highest scope risk.
- **O2 alone.** Distributed continuity with the Mission Continuity measurement method reused. Lowest learning-curve risk because the method is proven, and it has a known baseline.

### Practical notes per operator idea

- **O1:** records must be retrievable across nodes at run time. Shared risk 2 (record location) becomes central.
- **O2:** compaction happens inside our own AgentApp code (Flower does not compact). The design must make context handoff between agents explicit so it can be measured.
- **O3:** the approval step must fit within the 5-minute task timeout. Consider approvals that happen between runs (via `context.state` and a run series) rather than mid-run.
- **O4, O5:** "refund", "modify" and "send externally" must be **simulated** tools, because Flower connectors are read-only.

---

## 5. Open decisions for 2026-09-29

1. Which direction or combination, weighed against the judging criteria: Flower depth, originality, demo.
   - **Does Flower create the phenomenon we're investigating?** Would removing Flower fundamentally weaken or eliminate the experiment? If not, it is a Sentience demo with Flower merely attached.
2. Whether a cross-node evidence plane (O1, O5, C2, C3) may be built in public code, given the paid-tier boundary.
3. Whether surfacing Governor gaps publicly (C6; possibly O5) is acceptable.
4. Confirm on the day: SuperNode count and triggering, record persistence, Endeavor availability.

## 6. Flower deployment architecture (reference)

From Flower's framework docs:
- `explanation-flower-architecture`
- `ref-flower-network-communication`
- mods
- audit logging
- SuperNode authentication
- the enterprise architecture blog (Dec 2025)

### Components

| Term | What it is | Lifetime | Where |
| :-- | :-- | :-- | :-- |
| **SuperLink** | The hub. Forwards task instructions to SuperNodes and receives results. Holds run and message state. All traffic passes through it. | Long-running | Coordinator / server side |
| **SuperNode** | A participant (a data holder). Connects **outbound** to the SuperLink, pulls tasks, runs them, returns results. Never accepts inbound connections; never talks to other SuperNodes. | Long-running | Each participant's machine |
| **SuperExec** | Process manager. Schedules, launches and manages the short-lived app processes for a run, isolated from the orchestration code. One on each side. | Long-running | Next to the SuperLink and each SuperNode |
| **ServerApp** | Project code on the hub side for federated learning: selects clients, configures them, aggregates results. | Short-lived, per run | Launched by SuperExec against the SuperLink |
| **ClientApp** | Project code on the participant side for federated learning: local training, evaluation, pre- and post-processing. Data stays here. | Short-lived, per run | Launched by SuperExec against a SuperNode |
| **AgentApp** | Agent code (`@app.main()`). On the SuperLink it gets `get_nodes`, `push_messages` and `pull_messages`; on a SuperNode it gets `push_messages` only. | Short-lived, per run | Either side |
| **FAB** | Flower App Bundle: the packaged app. Holds **either** one AgentApp **or** a ServerApp/ClientApp pair. | Artifact | Pulled by nodes at run start |
| **Run / run series** | One execution of a FAB. Several runs can share one SuperLink and its SuperNodes (multi-run); a node takes part only if selected. | Per run | Federation |
| **Federation / SuperGrid** | SuperGrid is Flower's hosted, multi-tenant grid. A federation is an isolated workspace on it; one SuperNode can belong to several. | Persistent | Hosted |
| **Mods** | Wrappers around a ClientApp, `Callable[[Message, Context, next], Message]`, that run before and after each message. Can inspect or modify messages and context. Client side only. | Per call | ClientApp |

### Isolation

- **Subprocess mode (default):** the SuperLink or SuperNode starts SuperExec itself.
- **Process mode:** SuperExec runs separately (e.g. in another container) and connects over the Runtime API.

### Connections

| Connection | Port | Direction | Carries |
| :-- | :-- | :-- | :-- |
| Fleet API | 9092 | SuperNode → SuperLink | Messages and FABs |
| Runtime API | 8000 | ServerApp / SuperExec → SuperLink | Run discovery, FABs, messages |
| Runtime API | 9094 | ClientApp / SuperExec → SuperNode | FABs, messages |
| Control API | 8000 | `flwr` CLI → SuperLink | Federation and run management |

### Trust features

- **SuperNode authentication.**
  - Each SuperNode has an EC key pair and signs a timestamp.
  - `--enable-supernode-auth` restricts the SuperLink to registered keys.
  - Requires TLS.
  - Gives stable node identity. Message contents are not signed.
- **SuperLink event log (`--enable-event-log`).**
  - Requires account auth (OIDC).
  - Records JSON metadata for CLI (user) events and Fleet (application) events: timestamp, actor ID/type/IP, action (e.g. `FleetServicer.PullMessages`), run ID, FAB hash, status.
  - Does not record message contents.
- **SecAgg / SecAgg+.** Secure aggregation: `SecAggPlusWorkflow` in the ServerApp plus `secaggplus_mod` in the ClientApp.
- **Differential privacy mods.**
- **Content-addressable messaging.** Flower's enterprise material claims verifiable audit logging down to each message.

## Sources

- Luma event page: https://luma.com/flwrlabs-bamu
- Flower Discuss, Stanford 2026 details: https://discuss.flower.ai/t/collaborative-agent-hackathon-stanford-ca-2026/1275
- Flower Agent docs: https://flower.ai/docs/agent/ (AgentApp runtime, connectors, agents and federations)
- Grid messaging tools: flwrlabs/flower PR #8143; node-role scoping: PR #8209
- Endeavor 1.0: https://flower.ai/blog/2026-09-01-introducing-endeavor-1.0
- PreventNet-Flower (prior art): https://github.com/NishankKS/PreventNet-Flower
- Sentience Governor README and `docs/profile.md` (public repo, 0.3.2.1)
- Flower architecture: https://flower.ai/docs/framework/explanation-flower-architecture.html
- Network communication: https://flower.ai/docs/framework/ref-flower-network-communication.html
- Mods: https://flower.ai/docs/framework/how-to-use-built-in-mods.html
- Audit logging: https://flower.ai/docs/framework/how-to-configure-audit-logging.html
- SuperNode authentication: https://flower.ai/docs/framework/how-to-authenticate-supernodes.html
- Secure aggregation: https://flower.ai/docs/framework/explanation-ref-secure-aggregation-protocols.html
- Enterprise architecture patterns: https://flower.ai/blog/2025-12-02-enterprise-grade-federated-ai/
