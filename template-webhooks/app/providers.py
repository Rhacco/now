"""Provider-specific payload adapters."""

from __future__ import annotations

from typing import Any, Callable

Payload = dict[str, Any]
PayloadBuilder = Callable[[str, str, str], Payload]


def _discord(message: str, sender_name: str, icon_url: str) -> Payload:
    """Map neutral fields to Discord's incoming-webhook payload."""
    payload: Payload = {"content": message}
    if sender_name:
        payload["username"] = sender_name
    if icon_url:
        payload["avatar_url"] = icon_url
    return payload


# Add future providers here; transport and orchestration stay unchanged.
_BUILDERS: dict[str, PayloadBuilder] = {
    "discord": _discord,
}


def build_payload(provider: str, message: str, sender_name: str, icon_url: str) -> Payload:
    """Build one provider payload from neutral template fields."""
    name = provider.strip().lower()
    try:
        builder = _BUILDERS[name]
    except KeyError:
        raise ValueError(f"Unsupported provider: {provider!r}") from None
    return builder(message, sender_name, icon_url)
