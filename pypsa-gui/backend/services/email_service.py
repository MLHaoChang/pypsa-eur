from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OUTBOX: list[dict[str, Any]] = []


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    OUTBOX.append(
        {
            "to": to,
            "subject": subject,
            "body": body,
            "metadata": dict(metadata or {}),
        }
    )


def reset_outbox_for_tests() -> None:
    OUTBOX.clear()
