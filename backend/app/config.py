from datetime import datetime
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional

from backend.app.providers import PROVIDERS

logger = logging.getLogger(__name__)

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# Overridable so a test run or a second instance can operate on its own state
# instead of the checkout's data directory.
DATA_DIR = Path(os.getenv("KAYAK_DATA_DIR") or (BASE_DIR / "data")).resolve()
AGENTS_DIR = DATA_DIR / "agents"
SKILLS_DIR = DATA_DIR / "skills"
TOOLS_DIR = DATA_DIR / "tools"
WORKSPACES_DIR = DATA_DIR / "workspaces"
MEMORY_FILE = DATA_DIR / "memories.md"
DB_PATH = DATA_DIR / "kayak.db"
SETTINGS_FILE = DATA_DIR / "settings.json"

# Ensure essential data directories exist
for directory in [DATA_DIR, AGENTS_DIR, SKILLS_DIR, TOOLS_DIR, WORKSPACES_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Keys that are persisted to/from settings.json: what a user sets on the Settings page.
# Everything else is deployment configuration, which belongs to the environment rather
# than to a form.
#
# settings.json is also their *only* source. Kayak is meant to be installed and opened,
# not configured beforehand, so a credential is something you type into the Settings
# page once the app is already running -- never something the user has to place in the
# environment before it will start.
_CREDENTIAL_KEYS = [provider.api_key_setting for provider in PROVIDERS]
#: How much each sandbox may use. None means the recommendation computed from Docker
#: Desktop's resources, which is also what a new install starts with.
_ALLOCATION_KEYS = ["SANDBOX_MEMORY_GB", "SANDBOX_CPUS"]
_PERSISTABLE_KEYS = _CREDENTIAL_KEYS + _ALLOCATION_KEYS


#: Owner read/write only. Anything wider exposes provider keys to every account on
#: the machine, which on a shared or multi-user host is a credential leak.
SECRET_FILE_MODE = 0o600


def _split_csv(raw: str) -> list[str]:
    """Splits a comma-separated environment value into a clean list."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def running_in_container(dockerenv: Path = Path("/.dockerenv")) -> bool:
    """Reports whether this process runs in a container rather than on the Mac itself.

    Args:
        dockerenv: Marker file Docker places in every container.
    """
    return dockerenv.exists()


def local_api_base(port: int) -> str:
    """The OpenAI-compatible base URL of a model server published on a Mac port.

    `host.docker.internal` is how a container reaches the Mac on Docker Desktop;
    it does not resolve on the Mac itself, where the published port is simply
    localhost.
    """
    host = "host.docker.internal" if settings.RUNNING_IN_CONTAINER else "localhost"
    return f"http://{host}:{port}/v1"


def _write_private_json(path: Path, payload: Dict[str, Any]) -> None:
    """Writes JSON to `path` atomically, readable only by the owning user.

    The temporary file is created in the destination directory so that the final
    move is a rename within one filesystem, which is atomic: readers see either the
    old file or the new one, never a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, SECRET_FILE_MODE)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


class Settings:

    def __init__(self):
        # Stamped into the image at build time by the release workflow. A
        # container cannot read the version label of the image it is running
        # from, so the value has to be baked in rather than looked up.
        self.VERSION: str = os.getenv("KAYAK_VERSION", "dev")

        # Kayak grants agents shell and filesystem access, so it binds to loopback
        # unless told otherwise; the image overrides this to 0.0.0.0 because
        # Docker isolates the container's network itself.
        self.HOST: str = os.getenv("KAYAK_HOST", "127.0.0.1")
        self.PORT: int = int(os.getenv("KAYAK_PORT", "8000"))

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
        # Read-only tool calls from one model turn run in parallel, but a model that
        # asks for thirty page fetches at once should not open thirty sockets.
        self.AGENT_MAX_CONCURRENT_TOOLS: int = max(
            1, int(os.getenv("KAYAK_AGENT_MAX_CONCURRENT_TOOLS", "8"))
        )

        # Storage Paths
        self.BASE_DIR: Path = BASE_DIR
        self.DATA_DIR: Path = DATA_DIR
        self.AGENTS_DIR: Path = AGENTS_DIR
        self.SKILLS_DIR: Path = SKILLS_DIR
        self.TOOLS_DIR: Path = TOOLS_DIR
        self.WORKSPACES_DIR: Path = WORKSPACES_DIR
        self.MEMORY_FILE: Path = MEMORY_FILE
        self.DB_PATH: Path = DB_PATH
        self.SETTINGS_FILE: Path = SETTINGS_FILE

        # LLM Providers Configuration. Credentials start empty and are filled in by
        # load_from_file() below: the Settings page is the only way to set them, so a
        # key present in the environment is deliberately ignored rather than quietly
        # becoming a second, invisible source of truth. There is also no global default
        # model: every agent carries its own, and anything that needs a model is acting
        # on behalf of some agent.
        self.OPENAI_API_KEY: str = ""
        self.GEMINI_API_KEY: str = ""
        self.ANTHROPIC_API_KEY: str = ""
        self.HUGGINGFACE_API_KEY: str = ""

        self.SANDBOX_MEMORY_GB: Optional[float] = None
        self.SANDBOX_CPUS: Optional[float] = None

        self.RUNNING_IN_CONTAINER: bool = running_in_container()
        # Each local model server needs a port of its own, and falls back to the
        # neighbouring ones when its port is taken.
        self.VLLM_PORT: int = int(os.getenv("KAYAK_VLLM_PORT", "8001"))
        self.TTS_PORT: int = int(os.getenv("KAYAK_TTS_PORT", "8011"))
        self.STT_PORT: int = int(os.getenv("KAYAK_STT_PORT", "8021"))
        #: Image serving both speech synthesis and transcription -- one runtime, told
        #: at start which it is. Published alongside the server and sandbox images by
        #: the same release, so it tracks `latest` exactly as the sandbox image does.
        self.AUDIO_IMAGE: str = os.getenv(
            "KAYAK_AUDIO_IMAGE", "omaraflak/kayak-audio:latest"
        )

        #: Image every conversation's container runs. Pulled on first use when this
        #: Mac does not have it; the launcher pulls it ahead of time.
        self.DOCKER_SANDBOX_IMAGE: str = os.getenv(
            "KAYAK_SANDBOX_IMAGE", "omaraflak/kayak-sandbox:latest"
        )

        self.load_from_file()

    def load_from_file(self) -> None:
        """Loads what the user set on the Settings page from data/settings.json."""
        if not self.SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(self.SETTINGS_FILE.read_text(encoding="utf-8"))
            for key in _PERSISTABLE_KEYS:
                if key not in data:
                    continue
                value = data[key]
                # A hand-edited allocation that is not a positive number falls back
                # to the recommendation rather than breaking every sandbox start.
                if key in _ALLOCATION_KEYS and not (
                    isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
                ):
                    value = None
                setattr(self, key, value)
        except Exception:
            # Losing this file silently makes a fully configured install look blank,
            # so it is worth a real log line rather than a print to stdout. It is
            # also moved aside: the next save would otherwise overwrite it, and with
            # it every key that could still have been recovered by hand.
            logger.exception("Could not read settings file %s", self.SETTINGS_FILE)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                self.SETTINGS_FILE.rename(
                    self.SETTINGS_FILE.with_name(f"{self.SETTINGS_FILE.name}.unreadable-{stamp}")
                )
            except OSError:
                logger.exception("Could not move the unreadable settings file aside")

    def save_settings(self, updates: Dict[str, Any]) -> None:
        """Persists updated configuration to data/settings.json.

        The file holds provider API keys in plaintext, so it is written through a
        private temporary file and moved into place: the default umask would leave it
        world-readable, and writing in place would truncate every stored key if the
        process died mid-write.

        Args:
            updates: Setting keys and their new values. A credential set to None is
                left alone; an allocation set to None goes back to the recommendation.
        """
        current = {key: getattr(self, key) for key in _PERSISTABLE_KEYS}
        for key, value in updates.items():
            if key not in current or (value is None and key in _CREDENTIAL_KEYS):
                continue
            current[key] = value
            setattr(self, key, value)

        _write_private_json(self.SETTINGS_FILE, current)


settings = Settings()
