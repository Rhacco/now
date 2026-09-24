"""Load public settings, build a payload, and send one test webhook."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from providers import build_payload
from transport import post_json

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.toml"
WEBHOOK_ENV = "WEBHOOK_URL"


def load_settings() -> dict[str, object]:
    """Load and validate the small public template configuration."""
    try:
        with CONFIG_PATH.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not load settings.toml: {exc}") from None

    provider = data.get("provider")
    message = data.get("message")
    timeout = data.get("timeout_seconds")

    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("settings.toml: provider must be a non-empty string.")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("settings.toml: message must be a non-empty string.")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 60:
        raise ValueError("settings.toml: timeout_seconds must be an integer from 1 to 60.")
    return data


def require_webhook_url() -> str:
    """Read the secret webhook URL without printing it and require HTTPS."""
    url = os.getenv(WEBHOOK_ENV, "").strip()
    if not url:
        raise ValueError(f"Environment variable {WEBHOOK_ENV} is missing.")

    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Environment variable {WEBHOOK_ENV} must contain a valid HTTPS URL.")
    return url


def add_run_context(message: str) -> str:
    """Add compact GitHub run context when available."""
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    run_number = os.getenv("GITHUB_RUN_NUMBER", "").strip()
    if repository and run_number:
        return f"{message} · {repository} · Run {run_number}"
    return message


def main() -> int:
    """Run the template's single send path and return a process exit code."""
    try:
        settings = load_settings()
        url = require_webhook_url()
        message = add_run_context(str(settings["message"]))
        payload = build_payload(str(settings["provider"]), message)
        status = post_json(url, payload, int(settings["timeout_seconds"]))
    except (ValueError, RuntimeError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(f"Webhook-Test erfolgreich gesendet (HTTP {status}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
