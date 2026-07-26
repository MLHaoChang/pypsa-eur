from __future__ import annotations

import os
import smtplib
from collections.abc import Callable, Mapping
from email.message import EmailMessage
from typing import Any

from settings import get_settings

OUTBOX: list[dict[str, Any]] = []
_DELIVERY_HOOK: Callable[[dict[str, Any]], None] | None = None


class EmailServiceError(RuntimeError):
    """Base error for SMTP configuration or delivery failures."""


class EmailConfigurationError(EmailServiceError):
    """Raised when the SMTP settings are incomplete."""


class EmailDeliveryError(EmailServiceError):
    """Raised when the SMTP backend rejects or fails delivery."""


def _configured(settings) -> bool:
    return bool(settings.smtp_host.strip() and settings.smtp_from.strip() and settings.smtp_port > 0)


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def get_status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "configured": _configured(settings),
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_from": settings.smtp_from,
        "smtp_user_set": bool(settings.smtp_user),
        "delivery_mode": "outbox" if (_DELIVERY_HOOK is not None or _running_under_pytest()) else "smtp",
    }


def _build_message(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None,
) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    message["To"] = to
    message["From"] = settings.smtp_from
    message["Subject"] = subject
    message.set_content(body_text)
    if body_html is not None:
        message.add_alternative(body_html, subtype="html")
    return message


def _send_via_smtp(message: EmailMessage) -> None:
    settings = get_settings()
    if not _configured(settings):
        raise EmailConfigurationError("SMTP is not configured")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
            if settings.smtp_user:
                client.login(settings.smtp_user, settings.smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError(f"SMTP delivery failed: {exc}") from exc


def send_email(
    *,
    to: str,
    subject: str,
    body_text: str | None = None,
    body_html: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    body: str | None = None,
) -> None:
    text = body_text if body_text is not None else body
    if text is None:
        raise ValueError("body_text is required")

    envelope = {
        "to": to,
        "subject": subject,
        "body": text,
        "body_text": text,
        "body_html": body_html,
        "metadata": dict(metadata or {}),
    }
    OUTBOX.append(envelope)

    if _DELIVERY_HOOK is not None:
        _DELIVERY_HOOK(envelope)
        return

    if _running_under_pytest():
        return

    _send_via_smtp(
        _build_message(
            to=to,
            subject=subject,
            body_text=text,
            body_html=body_html,
        )
    )


def reset_outbox_for_tests() -> None:
    OUTBOX.clear()


def set_delivery_hook_for_tests(
    hook: Callable[[dict[str, Any]], None] | None,
) -> None:
    global _DELIVERY_HOOK
    _DELIVERY_HOOK = hook
