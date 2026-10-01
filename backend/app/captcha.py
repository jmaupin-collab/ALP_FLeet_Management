"""CAPTCHA verification for the public request form.

Written against the Cloudflare Turnstile / reCAPTCHA siteverify shape, which
both use: POST form-encoded `secret` and `response`, get back JSON with a
`success` boolean. Swapping providers is a configuration change, not a code
change.

Verification is off by default so the feature works in development and in
tests. When it is on, a failed check rejects the submission — unlike email,
this one is a gate, not a courtesy.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from app.config import get_settings

logger = logging.getLogger("fleet.captcha")

VERIFY_TIMEOUT_SECONDS = 10
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class CaptchaVerifier:
    """Contract for a verifier. Returns True when the token is acceptable."""

    enabled = False

    def verify(self, token: str | None, remote_ip: str | None = None) -> bool:  # pragma: no cover
        raise NotImplementedError


class DisabledCaptchaVerifier(CaptchaVerifier):
    """No provider configured. Every submission passes."""

    enabled = False

    def verify(self, token: str | None, remote_ip: str | None = None) -> bool:
        return True


class SiteverifyCaptchaVerifier(CaptchaVerifier):
    enabled = True

    def __init__(self, secret: str, verify_url: str) -> None:
        self.secret = secret
        self.verify_url = verify_url

    def verify(self, token: str | None, remote_ip: str | None = None) -> bool:
        if not token:
            return False
        fields = {"secret": self.secret, "response": token}
        if remote_ip:
            fields["remoteip"] = remote_ip
        data = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(self.verify_url, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=VERIFY_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            # The provider being unreachable must not become an open door.
            logger.warning("CAPTCHA verification could not be completed: %s", exc)
            return False
        if not payload.get("success"):
            logger.info("CAPTCHA rejected a submission: %s", payload.get("error-codes"))
            return False
        return True


def build_verifier() -> CaptchaVerifier:
    settings = get_settings()
    if not settings.captcha_enabled:
        return DisabledCaptchaVerifier()
    if not settings.captcha_secret_key:
        logger.warning(
            "CAPTCHA_PROVIDER=%s but CAPTCHA_SECRET_KEY is unset; verification is disabled.",
            settings.captcha_provider,
        )
        return DisabledCaptchaVerifier()
    return SiteverifyCaptchaVerifier(
        settings.captcha_secret_key,
        settings.captcha_verify_url or TURNSTILE_VERIFY_URL,
    )


_verifier: CaptchaVerifier | None = None


def get_verifier() -> CaptchaVerifier:
    global _verifier
    if _verifier is None:
        _verifier = build_verifier()
    return _verifier


def set_verifier(verifier: CaptchaVerifier | None) -> None:
    global _verifier
    _verifier = verifier
