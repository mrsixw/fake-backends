"""The ASGI layer in front of every fake: journal, faults, then auth.

It is pure ASGI rather than Starlette middleware because two of the fault
actions must control the connection itself: ``drop`` closes it before any
response, and ``truncate`` closes it part-way through the body.

Order per request: identify the caller, ask the backend for details, let the
fault book decide, journal the request, then act. A fault fires before
authentication, so a canned 503 is a 503 whatever the credential.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fake_backends.server.backend import Backend, RequestInfo
from fake_backends.server.constants import (
    CONTROL_PREFIX,
    HEALTH_PATH,
    JSON_CONTENT_TYPE,
    MALFORMED_JSON_BODY,
    TEXT_CONTENT_TYPE,
    TRUNCATE_KEEP_DIVISOR,
)
from fake_backends.server.faults import ActionType, FaultBook, Rule
from fake_backends.server.journal import Journal
from fake_backends.server.serve import abort_connection
from fake_backends.server.store import Store

__all__ = ["FaultMiddleware", "checked_details", "is_control_path"]


def is_control_path(path: str) -> bool:
    return (
        path == HEALTH_PATH
        or path == CONTROL_PREFIX
        or path.startswith(f"{CONTROL_PREFIX}/")
    )


async def _read_body(receive: Receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replaying(body: bytes, receive: Receive) -> Receive:
    """A receive that hands the already-read body to the app, once."""
    delivered = False

    async def replay() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


class _Cut(Exception):
    """Raised from send to stop the app once a truncated body is sent."""


def _declared_length(start: Message) -> int:
    """The Content-Length a response start declares, or 0 if none."""
    for name, value in start.get("headers", []):
        if name.lower() == b"content-length":
            return int(value)
    return 0


def checked_details(details: dict[str, Any], backend_name: str) -> dict[str, Any]:
    """Return ``details`` as plain JSON values, or raise naming the backend.

    The journal is served as JSON, so a detail that cannot be encoded would
    otherwise fail every later read of it, far from the backend that caused it.
    """
    try:
        return json.loads(json.dumps(details))
    except TypeError as exc:
        raise TypeError(
            f"backend {backend_name!r} returned details that are not JSON: {exc}"
        ) from exc


def _canned_response(spec: dict[str, Any]) -> Response:
    body = spec.get("body")
    headers = {str(k): str(v) for k, v in spec.get("headers", {}).items()}
    status = spec["status"]
    if isinstance(body, str):
        return Response(
            body, status_code=status, headers=headers, media_type=TEXT_CONTENT_TYPE
        )
    if body is None:
        return Response(status_code=status, headers=headers)
    return JSONResponse(body, status_code=status, headers=headers)


class FaultMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        backend: Backend,
        store: Store,
        faults: FaultBook,
        journal: Journal,
    ) -> None:
        self.app = app
        self.backend = backend
        self.store = store
        self.faults = faults
        self.journal = journal

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_control_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        body = await _read_body(receive)
        info = RequestInfo(
            method=scope["method"],
            path=scope["path"],
            query_string=scope["query_string"].decode("latin-1"),
            headers={
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope["headers"]
            },
            body=body,
        )
        credential = self.backend.credential(info)
        identity = self.store.tokens.get(credential) if credential else None
        details = checked_details(self.backend.details(info), self.backend.name)
        rule = self.faults.decide(info.method, info.path, identity, details)
        self.journal.record(
            {
                "method": info.method,
                "path": info.path,
                "query": parse_qs(info.query_string, keep_blank_values=True),
                "identity": identity,
                "details": details,
                "fault": rule.id if rule else None,
                "timestamp": time.time(),
            }
        )

        scope.setdefault("state", {})["identity"] = identity
        receive = _replaying(body, receive)

        async def answer(send_to: Send) -> None:
            if identity is None and not self.backend.is_public(info.method, info.path):
                await self.backend.unauthorized(info)(scope, receive, send_to)
            else:
                await self.app(scope, receive, send_to)

        if rule is None:
            await answer(send)
        else:
            await self._apply(rule, info, scope, receive, send, answer)

    async def _apply(
        self,
        rule: Rule,
        info: RequestInfo,
        scope: Scope,
        receive: Receive,
        send: Send,
        answer: Callable[[Send], Awaitable[None]],
    ) -> None:
        action = rule.action
        if action.delay_seconds:
            await asyncio.sleep(action.delay_seconds)
        match action.type:
            case ActionType.RESPOND:
                await _canned_response(dict(action.spec))(scope, receive, send)
            case ActionType.DELAY:
                await answer(send)
            case ActionType.DROP:
                await abort_connection()
            case ActionType.MALFORMED_JSON:
                response = Response(
                    MALFORMED_JSON_BODY,
                    status_code=HTTPStatus.OK,
                    media_type=JSON_CONTENT_TYPE,
                )
                await response(scope, receive, send)
            case ActionType.HOOK:
                response = self.backend.hook_action(action.spec, info)
                await response(scope, receive, send)
            case ActionType.TRUNCATE:
                await self._truncate(action.spec, send, answer)

    async def _truncate(
        self,
        spec: Any,
        send: Send,
        answer: Callable[[Send], Awaitable[None]],
    ) -> None:
        """Forward the real response's headers and only part of its body.

        The body is streamed, never buffered, so an endless response is cut
        as promptly as a short one. With no ``keep_bytes`` the cut comes at
        half the declared Content-Length, or straight after the headers when
        the response declares none.
        """
        remaining: int | None = spec.get("keep_bytes")

        async def cutting(message: Message) -> None:
            nonlocal remaining
            if message["type"] == "http.response.start":
                if remaining is None:
                    remaining = _declared_length(message) // TRUNCATE_KEEP_DIVISOR
                await send(message)
                if remaining == 0:
                    raise _Cut
                return
            if message["type"] != "http.response.body":
                return
            body = message.get("body", b"")[:remaining]
            remaining -= len(body)
            await send({"type": "http.response.body", "body": body, "more_body": True})
            if remaining == 0:
                raise _Cut

        try:
            await answer(cutting)
        except _Cut:
            pass
        await abort_connection()
