"""Generic JSON-over-HTTPS webhook transport."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request


def post_json(url: str, payload: dict[str, Any], timeout_seconds: int) -> int:
    """POST one JSON payload and return the successful HTTP status."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "webhook-template",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            status = response.status
    except error.HTTPError as exc:
        raise RuntimeError(f"Webhook returned HTTP {exc.code}.") from None
    except error.URLError as exc:
        raise RuntimeError(f"Webhook request failed: {exc.reason}.") from None

    if not 200 <= status < 300:
        raise RuntimeError(f"Webhook returned HTTP {status}.")
    return status
