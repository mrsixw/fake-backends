"""fake-jenkins: the Jenkins API jeeves calls, and the Lockable Resources plugin.

Builds move through the queue and run as they are polled, not as time
passes, so a ``--wait`` scenario sees the same sequence every run. Security
settings in the seed reproduce a login redirect, crumb enforcement and both
plugin switches. Everything is documented in docs/jenkins.md.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import quote

from starlette.responses import RedirectResponse, Response
from starlette.routing import BaseRoute

from fake_backends.server.backend import Backend, RequestInfo
from fake_backends.server.jenkins.api import LOGIN_PATH, error_page, jenkins_routes
from fake_backends.server.jenkins.lockable import lockable_routes
from fake_backends.server.jenkins.model import JenkinsState, parse_state

__all__ = ["JEEVES_FIXTURES", "JenkinsBackend"]

_BASIC = "basic"
JEEVES_FIXTURES = "jeeves-fixtures"
_FIXTURES = Path(__file__).parent / "fixtures"


class JenkinsBackend(Backend):
    name = "jenkins"

    def __init__(self) -> None:
        # The last state parsed, for unauthorized(), which has no request
        # context to reach the store through. parse_state runs last in a
        # load, so a state parsed here is the state the store now holds.
        self._state = JenkinsState()

    def routes(self) -> list[BaseRoute]:
        return [*jenkins_routes(), *lockable_routes()]

    def credential(self, request: RequestInfo) -> str | None:
        """The API token from HTTP Basic ``user:token``."""
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != _BASIC:
            return None
        try:
            decoded = base64.b64decode(value.strip(), validate=True).decode()
        except binascii.Error, UnicodeDecodeError:
            return None
        _, sep, token = decoded.partition(":")
        return token if sep and token else None

    def is_public(self, method: str, path: str) -> bool:
        return path == LOGIN_PATH

    def unauthorized(self, request: RequestInfo) -> Response:
        """No credential: the login redirect or a 403. A wrong one: 401."""
        if self.credential(request) is not None:
            return error_page(
                HTTPStatus.UNAUTHORIZED,
                "Invalid password/token",
                **{"WWW-Authenticate": 'Basic realm="Jenkins"'},
            )
        if self._state.security.login_redirect:
            return RedirectResponse(
                f"{LOGIN_PATH}?from={quote(request.path)}", HTTPStatus.FOUND
            )
        return error_page(
            HTTPStatus.FORBIDDEN,
            "Authentication required",
            **{"X-You-Are-Authenticated-As": "anonymous"},
        )

    def fixtures(self) -> Mapping[str, Path]:
        return {JEEVES_FIXTURES: _FIXTURES / f"{JEEVES_FIXTURES}.yaml"}

    def parse_state(self, data: Mapping[str, Any]) -> JenkinsState:
        self._state = parse_state(data)
        return self._state
