import os
import secrets
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _secret(name: str, required: bool = True) -> str:
    """Read NAME from the environment, or from the file named by NAME_FILE."""
    value = _env(name)
    file = _env(f"{name}_FILE")
    if not value and file:
        try:
            value = Path(file).read_text().strip()
        except OSError as e:
            raise ConfigError(f"cannot read {name}_FILE ({file}): {e}") from e
    if required and not value:
        raise ConfigError(f"{name} (or {name}_FILE) must be set")
    return value


def _bool(name: str, default: bool) -> bool:
    value = _env(name).lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {value!r}")


def _int(name: str, default: int, minimum: int = 1) -> int:
    value = _env(name)
    try:
        number = int(value) if value else default
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {value!r}") from None
    if number < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return number


@dataclass(frozen=True)
class Settings:
    slink_url: str
    api_key: str
    ui_password: str
    session_secret: str
    port: int
    base_path: str
    workers: int
    max_workers: int
    default_public: bool
    default_collection: str
    data_dir: Path
    max_image_bytes: int


def load_settings() -> Settings:
    slink_url = _env("SLINK_URL").rstrip("/")
    if not slink_url.startswith(("http://", "https://")):
        raise ConfigError("SLINK_URL must be set to your Slink address, e.g. https://i.example.com")

    base_path = "/" + _env("BASE_PATH", "/import").strip("/")
    if base_path == "/":
        base_path = ""

    max_workers = _int("MAX_WORKERS", 16)
    workers = min(_int("WORKERS", 4), max_workers)

    return Settings(
        slink_url=slink_url,
        api_key=_secret("SLINK_API_KEY"),
        ui_password=_secret("UI_PASSWORD"),
        # Without a fixed secret, logins only last until the container restarts
        session_secret=_secret("SESSION_SECRET", required=False) or secrets.token_urlsafe(32),
        port=_int("PORT", 8080),
        base_path=base_path,
        workers=workers,
        max_workers=max_workers,
        default_public=_bool("DEFAULT_PUBLIC", False),
        default_collection=_env("DEFAULT_COLLECTION"),
        data_dir=Path(_env("DATA_DIR", "/data")),
        max_image_bytes=_int("MAX_IMAGE_MB", 50) * 1024 * 1024,
    )
