"""GitHub's primary rate limits: buckets per account, as on github.com.

A user's personal tokens all spend from that user's limit, so the buckets are
keyed by the identity a token maps to, not by the token.

REST calls spend from ``core``, REST ``/search/*`` from ``search``, and
GraphQL from ``graphql``. A bucket's window starts with its first request and
resets after ``window_seconds``. Every response carries the ``X-RateLimit-*``
headers of the bucket it spent from.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from fake_backends.server.github.model import Bucket, GitHubState

__all__ = ["RateSnapshot", "peek", "spend"]

# GitHub's own spelling of each bucket in X-RateLimit-Resource.
_RESOURCE_NAMES = {
    Bucket.CORE: "core",
    Bucket.SEARCH: "search",
    Bucket.GRAPHQL: "graphql",
}


@dataclass(frozen=True)
class RateSnapshot:
    bucket: Bucket
    limit: int
    used: int
    reset: int  # epoch seconds
    allowed: bool

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset),
            "X-RateLimit-Used": str(self.used),
            "X-RateLimit-Resource": _RESOURCE_NAMES[self.bucket],
        }


def _window(
    state: GitHubState, identity: str, bucket: Bucket, now: float
) -> tuple[int, int]:
    limit, window = state.limits[bucket]
    used, reset = state.usage.get((identity, bucket), (0, 0))
    if now >= reset:
        used, reset = 0, math.ceil(now) + window
    return used, reset


def spend(
    state: GitHubState,
    identity: str,
    bucket: Bucket,
    cost: int = 1,
    now: float | None = None,
) -> RateSnapshot:
    """Spend ``cost`` from a bucket, unless that would exceed its limit."""
    now = time.time() if now is None else now
    limit, _ = state.limits[bucket]
    used, reset = _window(state, identity, bucket, now)
    allowed = used + cost <= limit
    if allowed:
        used += cost
    state.usage[(identity, bucket)] = (used, reset)
    return RateSnapshot(bucket, limit, used, reset, allowed)


def peek(
    state: GitHubState, identity: str, bucket: Bucket, now: float | None = None
) -> RateSnapshot:
    """A bucket's state, without spending from it."""
    now = time.time() if now is None else now
    limit, _ = state.limits[bucket]
    used, reset = _window(state, identity, bucket, now)
    return RateSnapshot(bucket, limit, used, reset, used < limit)
