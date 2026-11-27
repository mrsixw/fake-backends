"""The extension points every fake implements."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute

from fake_backends.server.constants import UNAUTHORIZED_BODY

__all__ = ["Backend", "RequestInfo"]

_CREDENTIAL_SCHEMES = frozenset({"bearer", "token"})


@dataclass(frozen=True)
class RequestInfo:
    """What the core knows about a request before the handler runs."""

    method: str
    path: str
    query_string: str
    headers: Mapping[str, str]
    body: bytes


class Backend(ABC):
    """One faked service.

    A subclass supplies its routes and may override how a request is
    authenticated, which extra details are journaled and matched, and which
    backend-specific fault actions it supports.
    """

    name: str

    @abstractmethod
    def routes(self) -> list[BaseRoute]:
        """The service's own routes."""

    def credential(self, request: RequestInfo) -> str | None:
        """The token presented by ``request``, or None if there is none.

        The default reads ``Authorization: Bearer <t>`` or ``token <t>``.
        """
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() in _CREDENTIAL_SCHEMES and value.strip():
            return value.strip()
        return None

    def is_public(self, method: str, path: str) -> bool:
        """Whether a route answers without a credential."""
        return False

    def unauthorized(self, request: RequestInfo) -> Response:
        """The service's own answer to a missing or unknown credential.

        ``request`` lets a backend answer the two differently, as Jenkins
        does: a login redirect for none, a 401 for a wrong one.
        """
        return JSONResponse(UNAUTHORIZED_BODY, status_code=HTTPStatus.UNAUTHORIZED)

    def details(self, request: RequestInfo) -> dict[str, Any]:
        """Backend-specific facts, journaled and available to fault matching.

        A list value matches a rule whose value is one of its members; any
        other value must be equal.
        """
        return {}

    def parse_state(self, data: Mapping[str, Any]) -> Any:
        """Turn a seed's data into the state the handlers serve.

        ``data`` is every top-level seed key except ``tokens``, and is ``{}``
        after a reset. Raise ValueError if the backend cannot serve it. The
        result is what handlers find in ``request.app.state.store.data``, so
        a backend can parse once and then change it as requests arrive. The
        default keeps the mapping as it is.
        """
        return dict(data)

    def fixtures(self) -> Mapping[str, Path]:
        """Ready-made seed files, by name, for ``POST /_fake/fixtures/{name}``."""
        return {}

    def supports_hook(self, spec: Mapping[str, Any]) -> bool:
        """Whether a ``hook`` fault action with ``spec`` can be answered."""
        return False

    def hook_action(self, spec: Mapping[str, Any], request: RequestInfo) -> Response:
        """Answer a ``hook`` fault action. Called only when supports_hook is."""
        raise NotImplementedError(f"{self.name} has no hook actions")
