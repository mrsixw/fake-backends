"""fake-github: the REST and GraphQL calls breakfast, gh-snitch and grind make.

Served paths:

- GraphQL at ``/graphql`` (api.github.com style) and ``/api/graphql``
  (Enterprise style).
- REST at the root and again under ``/api/v3``.
- Release asset downloads at github.com's web paths, without a token.

The seed format is documented in docs/github.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any

from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount

from fake_backends.server.backend import Backend, RequestInfo
from fake_backends.server.github.graphql_api import (
    GRAPHQL_PATHS,
    graphql_error,
    graphql_routes,
    operation_details,
)
from fake_backends.server.github.model import GitHubState, parse_state
from fake_backends.server.github.rest import (
    DOCS_URL,
    ENTERPRISE_PREFIX,
    download_routes,
    rest_routes,
)

__all__ = ["BREAKFAST_FIXTURES", "GRAPHQL_ERROR_HOOK", "GitHubBackend"]

GRAPHQL_ERROR_HOOK = "graphql_error"
BREAKFAST_FIXTURES = "breakfast-fixtures"
_FIXTURES = Path(__file__).parent / "fixtures"

# What a caller may fetch without a token: release lookups and downloads,
# which the consumers' update checks make.
_PUBLIC = [
    re.compile(
        rf"^({re.escape(ENTERPRISE_PREFIX)})?/repos/[^/]+/[^/]+/releases/latest$"
    ),
    re.compile(r"^/[^/]+/[^/]+/releases/(latest/)?download/"),
]


class GitHubBackend(Backend):
    name = "github"

    def routes(self) -> list[BaseRoute]:
        return [
            *graphql_routes(),
            Mount(ENTERPRISE_PREFIX, routes=rest_routes()),
            *rest_routes(),
            *download_routes(),
        ]

    def unauthorized(self) -> Response:
        return JSONResponse(
            {
                "message": "Bad credentials",
                "documentation_url": DOCS_URL,
                "status": str(HTTPStatus.UNAUTHORIZED.value),
            },
            status_code=HTTPStatus.UNAUTHORIZED,
        )

    def is_public(self, method: str, path: str) -> bool:
        return method == "GET" and any(p.match(path) for p in _PUBLIC)

    def parse_state(self, data: Mapping[str, Any]) -> GitHubState:
        return parse_state(data)

    def fixtures(self) -> Mapping[str, Path]:
        return {BREAKFAST_FIXTURES: _FIXTURES / f"{BREAKFAST_FIXTURES}.yaml"}

    def details(self, request: RequestInfo) -> dict[str, Any]:
        if request.method == "POST" and request.path in GRAPHQL_PATHS:
            return operation_details(request.body)
        return {}

    def supports_hook(self, spec: Mapping[str, Any]) -> bool:
        # The action's own "type" is "hook", so the error's type has its own key.
        return spec.get("name") == GRAPHQL_ERROR_HOOK and isinstance(
            spec.get("error_type"), str
        )

    def hook_action(self, spec: Mapping[str, Any], request: RequestInfo) -> Response:
        """A GraphQL ``errors[]`` entry, such as RATE_LIMITED or NOT_FOUND."""
        kind = spec["error_type"]
        return graphql_error(kind, str(spec.get("message", f"Simulated {kind} error")))
