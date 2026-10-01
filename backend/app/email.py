"""Outbound email behind a provider abstraction.

Credentials come from the environment, never from source. Delivery is
best-effort by design: a notification email is a convenience, and losing an
ALPR request because a mail provider was down would be a far worse failure
than not sending the mail. Every send path therefore swallows its errors and
logs them, and `send_email` returns whether it succeeded rather than raising.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from app.config import get_settings

logger = logging.getLogger("fleet.email")

SEND_TIMEOUT_SECONDS = 10


@dataclass
class EmailMessage:
    to: list[str]
    subject: str
    body: str
    reply_to: str | None = None


class EmailSender:
    """Base contract. Implementations must not raise on delivery failure."""

    name = "base"

    def send(self, message: EmailMessage) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class NullEmailSender(EmailSender):
    """No provider configured. Logs and reports a non-delivery."""

    name = "none"

    def send(self, message: EmailMessage) -> bool:
        logger.info(
            "Email not sent (no EMAIL_PROVIDER configured): %r to %s",
            message.subject,
            ", ".join(message.to),
        )
        return False


@dataclass
class MemoryEmailSender(EmailSender):
    """Captures messages instead of sending. Used by tests and local runs."""

    name: str = "memory"
    outbox: list[EmailMessage] = field(default_factory=list)

    def send(self, message: EmailMessage) -> bool:
        self.outbox.append(message)
        return True


class HttpApiEmailSender(EmailSender):
    """Generic JSON-over-HTTPS provider (Resend, Postmark, SendGrid-style).

    The payload shape is the common `from / to / subject / text` one. A provider
    that needs a different body can subclass and override `payload`.
    """

    name = "http"

    def __init__(self, api_url: str, api_key: str, sender: str) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.sender = sender

    def payload(self, message: EmailMessage) -> dict:
        body = {
            "from": self.sender,
            "to": message.to,
            "subject": message.subject,
            "text": message.body,
        }
        if message.reply_to:
            body["reply_to"] = message.reply_to
        return body

    def send(self, message: EmailMessage) -> bool:
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(self.payload(message)).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:
                if 200 <= response.status < 300:
                    return True
                logger.warning("Email provider returned HTTP %s for %r", response.status, message.subject)
                return False
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("Email delivery failed for %r: %s", message.subject, exc)
            return False


def build_sender() -> EmailSender:
    """Pick a sender from configuration. Unknown providers degrade to null."""
    settings = get_settings()
    provider = settings.email_provider.lower()

    if not settings.email_enabled:
        return NullEmailSender()
    if provider == "memory":
        return MemoryEmailSender()
    if not (settings.email_api_key and settings.email_from and settings.email_api_url):
        logger.warning(
            "EMAIL_PROVIDER=%s but EMAIL_API_URL / EMAIL_API_KEY / EMAIL_FROM are incomplete; "
            "email notifications are disabled.",
            provider,
        )
        return NullEmailSender()
    return HttpApiEmailSender(settings.email_api_url, settings.email_api_key, settings.email_from)


# Swapped wholesale in tests. Module-level so a test can install a MemoryEmailSender
# without monkeypatching every call site.
_sender: EmailSender | None = None


def get_sender() -> EmailSender:
    global _sender
    if _sender is None:
        _sender = build_sender()
    return _sender


def set_sender(sender: EmailSender | None) -> None:
    global _sender
    _sender = sender


def send_email(to: list[str], subject: str, body: str, reply_to: str | None = None) -> bool:
    """Best-effort send. Never raises; returns True only on confirmed delivery."""
    recipients = [address for address in to if address]
    if not recipients:
        logger.info("Email %r had no recipients; nothing sent.", subject)
        return False
    message = EmailMessage(to=recipients, subject=subject, body=body, reply_to=reply_to)
    try:
        return get_sender().send(message)
    except Exception:
        # A provider that raises anyway must still not take the caller down.
        logger.exception("Unexpected email failure for %r", subject)
        return False
