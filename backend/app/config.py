import json
import os
from pathlib import Path
from typing import Any, Dict

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# Overridable so a test run or a second instance can operate on its own state
# instead of the checkout's data directory.
DATA_DIR = Path(os.getenv("KAYAK_DATA_DIR") or (BASE_DIR / "data")).resolve()
AGENTS_DIR = DATA_DIR / "agents"
SKILLS_DIR = DATA_DIR / "skills"
TOOLS_DIR = DATA_DIR / "tools"
WORKSPACES_DIR = DATA_DIR / "workspaces"
DB_PATH = DATA_DIR / "kayak.db"
SETTINGS_FILE = DATA_DIR / "settings.json"

# Ensure essential data directories exist
for directory in [DATA_DIR, AGENTS_DIR, SKILLS_DIR, TOOLS_DIR, WORKSPACES_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Keys that are persisted to/from settings.json
_PERSISTABLE_KEYS = [
    "DEFAULT_MODEL",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "HUGGINGFACE_API_KEY",
    "VLLM_API_BASE",
]


def _split_csv(raw: str) -> list[str]:
    """Splits a comma-separated environment value into a clean list."""
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:

    def __init__(self):
        # Server. Kayak grants agents shell and filesystem access, so it binds to
        # loopback unless explicitly told otherwise; the container image overrides
        # this to 0.0.0.0 because Docker isolates the network itself.
        self.HOST: str = os.getenv("KAYAK_HOST", "127.0.0.1")
        self.PORT: int = int(os.getenv("KAYAK_PORT", "8000"))
        self.DEBUG: bool = os.getenv("KAYAK_DEBUG", "false").lower() == "true"

        # Shared secret required on every /api request when set. Left empty the
        # server is unauthenticated, which is only safe on a loopback bind.
        self.AUTH_TOKEN: str = os.getenv("KAYAK_AUTH_TOKEN", "")

        # Browser origins allowed to call the API. Credentialed wildcard CORS is both
        # rejected by browsers and unsafe, so the default is an explicit local list.
        self.CORS_ORIGINS: list[str] = _split_csv(
            os.getenv(
                "KAYAK_CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000,"
                "http://localhost:8000,http://127.0.0.1:8000",
            )
        )

        # Agent execution limits
        self.AGENT_MAX_ITERATIONS: int = int(
            os.getenv("KAYAK_AGENT_MAX_ITERATIONS", "25")
        )
        self.AGENT_MAX_SUBAGENT_DEPTH: int = int(
            os.getenv("KAYAK_AGENT_MAX_SUBAGENT_DEPTH", "3")
        )

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
        self.VLLM_PORT: int = int(os.getenv("KAYAK_VLLM_PORT", "8001"))
        self.VLLM_API_BASE: str = os.getenv(
            "VLLM_API_BASE", f"http://host.docker.internal:{self.VLLM_PORT}/v1"
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
        if not self.SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(self.SETTINGS_FILE.read_text(encoding="utf-8"))
            for key in _PERSISTABLE_KEYS:
                if key in data:
                    setattr(self, key, data[key])
        except Exception as error:
            print(f"Error reading settings file: {error}")

    def save_settings(self, updates: Dict[str, Any]) -> None:
        """Persists updated configuration dictionary to data/settings.json.

        Args:
            updates: Dictionary of setting keys and their new values.
        """
        current = {key: getattr(self, key) for key in _PERSISTABLE_KEYS}
        for key, value in updates.items():
            if value is not None and key in current:
                current[key] = value
                setattr(self, key, value)

        self.SETTINGS_FILE.write_text(
            json.dumps(current, indent=2), encoding="utf-8"
        )


settings = Settings()
