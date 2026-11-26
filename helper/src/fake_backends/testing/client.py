"""A thin client for the control API every fake serves.

Standard library only, so installing the helper adds nothing but pytest to a
consumer's test environment. The endpoints are documented in the repo README.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

__all__ = ["CONTROL_TIMEOUT_SECONDS", "FakeClient", "FakeControlError"]

# How long one control call may take. The fake answers these from memory, so
# anything near this means it is wedged, not busy.
CONTROL_TIMEOUT_SECONDS = 10

_JSON = "application/json"


class FakeControlError(RuntimeError):
    """A control call was refused, for example an invalid fault rule."""

    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"{method} {path} answered {status}: {body}")
        self.status = status
        self.body = body


class FakeClient:
    """Drive one running fake through its ``/_fake/`` control API."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def __repr__(self) -> str:
        return f"FakeClient({self.base_url!r})"

    def url(self, path: str = "/") -> str:
        """The full URL of ``path`` on this fake, for the tool under test."""
        return f"{self.base_url}/{path.lstrip('/')}"

    def healthy(self) -> bool:
        try:
            self._call("GET", "/healthz")
        except (OSError, FakeControlError):
            return False
        return True

    def seed(self, state: Mapping[str, Any]) -> None:
        """Replace the fake's state. ``tokens`` maps each token to an identity."""
        self._call("PUT", "/_fake/state", state)

    def reset(self) -> None:
        """Clear the state, every fault rule and the journal."""
        self._call("POST", "/_fake/reset")

    def add_fault(self, rule: Mapping[str, Any]) -> int:
        """Register a fault rule and return its id."""
        return self._call("POST", "/_fake/faults", rule)["id"]

    def faults(self) -> list[dict[str, Any]]:
        """Every rule, with its ``matched`` and ``fired`` counts."""
        return self._call("GET", "/_fake/faults")

    def remove_fault(self, rule_id: int) -> None:
        self._call("DELETE", f"/_fake/faults/{rule_id}")

    def clear_faults(self) -> None:
        self._call("DELETE", "/_fake/faults")

    def requests(self) -> list[dict[str, Any]]:
        """The journal: every non-control request, in arrival order."""
        return self._call("GET", "/_fake/requests")

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.url(path),
            data=data,
            method=method,
            headers={"Content-Type": _JSON} if data is not None else {},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=CONTROL_TIMEOUT_SECONDS
            ) as response:
                payload = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raise FakeControlError(
                method, path, exc.code, exc.read().decode(errors="replace")
            ) from None
        if status == HTTPStatus.NO_CONTENT or not payload:
            return None
        return json.loads(payload)
