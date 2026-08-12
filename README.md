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

--

## 🚀 Quick Start

### 1. Configure Environment (Optional)

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
