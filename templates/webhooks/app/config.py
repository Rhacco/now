"""Load and validate public webhook template settings."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated neutral settings used by the send pipeline."""

    provider: str
    secret_env: str
    timeout_seconds: int
    sender_name: str
    icon_url: str
    message: str


def _section(data: dict[str, object], name: str) -> dict[str, object]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"settings.toml: [{name}] section is required.")
    return value


def load_settings(path: Path) -> Settings:
    """Load settings.toml and fail early on invalid public configuration."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not load settings.toml: {exc}") from None

    webhook = _section(data, "webhook")
    sender = _section(data, "sender")
    message = _section(data, "message")

    provider = webhook.get("provider")
    secret_env = webhook.get("secret_env")
    timeout = webhook.get("timeout_seconds")
    sender_name = sender.get("name", "")
    icon_url = sender.get("icon_url", "")
    text = message.get("text")

    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("settings.toml: webhook.provider must be a non-empty string.")
    if not isinstance(secret_env, str) or not _ENV_NAME.fullmatch(secret_env):
        raise ValueError("settings.toml: webhook.secret_env must be a valid environment variable name.")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 60:
        raise ValueError("settings.toml: webhook.timeout_seconds must be an integer from 1 to 60.")
    if not isinstance(sender_name, str) or not isinstance(icon_url, str):
        raise ValueError("settings.toml: sender fields must be strings.")
    if icon_url:
        parsed_icon = urlsplit(icon_url)
        if parsed_icon.scheme != "https" or not parsed_icon.netloc:
            raise ValueError("settings.toml: sender.icon_url must be empty or a valid HTTPS URL.")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("settings.toml: message.text must be a non-empty string.")

    return Settings(
        provider=provider.strip(),
        secret_env=secret_env,
        timeout_seconds=timeout,
        sender_name=sender_name.strip(),
        icon_url=icon_url.strip(),
        message=text.strip(),
    )
