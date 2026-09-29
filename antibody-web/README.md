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

First do steps 1–3 of the root [Quick start](../README.md#quick-start-local-flower-superlink-no-supergrid-access-needed): `uv sync`, the Governor setup, and a local SuperLink, either with Endeavor (default) or in offline mode with Ollama. Then start the console from this folder.

macOS / Linux:

```bash
export PYTHONUTF8=1
../agent/.venv/bin/python server.py
```

Windows (PowerShell):

```powershell
$env:PYTHONUTF8 = "1"
..\agent\.venv\Scripts\python server.py
```

Open http://127.0.0.1:8600 and press **Scan building**.

- With Ollama, a scan takes about 3 minutes on a laptop GPU (specialists run one at a time).
- With Endeavor they run six at a time; scan time is not measured yet.

<details>
<summary>PowerShell: local SuperLink</summary>

With Endeavor, from `..\agent`:

```powershell
$env:PYTHONUTF8 = "1"
Remove-Item Env:FLWR_MODEL_API_ENDPOINT, Env:ANTIBODY_MODEL -ErrorAction SilentlyContinue
$env:FLWR_MODEL_API_KEY = "<your Flower API key>"
uv run flower-superlink --insecure
```

Offline with Ollama:

```powershell
$env:OLLAMA_CONTEXT_LENGTH = "32768"; ollama serve          # window 1
$env:PYTHONUTF8 = "1"                                       # window 2, from ..\agent
$env:FLWR_MODEL_API_ENDPOINT = "http://127.0.0.1:11434/v1/responses"
$env:ANTIBODY_MODEL = "gemma4:latest"
uv run flower-superlink --insecure
```

</details>

Locally, `web_search`, `web_fetch`, Slack and Notion are exposed, but their calls fail (they need SuperGrid). The console marks them unavailable and the alert says so.

## Run on SuperGrid (once Flower Agent access is enabled)

```bash
export PYTHONUTF8=1 ANTIBODY_SUPERLINK=supergrid
../agent/.venv/bin/python server.py
```

```powershell
$env:PYTHONUTF8 = "1"
$env:ANTIBODY_SUPERLINK = "supergrid"
..\agent\.venv\Scripts\python server.py
```

On SuperGrid the model is Flower's Endeavor (`flower-endeavor-v1.0`), connectors work, and Slack /
Notion can be bound per scan after connecting them in Settings > Connectors (personal
workspace only in Flower 1.39).
