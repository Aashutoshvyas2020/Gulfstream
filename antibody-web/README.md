# Antibody web console

A web frontend and backend for the Antibody AgentApp in `../agent`. The backend starts
real Flower runs (the same thing `flwr chat` + `/load .` does) and relays the run's
events to the browser: each of the 11 agents lights up its area as it reports, the
coordinator's ranking sets the health gauge, connector calls show up live, and the
alert streams in.

| File | What it does |
|---|---|
| `server.py` | FastAPI backend: `/` and `/console` serve the console, `/api/meta`, `POST /api/scan`, `GET /api/runs/{id}/events` (SSE), `POST /api/runs/{id}/stop` |
| `static/index.html` | The operator console: building cross-section, 11 agent cells, health gauge, threat injection, connectors, swarm feed, alert, network-immunity story |

## Run locally (no SuperGrid access needed)

Three PowerShell windows.

1. Model server:

   ```powershell
   $env:OLLAMA_CONTEXT_LENGTH = "32768"
   ollama serve
   ```

2. Local Flower SuperLink, from `..\agent`:

   ```powershell
   $env:PYTHONUTF8 = "1"
   $env:FLWR_MODEL_API_ENDPOINT = "http://127.0.0.1:11434/v1/responses"
   $env:ANTIBODY_MODEL = "gemma4:latest"
   uv run flower-superlink --insecure
   ```

   This needs a `local-agent` connection in `~/.flwr/config.toml`:

   ```toml
   [superlink.local-agent]
   address = "127.0.0.1:8000"
   insecure = true
   ```

3. The console, from this folder:

   ```powershell
   $env:PYTHONUTF8 = "1"
   ..\agent\.venv\Scripts\python server.py
   ```

Open http://127.0.0.1:8600 and press **Scan building**. A scan takes about 3 minutes on a laptop GPU.

Locally, `web_search`, `web_fetch`, Slack and Notion are exposed but their calls fail
(they need SuperGrid); the console marks them unavailable and the alert says so.

## Run on SuperGrid (once Flower Agent access is enabled)

```powershell
$env:PYTHONUTF8 = "1"
$env:ANTIBODY_SUPERLINK = "supergrid"
..\agent\.venv\Scripts\python server.py
```

On SuperGrid the model is Flower's Endeavor (`flower-endeavor-v1.0`), connectors work, and Slack /
Notion can be bound per scan after connecting them in Settings > Connectors (personal
workspace only in Flower 1.39).
