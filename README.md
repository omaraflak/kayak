# 🛶 Kayak: Open-Source AI Agent Platform

Kayak is a simple, opinionated, open-source AI agent platform containerized with Docker. It brings the power of agentic programming, customizable skills, autonomous coding tools, and sub-agent task orchestration to everyone.

---

## ✨ Features

- **🔄 Swappable Models via LiteLLM**: Use any cloud provider (Google Gemini, OpenAI, Anthropic Claude) or local models (vLLM, Ollama) by configuring a standard model string.
- **💬 Parallel Web Conversations**: Create multiple conversations simultaneously and switch back and forth seamlessly without losing streaming state or history on page reload.
- **🐳 Isolated Docker Sandboxes**: Optionally launch any conversation inside an ephemeral, dedicated Docker container where the agent has full root access and an isolated workspace volume.
- **✨ Dedicated Skills Engine**: Markdown-based skills (`data/skills/<name>/SKILL.md`) that can be browsed and edited live in the UI. Skills support dual-mode: preloaded at startup or dynamically loaded on-demand.
- **🤖 Visual Agent Configurations**: Define agent profiles (`data/agents/<id>.yaml`) with custom system prompts, temperature, allowed tools, and preloaded skills.
- **🛠️ Interactive Tool Studio**: Chat with the Tool Architect agent to design new tools. The platform auto-extracts JSON parameter schemas from Python type annotations and docstrings, runs automated verification tests (`verify.py`), and activates tools with 1-click.
- **⚡ Long-Running Tasks & Sub-Agents**: Start long-running background shell commands or spawn sub-agents to investigate parallel sub-tasks while monitoring live logs in real time.

---

## 🏗️ Architecture & Philosophy

Kayak is built with strict **opinionated simplicity**:

1. **Single Source of Truth**:
   - `data/agents/<id>.yaml`: Agent profiles.
   - `data/skills/<name>/SKILL.md`: Skills with markdown instructions.
   - `data/tools/<name>/tool.py`: Typed Python tool functions.
   - `data/tools/<name>/verify.py`: Unit test verification suite.
   - `data/workspaces/<convo_id>/`: Dedicated workspace directories.
   - `data/kayak.db`: SQLite database (WAL mode) for indexed conversation history.

2. **Core Tech Stack**:
   - **Backend**: Python 3.11, FastAPI, Asyncio, LiteLLM, aiosqlite, Docker SDK.
   - **Frontend**: TypeScript, React 18, Vite, Tailwind CSS, Lucide Icons.
   - **Protocol**: REST API + Server-Sent Events (SSE) stream for real-time tokens and tool events.

---

## 🚀 Quick Start with Docker (Recommended)

### 1. Configure Environment
Set your preferred API keys in your environment or a `.env` file:
```bash
export GEMINI_API_KEY="your-gemini-api-key"
# or
export OPENAI_API_KEY="your-openai-api-key"
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

### 2. Build and Run Sandbox Image
```bash
docker build -f Dockerfile.sandbox -t kayak-sandbox:latest .
```

### 3. Launch Kayak
```bash
docker compose up --build
```
Open **http://localhost:8000** in your browser!

---

## 💻 Running Locally (Development Mode)

### Backend:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run FastAPI backend
python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend:
```bash
cd frontend
npm install
npm run dev
```
Open **http://localhost:3000** for the Vite development server (proxies `/api` to port 8000).

---

## 🛠️ Built-in Tools

| Tool | Description |
| :--- | :--- |
| `read_file` | Read file contents or specific line ranges (1-indexed) |
| `write_file` | Create or overwrite files |
| `edit_file` | Precise search-and-replace for existing files |
| `list_directory` | List workspace files and directories |
| `run_command` | Execute shell commands in local workspace or Docker container |
| `start_background_task` | Start long-running shell process in the background |
| `get_task_status` | Check status, exit code, and live stdout/stderr logs of a task |
| `send_task_input` | Send stdin text to a running background task |
| `stop_task` | Terminate a running task |
| `spawn_subagent` | Spawn an autonomous sub-agent with its own agent profile |
| `get_subagent_result` | Retrieve sub-agent conversation findings |
| `web_search` | Search the web using open-source DuckDuckGo |
| `fetch_url` | Fetch and extract clean text from public web pages |
| `load_skill` | Load instructions from a skill on demand |

---

## 📂 Project Structure

```
kayak/
├── docker-compose.yml
├── Dockerfile                  # Multi-stage production build
├── Dockerfile.sandbox          # Sandbox container base image
├── requirements.txt
├── backend/
│   └── app/
│       ├── main.py             # FastAPI entry point
│       ├── config.py           # Configuration & paths
│       ├── database.py         # Async SQLite DB
│       ├── llm.py              # LiteLLM provider integration
│       ├── models.py           # Pydantic schemas
│       ├── agent/
│       │   ├── engine.py       # ReAct agent loop
│       │   ├── prompt.py       # Dynamic system prompt builder
│       │   ├── sandbox.py      # Docker sandbox container manager
│       │   └── task_manager.py # Background task manager
│       ├── tools/
│       │   ├── registry.py     # Tool loader & schema generator
│       │   └── builtins/       # Core built-in tools
│       ├── skills/
│       │   └── registry.py     # Skill markdown parser
│       ├── agents/
│       │   └── manager.py      # Agent YAML manager
│       └── routes/             # API endpoints
├── frontend/
│   ├── src/
│   │   ├── components/         # ChatView, AgentsView, SkillsView, ToolsView, ToolBuilder, TasksMonitor
│   │   ├── hooks/useSSE.ts     # Real-time event streaming hook
│   │   └── api/client.ts       # REST client
└── data/                       # Persistent storage directory
    ├── kayak.db
    ├── agents/*.yaml
    ├── skills/<name>/SKILL.md
    ├── tools/<name>/tool.py
    └── workspaces/<convo_id>/
```
