# Flower: what we verified from primary sources

> Verified from Flower's docs, 2026-09-28/29. Flower Agent is marked experimental by Flower; re-check against current docs.

## What Flower Agent gives us (docs as of flwr 1.38/1.39)

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
  - Any write action (e.g. dispatching a contractor, messaging tenants) must be a **simulated tool we build**.
- **FAB limit.** A FAB holds either one `agentapp` or a `serverapp`/`clientapp` pair, never both.

## Flower deployment architecture

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
- Flower architecture: https://flower.ai/docs/framework/explanation-flower-architecture.html
- Network communication: https://flower.ai/docs/framework/ref-flower-network-communication.html
- Mods: https://flower.ai/docs/framework/how-to-use-built-in-mods.html
- Audit logging: https://flower.ai/docs/framework/how-to-configure-audit-logging.html
- SuperNode authentication: https://flower.ai/docs/framework/how-to-authenticate-supernodes.html
- Secure aggregation: https://flower.ai/docs/framework/explanation-ref-secure-aggregation-protocols.html
- Enterprise architecture patterns: https://flower.ai/blog/2025-12-02-enterprise-grade-federated-ai/
