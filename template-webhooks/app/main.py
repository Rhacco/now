"""Orchestrate one configurable webhook smoke-test message."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from config import load_settings
from providers import build_payload
from transport import post_json

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.toml"


def require_webhook_url(secret_env: str) -> str:
    """Read the selected secret without printing it and require HTTPS."""
    url = os.getenv(secret_env, "").strip()
    if not url:
        raise ValueError(f"Environment variable {secret_env} is missing.")

    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Environment variable {secret_env} must contain a valid HTTPS URL.")
    return url


def add_run_context(message: str) -> str:
    """Append compact GitHub run context when available."""
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    run_number = os.getenv("GITHUB_RUN_NUMBER", "").strip()
    if repository and run_number:
        return f"{message} · {repository} · Run {run_number}"
    return message


def main() -> int:
    """Build and send the configured message; return a process exit code."""
    try:
        settings = load_settings(CONFIG_PATH)
        url = require_webhook_url(settings.secret_env)
        payload = build_payload(
            settings.provider,
            add_run_context(settings.message),
            settings.sender_name,
            settings.icon_url,
        )
        status = post_json(url, payload, settings.timeout_seconds)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Webhook test sent successfully (HTTP {status}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
