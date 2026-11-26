"""A placeholder backend that exists only to exercise the server core.

It fakes no real API. It echoes what it was sent, so a test can see exactly
what reached the handler, and offers one hook action and two detail fields so
the backend extension points have something to call.
"""

import asyncio
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Route

from fake_backends.server import Backend, RequestInfo

__all__ = ["FIXTURE", "TEAPOT_HOOK", "EchoBackend"]

TEAPOT_HOOK = "teapot"
FIXTURE = "hello"
_FIXTURES = Path(__file__).parent / "fixtures"


async def _echo(request: Request) -> JSONResponse:
    store = request.app.state.store
    return JSONResponse(
        {
            "identity": request.state.identity,
            "method": request.method,
            "query": dict(request.query_params),
            "body": (await request.body()).decode(),
            "greeting": store.data.get("greeting"),
        }
    )


async def _public(request: Request) -> JSONResponse:
    return JSONResponse({"identity": request.state.identity})


async def _endless_chunks():
    while True:
        yield b"tick\n"
        await asyncio.sleep(0)


async def _endless(request: Request) -> StreamingResponse:
    """A response that never ends and carries no Content-Length."""
    return StreamingResponse(_endless_chunks(), media_type="text/plain")


class EchoBackend(Backend):
    name = "echo"

    def routes(self) -> list[BaseRoute]:
        return [
            Route("/echo", _echo, methods=["GET", "POST"]),
            Route("/echo/{rest:path}", _echo, methods=["GET", "POST"]),
            Route("/public", _public),
            Route("/endless", _endless),
        ]

    def is_public(self, method: str, path: str) -> bool:
        return path == "/public"

    def parse_state(self, data: Mapping[str, Any]) -> dict[str, Any]:
        greeting = data.get("greeting")
        if greeting is not None and not isinstance(greeting, str):
            raise ValueError("greeting must be a string")
        return dict(data)

    def fixtures(self) -> Mapping[str, Path]:
        return {FIXTURE: _FIXTURES / "hello.yaml"}

    def details(self, request: RequestInfo) -> dict[str, Any]:
        query = parse_qs(request.query_string)
        return {
            "word": query.get("word", [None])[0],
            "fields": query.get("field", []),
        }

    def supports_hook(self, spec: Mapping[str, Any]) -> bool:
        return spec.get("name") == TEAPOT_HOOK

    def hook_action(self, spec: Mapping[str, Any], request: RequestInfo) -> Response:
        return JSONResponse(
            {"hooked": spec.get("payload"), "path": request.path},
            status_code=HTTPStatus.IM_A_TEAPOT,
        )
