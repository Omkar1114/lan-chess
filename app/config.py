"""Runtime configuration, read exclusively from environment variables.

Nothing in here is secret by default: the secret key is either supplied through
the environment or generated on first start and persisted in the data folder.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"

# Values shipped in .env.example. They are treated as "not configured" so that a
# copied-but-unedited .env never results in a predictable secret or password.
_PLACEHOLDERS = {"", "change-me", "change-me-to-a-long-random-string"}


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(_env_str(name, str(default))))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """All tunables of the application."""

    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: Path = BASE_DIR / "data"
    secret_key: str = ""
    admin_username: str = ""
    admin_password: str = ""
    allow_registration: bool = True
    session_days: int = 30
    site_name: str = "LAN Chess"
    stockfish_path: str = ""
    engine_threads: int = 1
    engine_hash_mb: int = 32
    max_analysis_engines: int = 2
    log_level: str = "info"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lanchess.sqlite3"


def _resolve_secret(data_dir: Path, configured: str) -> str:
    """Return the configured secret, or a persisted auto-generated one."""
    if configured not in _PLACEHOLDERS:
        return configured
    key_file = data_dir / "secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    with contextlib.suppress(OSError):  # e.g. some Windows / bind-mount setups
        key_file.chmod(0o600)
    return key


def _resolve_stockfish(configured: str) -> str:
    """Locate the Stockfish binary; an empty string means 'not available'."""
    candidates = [configured] if configured else []
    candidates += ["stockfish", "/usr/games/stockfish", "/usr/local/bin/stockfish"]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return ""


def load_settings() -> Settings:
    """Build Settings from the process environment."""
    data_dir = Path(_env_str("DATA_DIR") or (BASE_DIR / "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    admin_password = _env_str("ADMIN_PASSWORD")
    if admin_password in _PLACEHOLDERS:
        admin_password = ""

    return Settings(
        host=_env_str("APP_HOST", "0.0.0.0"),
        port=_env_int("APP_PORT", 8080, 1),
        data_dir=data_dir,
        secret_key=_resolve_secret(data_dir, _env_str("SECRET_KEY")),
        admin_username=_env_str("ADMIN_USERNAME"),
        admin_password=admin_password,
        allow_registration=_env_bool("ALLOW_REGISTRATION", True),
        session_days=_env_int("SESSION_DAYS", 30, 1),
        site_name=_env_str("SITE_NAME", "LAN Chess") or "LAN Chess",
        stockfish_path=_resolve_stockfish(_env_str("STOCKFISH_PATH")),
        engine_threads=_env_int("ENGINE_THREADS", 1, 1),
        engine_hash_mb=_env_int("ENGINE_HASH_MB", 32, 1),
        max_analysis_engines=_env_int("MAX_ANALYSIS_ENGINES", 2, 0),
        log_level=_env_str("LOG_LEVEL", "info").lower() or "info",
    )
