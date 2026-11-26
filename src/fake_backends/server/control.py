"""The control API every fake serves under /_fake, plus /healthz."""

import json
from http import HTTPStatus

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from fake_backends.server.constants import CONTROL_PREFIX, HEALTH_PATH
from fake_backends.server.faults import RuleError
from fake_backends.server.store import SeedError, parse_seed

__all__ = ["control_routes"]


def _error(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=HTTPStatus.BAD_REQUEST)


def _no_content() -> Response:
    return Response(status_code=HTTPStatus.NO_CONTENT)


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _put_state(request: Request) -> Response:
    state = request.app.state
    try:
        seed = parse_seed(await request.body(), request.headers.get("content-type", ""))
        state.backend.validate_state(seed)
        state.store.load(seed)
    except (SeedError, ValueError) as exc:
        return _error(str(exc))
    return _no_content()


async def _reset(request: Request) -> Response:
    state = request.app.state
    state.store.reset()
    state.faults.clear()
    state.journal.clear()
    return _no_content()


async def _add_fault(request: Request) -> JSONResponse:
    state = request.app.state
    try:
        raw = json.loads(await request.body())
        rule = state.faults.add(raw, state.backend.supports_hook)
    except json.JSONDecodeError as exc:
        return _error(f"fault rule is not valid JSON: {exc}")
    except RuleError as exc:
        return _error(str(exc))
    return JSONResponse({"id": rule.id}, status_code=HTTPStatus.CREATED)


async def _list_faults(request: Request) -> JSONResponse:
    return JSONResponse(request.app.state.faults.describe())


async def _clear_faults(request: Request) -> Response:
    request.app.state.faults.clear()
    return _no_content()


async def _delete_fault(request: Request) -> Response:
    if request.app.state.faults.remove(request.path_params["rule_id"]):
        return _no_content()
    return JSONResponse({"error": "no such rule"}, status_code=HTTPStatus.NOT_FOUND)


async def _journal(request: Request) -> JSONResponse:
    return JSONResponse(request.app.state.journal.entries())


def control_routes() -> list[BaseRoute]:
    return [
        Route(HEALTH_PATH, _health),
        Route(f"{CONTROL_PREFIX}/state", _put_state, methods=["PUT"]),
        Route(f"{CONTROL_PREFIX}/reset", _reset, methods=["POST"]),
        Route(f"{CONTROL_PREFIX}/faults", _add_fault, methods=["POST"]),
        Route(f"{CONTROL_PREFIX}/faults", _list_faults, methods=["GET"]),
        Route(f"{CONTROL_PREFIX}/faults", _clear_faults, methods=["DELETE"]),
        Route(
            f"{CONTROL_PREFIX}/faults/{{rule_id:int}}",
            _delete_fault,
            methods=["DELETE"],
        ),
        Route(f"{CONTROL_PREFIX}/requests", _journal, methods=["GET"]),
    ]
