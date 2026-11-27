"""fake-jira: the JIRA REST calls grind makes, in Cloud or Server mode.

``FAKE_JIRA_MODE`` picks the mode when the image starts: ``cloud`` (REST v3,
ADF bodies, ``nextPageToken`` paging, user search) or ``server`` (REST v2,
plain-text bodies, ``startAt``/``total`` paging). The seed format and every
endpoint are documented in docs/jira.md.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute

from fake_backends.server.backend import Backend, RequestInfo
from fake_backends.server.jira import shapes
from fake_backends.server.jira.api import api_routes
from fake_backends.server.jira.model import JiraState, Mode, parse_state

__all__ = ["MODE_ENV", "JiraBackend", "Mode"]

MODE_ENV = "FAKE_JIRA_MODE"
_BASIC = "basic"
_BEARER = "bearer"


class JiraBackend(Backend):
    name = "jira"

    def __init__(self, mode: Mode | str | None = None) -> None:
        raw = mode if mode is not None else os.environ.get(MODE_ENV, Mode.CLOUD)
        try:
            self.mode = Mode(raw)
        except ValueError:
            raise ValueError(
                f"{MODE_ENV} must be one of {[m.value for m in Mode]}, not {raw!r}"
            ) from None

    def routes(self) -> list[BaseRoute]:
        return api_routes(self.mode)

    def credential(self, request: RequestInfo) -> str | None:
        """The API token or personal access token.

        Cloud takes HTTP Basic, ``email-or-name:api-token``. Server also takes
        a Bearer personal access token. The username part of Basic is not
        checked: the token alone decides who the caller is.
        """
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        scheme = scheme.lower()
        if scheme == _BASIC:
            try:
                decoded = base64.b64decode(value.strip(), validate=True).decode()
            except binascii.Error, UnicodeDecodeError:
                return None
            _, sep, token = decoded.partition(":")
            return token if sep and token else None
        if scheme == _BEARER and self.mode is Mode.SERVER and value.strip():
            return value.strip()
        return None

    def unauthorized(self, request: RequestInfo) -> Response:
        return JSONResponse(
            shapes.error(
                [
                    "You are not authenticated. "
                    "Authentication required to perform this operation."
                ]
            ),
            status_code=HTTPStatus.UNAUTHORIZED,
        )

    def parse_state(self, data: Mapping[str, Any]) -> JiraState:
        return parse_state(data, self.mode)

    def details(self, request: RequestInfo) -> dict[str, Any]:
        return {"jira_mode": self.mode.value}
