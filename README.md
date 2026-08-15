# 🛶 Kayak: Open-Source AI Agent Platform

Kayak is a simple, opinionated, open-source AI agent platform containerized with Docker. It brings the power of agentic programming, customizable skills, autonomous coding tools, and sub-agent task orchestration to everyone.

---

## ✨ Features

- **🔄 Swappable Models via LiteLLM**: Use any cloud provider (Gemini, OpenAI, Anthropic) or local models (Hugging Face via vLLM).
- **💬 Parallel Conversations**: Create multiple conversations simultaneously and switch back and forth seamlessly.
- **🐳 Isolated Docker Sandboxes**: Launch any conversation inside an ephemeral, dedicated Docker container where the agent has full root access and an isolated workspace volume.
- **✨ Skills**: Markdown-based skills (`data/skills/<name>/SKILL.md`) that can be browsed and edited live in the UI. Skills support dual-mode: preloaded at startup or dynamically loaded on-demand.
- **🛠️ Tool**: Python-based tools (`data/tools/<name>/tool.py`) that can be used at runtime by your agents.
- **🤖 Visual Agent Configurations**: Define agent profiles (`data/agents/<id>.yaml`) with custom system prompts, temperature, allowed tools, and preloaded skills.
- **⚡ Long-Running Tasks & Sub-Agents**: Start long-running background shell commands or spawn sub-agents to investigate parallel sub-tasks while monitoring live logs in real time.
- **🔐 Per-Tool Permissions**: Each agent profile declares which tools it may use and whether a tool runs automatically (`auto_approve`), prompts you first (`ask_user`), or is blocked (`denied`). Permissions are enforced when the tool runs, not only when the model is offered it.

--

## 🚀 Quick Start

There is nothing to configure before starting. Kayak runs with no `.env` file and no
environment variables set.

### 1. Build the Sandbox Image

```bash
docker build -f Dockerfile.sandbox -t kayak-sandbox:latest .
```

### 2. Launch Kayak

```bash
docker compose up --build
```

### 3. Add a Model Provider Key

Open **http://localhost:8000**, go to **Settings**, and paste in a key for whichever
provider you want to use (Google Gemini, OpenAI, or Anthropic). Each one links to the
console where you obtain it. Keys are stored in `data/settings.json` on your own
machine, are never sent back to the browser, and take effect immediately.

Provider keys are **not** read from the environment. The Settings page is the only place
they come from, so there is exactly one place to look when a key is wrong and no
prior setup step to get right.

---

## 🔒 Security Model

Kayak agents run shell commands and read and write files, and the server mounts the
Docker socket so it can start sandbox containers. **Anything that can reach Kayak's
port can run code on the machine hosting it.** The defaults are set accordingly:

- The server binds to `127.0.0.1` and Docker Compose publishes the port to loopback
  only. Change either one and you are exposing a shell to your network.
- If you do need remote access, set `KAYAK_AUTH_TOKEN` to a long random string. Every
  API call then requires it, and the UI prompts for it once.
- Set `KAYAK_CORS_ORIGINS` to the exact origins that should be able to call the API.
- File tools are confined to each conversation's workspace directory; absolute paths,
  `..` traversal, and symlinks pointing outside it are refused.
- Every conversation runs in its own Docker container, with only that conversation's
  workspace mounted into it. Agents cannot read each other's files, the database, or
  your stored credentials.
- API keys are stored in `data/settings.json`, written owner-readable only, and are
  never returned in full over the API — the settings endpoint serves a masked preview.
  They are not passed to agents or into sandbox containers.

`.dockerignore` keeps `data/settings.json` and the local database out of built images.
Never commit or push an image built without it.

---

## 🧪 Development

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest
```

Run the app locally against a Vite dev server:

```bash
./run.sh --local
```

Optional environment variables, all of which control how the server is deployed rather
than what it can do. None are required, and provider credentials are deliberately not
among them:

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAYAK_HOST` | `127.0.0.1` | Bind address. |
| `KAYAK_AUTH_TOKEN` | _(empty)_ | Shared secret required on every API call when set. |
| `KAYAK_CORS_ORIGINS` | localhost origins | Comma-separated browser origins allowed to call the API. |
| `KAYAK_AGENT_MAX_ITERATIONS` | `25` | Tool-use steps allowed in a single turn. |
| `KAYAK_AGENT_MAX_SUBAGENT_DEPTH` | `3` | How deeply sub-agents may nest. |
| `KAYAK_DATA_DIR` | `./data` | Location of agents, skills, tools, workspaces, and the database. |
| `KAYAK_VLLM_PORT` | `8001` | Port the local vLLM container serves on. |
