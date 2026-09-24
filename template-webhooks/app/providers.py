"""Payload adapters for supported webhook receivers."""

from __future__ import annotations

from typing import Any, Callable

Payload = dict[str, Any]
PayloadBuilder = Callable[[str], Payload]


def _discord(message: str) -> Payload:
    """Build Discord's minimal incoming-webhook payload."""
    return {"content": message}


# Add future receivers here without changing transport or orchestration code.
_BUILDERS: dict[str, PayloadBuilder] = {
    "discord": _discord,
}


def build_payload(provider: str, message: str) -> Payload:
    """Build the JSON payload for the selected receiver."""
    name = provider.strip().lower()
    try:
        builder = _BUILDERS[name]
    except KeyError:
        raise ValueError(f"Unsupported provider: {provider!r}") from None
    return builder(message)
