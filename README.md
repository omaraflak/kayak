# Kayak

Open-source AI agent platform. Runs in Docker.

## Install

The easiest way is the [Kayak Launcher](https://github.com/omaraflak/kayak-launcher), a
desktop app that installs and updates Kayak for you. Download it, open it, done.

To run it yourself instead:

```bash
docker build -f Dockerfile.sandbox -t kayak-sandbox:latest .
docker compose up --build
```

Then open <http://localhost:8000>, go to **Settings**, and paste in an API key for
Gemini, OpenAI, or Anthropic. Keys are entered there, not in the environment.

> Kayak's agents run shell commands and the server mounts the Docker socket, so anything
> that can reach its port can run code on the host. It binds to `127.0.0.1` for that
> reason. If you expose it, set `KAYAK_AUTH_TOKEN` to a long random string.

## Development

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest
```

Backend with reload, on <http://localhost:8000>:

```bash
PYTHONPATH=. ./.venv/bin/python -m uvicorn backend.app.main:app --reload
```

Frontend with hot reload, on <http://localhost:3000>, proxying `/api` to the backend
above:

```bash
cd frontend && npm install && npm run dev
```

Frontend checks:

```bash
cd frontend && npx tsc --noEmit && npm test && npm run build
```

## Releasing

See [RELEASING.md](RELEASING.md).

## Configuration

All optional. Provider keys are deliberately absent — they come from the Settings page.

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAYAK_HOST` | `127.0.0.1` | Bind address. |
| `KAYAK_AUTH_TOKEN` | _(empty)_ | Shared secret required on every API call when set. |
| `KAYAK_CORS_ORIGINS` | localhost origins | Browser origins allowed to call the API. |
| `KAYAK_AGENT_MAX_ITERATIONS` | `25` | Tool-use steps allowed in a single turn. |
| `KAYAK_AGENT_MAX_SUBAGENT_DEPTH` | `3` | How deeply sub-agents may nest. |
| `KAYAK_DATA_DIR` | `./data` | Agents, skills, tools, workspaces, and the database. |
| `KAYAK_VLLM_PORT` | `8001` | Port the local vLLM container serves on. |
| `KAYAK_TTS_PORT` | `8011` | Port the local speech container serves on. |
| `KAYAK_TTS_IMAGE` | `omaraflak/kayak-audio:latest` | Image that serves speech models. |
