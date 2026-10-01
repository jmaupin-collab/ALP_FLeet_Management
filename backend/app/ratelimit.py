"""Sliding-window rate limiting for unauthenticated endpoints.

The store is deliberately behind a small interface. The default keeps hit
timestamps in process memory, which is correct for a single worker and is the
honest default for this deployment; a multi-worker or multi-instance rollout
should register a Redis-backed store instead of changing any call site.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from time import monotonic


class RateLimitStore:
    """Contract for a hit store. Implementations must be safe to share."""

    def hit(self, key: str, limit: int, window_seconds: float) -> tuple[bool, float]:
        """Record an attempt. Returns (allowed, seconds_until_retry)."""
        raise NotImplementedError  # pragma: no cover - interface

    def reset(self) -> None:
        raise NotImplementedError  # pragma: no cover - interface


class InMemoryRateLimitStore(RateLimitStore):
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: float) -> tuple[bool, float]:
        # monotonic, not wall clock, so a clock adjustment cannot widen or
        # collapse the window under an attacker's feet.
        now = monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                # Refused attempts are not recorded, so a client hammering the
                # endpoint cannot push its own window out indefinitely.
                return False, max(0.0, bucket[0] + window_seconds - now)
            bucket.append(now)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_store: RateLimitStore = InMemoryRateLimitStore()


def get_store() -> RateLimitStore:
    return _store


def set_store(store: RateLimitStore) -> None:
    global _store
    _store = store


def check_rate_limit(key: str, limit: int, window_seconds: float) -> tuple[bool, float]:
    """A limit of zero or less disables the check entirely."""
    if limit <= 0:
        return True, 0.0
    return get_store().hit(key, limit, window_seconds)


def client_ip(request) -> str:
    """Best-effort caller identity for rate limiting.

    X-Forwarded-For is only meaningful behind a proxy that overwrites it, so the
    left-most entry is used and treated as a hint, not as proof of identity.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:45]
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()[:45]
    return (request.client.host if request.client else "unknown")[:45]
