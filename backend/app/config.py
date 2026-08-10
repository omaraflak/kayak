import json
import os
from pathlib import Path
from typing import Any, Dict

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
AGENTS_DIR = DATA_DIR / "agents"
SKILLS_DIR = DATA_DIR / "skills"
TOOLS_DIR = DATA_DIR / "tools"
WORKSPACES_DIR = DATA_DIR / "workspaces"
DB_PATH = DATA_DIR / "kayak.db"
SETTINGS_FILE = DATA_DIR / "settings.json"

# Ensure essential data directories exist
for directory in [DATA_DIR, AGENTS_DIR, SKILLS_DIR, TOOLS_DIR, WORKSPACES_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


class Settings:

    def __init__(self):
        # Server
        self.HOST: str = os.getenv("KAYAK_HOST", "0.0.0.0")
        self.PORT: int = int(os.getenv("KAYAK_PORT", "8000"))
        self.DEBUG: bool = os.getenv("KAYAK_DEBUG", "false").lower() == "true"

        # Storage Paths
        self.BASE_DIR: Path = BASE_DIR
        self.DATA_DIR: Path = DATA_DIR
        self.AGENTS_DIR: Path = AGENTS_DIR
        self.SKILLS_DIR: Path = SKILLS_DIR
        self.TOOLS_DIR: Path = TOOLS_DIR
        self.WORKSPACES_DIR: Path = WORKSPACES_DIR
        self.DB_PATH: Path = DB_PATH
        self.SETTINGS_FILE: Path = SETTINGS_FILE

        # LLM Providers Configuration
        self.DEFAULT_MODEL: str = os.getenv(
            "KAYAK_DEFAULT_MODEL", "gemini/gemini-3.6-flash"
        )
        self.OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
        self.ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
        self.VLLM_API_BASE: str = os.getenv(
            "VLLM_API_BASE", "http://localhost:8000/v1"
        )
        self.OLLAMA_API_BASE: str = os.getenv(
            "OLLAMA_API_BASE", "http://localhost:11434"
        )

        self.HUGGINGFACE_API_KEY: str = os.getenv("HUGGINGFACE_API_KEY", "")

        # Docker Sandbox Configuration
        self.DOCKER_SANDBOX_IMAGE: str = os.getenv(
            "KAYAK_SANDBOX_IMAGE", "kayak-sandbox:latest"
        )
        self.DOCKER_SOCKET_PATH: str = os.getenv(
            "DOCKER_SOCKET_PATH", "/var/run/docker.sock"
        )
        self.SANDBOX_TIMEOUT_SECONDS: int = int(
            os.getenv("SANDBOX_TIMEOUT_SECONDS", "3600")
        )

        self.load_from_file()

    def load_from_file(self) -> None:
        """Loads persistent user credentials and endpoints from data/settings.json."""
        if self.SETTINGS_FILE.exists():
            try:
                data = json.loads(self.SETTINGS_FILE.read_text(encoding="utf-8"))
                if "DEFAULT_MODEL" in data:
                    self.DEFAULT_MODEL = data["DEFAULT_MODEL"]
                if "OPENAI_API_KEY" in data:
                    self.OPENAI_API_KEY = data["OPENAI_API_KEY"]
                if "GEMINI_API_KEY" in data:
                    self.GEMINI_API_KEY = data["GEMINI_API_KEY"]
                if "ANTHROPIC_API_KEY" in data:
                    self.ANTHROPIC_API_KEY = data["ANTHROPIC_API_KEY"]
                if "HUGGINGFACE_API_KEY" in data:
                    self.HUGGINGFACE_API_KEY = data["HUGGINGFACE_API_KEY"]
                if "VLLM_API_BASE" in data:
                    self.VLLM_API_BASE = data["VLLM_API_BASE"]
                if "OLLAMA_API_BASE" in data:
                    self.OLLAMA_API_BASE = data["OLLAMA_API_BASE"]
            except Exception as error:
                print(f"Error reading settings file: {error}")

    def save_settings(self, updates: Dict[str, Any]) -> None:
        """Persists updated configuration dictionary to data/settings.json.

        Args:
            updates: Dictionary of setting keys and their new values.
        """
        current = {
            "DEFAULT_MODEL": self.DEFAULT_MODEL,
            "OPENAI_API_KEY": self.OPENAI_API_KEY,
            "GEMINI_API_KEY": self.GEMINI_API_KEY,
            "ANTHROPIC_API_KEY": self.ANTHROPIC_API_KEY,
            "HUGGINGFACE_API_KEY": self.HUGGINGFACE_API_KEY,
            "VLLM_API_BASE": self.VLLM_API_BASE,
            "OLLAMA_API_BASE": self.OLLAMA_API_BASE,
        }
        for key, value in updates.items():
            if value is not None and key in current:
                current[key] = value
                setattr(self, key, value)

        self.SETTINGS_FILE.write_text(
            json.dumps(current, indent=2), encoding="utf-8"
        )


settings = Settings()
