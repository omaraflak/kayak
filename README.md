# Kayak

Open-source AI agent platform for macOS. Agents run inside Docker containers on your
Mac; models come from Gemini, OpenAI, Anthropic, or run locally.

Kayak supports macOS only, on Apple Silicon and Intel, with
[Docker Desktop](https://www.docker.com/products/docker-desktop/).

## Install

The easiest way is the [Kayak Launcher](https://github.com/omaraflak/kayak-launcher), a
Mac app that installs and updates Kayak for you. Download it, open it, done.

To run it from source instead, start Docker Desktop and run:

```bash
docker compose up --build
```

Then open <http://localhost:8000>, go to **Settings**, and paste in an API key for
Gemini, OpenAI, or Anthropic. Keys are entered there, not in the environment.

The first conversation pulls the sandbox image (`omaraflak/kayak-sandbox`) if your Mac
does not have it yet. To try changes to `Dockerfile.sandbox`, build it under that name:

```bash
docker build -f Dockerfile.sandbox -t omaraflak/kayak-sandbox:latest .
```

> Kayak's agents run shell commands and the server mounts the Docker socket, so anything
> that can reach its port can run code on your Mac. It is published on `127.0.0.1` for
> that reason. If you expose it, set `KAYAK_AUTH_TOKEN` to a long random string.

## How it runs

- **The server** is a container (`Dockerfile`) holding the API and the web UI.
- **Every conversation** gets its own sandbox container, with its workspace mounted at
  `/workspace`. Agents' commands and custom tools only ever run there.
- **Local models** run in containers too: text models on vLLM's CPU image, speech and
  transcription on Kayak's own audio image (`Dockerfile.audio`). Docker Desktop gives
  containers no GPU, so on Apple Silicon the launcher serves MLX models
  (`mlx-community/...`) on the Apple GPU instead, natively on the Mac.

Your data lives in `data/` when running from source, and in
`~/Library/Application Support/Kayak/data` under the launcher.

## Development

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest
```

Backend with reload, on <http://localhost:8000> (agents still need Docker Desktop):

```bash
PYTHONPATH=. ./.venv/bin/python -m uvicorn backend.app.main:app --reload --reload-dir backend
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

All optional, and set in the environment of the server container. Provider keys are
deliberately absent — they come from the Settings page.

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAYAK_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` inside the image). |
| `KAYAK_PORT` | `8000` | Port the server listens on. |
| `KAYAK_AUTH_TOKEN` | _(empty)_ | Shared secret required on every API call when set. The Kayak app sets a random one by itself, so containers on the same Mac cannot use the API. |
| `KAYAK_CORS_ORIGINS` | localhost origins | Browser origins allowed to call the API. |
| `KAYAK_DATA_DIR` | `./data` | Agents, skills, tools, workspaces, and the database. |
| `KAYAK_HOST_DATA_DIR` | _(discovered)_ | Where the data directory is on the Mac, for mounting into sibling containers. |
| `KAYAK_AGENT_MAX_ITERATIONS` | `25` | Tool-use steps allowed in a single turn. |
| `KAYAK_AGENT_MAX_SUBAGENT_DEPTH` | `3` | How deeply sub-agents may nest. |
| `KAYAK_AGENT_MAX_CONCURRENT_TOOLS` | `8` | Read-only tool calls run at once. |
| `KAYAK_SANDBOX_IMAGE` | `omaraflak/kayak-sandbox:latest` | Image every conversation's container runs. |
| `KAYAK_AUDIO_IMAGE` | `omaraflak/kayak-audio:latest` | Image that serves speech and transcription models. |
| `KAYAK_VLLM_PORT` | `8001` | Port the local text model is published on. |
| `KAYAK_TTS_PORT` | `8011` | Port the local speech model is published on. |
| `KAYAK_STT_PORT` | `8021` | Port the local transcription model is published on. |
