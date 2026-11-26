"""The Lockable Resources plugin's root action, as jeeves meets it.

The semantics follow jeeves' AGENTS.md and what plugin 1576.v217259b_f9976
was observed to do in its end-to-end suite:

- every mutator takes one ``resource`` and answers by redirecting to the
  previous page, so only its status code means anything;
- ``unreserve``, ``unlock`` and ``reset`` answer 200 whether or not there was
  anything to do. ``unreserve`` refuses (403) only a caller who neither holds
  the reservation nor is an administrator;
- ``reserve`` answers 423 when the resource is not free, even for the holder;
- ``createResource`` answers 409 when the name exists, ``deleteResource`` 423
  when the resource is not free;
- an unknown resource is a 404 from every mutator.

The ``plugin`` seed setting switches the whole action off (``absent``: every
path is a 404) or removes the two UI-facing mutators (``old``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from http import HTTPStatus

from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import BaseRoute, Route

from fake_backends.server.jenkins import views
from fake_backends.server.jenkins.api import check_crumb, form, not_found, state_of
from fake_backends.server.jenkins.model import PluginState, Resource

__all__ = ["ROOT", "lockable_routes"]

ROOT = "/lockable-resources"
HTTP_LOCKED = 423
_UI_ONLY = frozenset({"createResource", "deleteResource"})

Mutator = Callable[[Request, dict[str, str]], Awaitable[Response]]


def _plugin_missing(request: Request, endpoint: str | None = None) -> bool:
    plugin = state_of(request).plugin
    return plugin is PluginState.ABSENT or (
        plugin is PluginState.OLD and endpoint in _UI_ONLY
    )


def _back(request: Request) -> Response:
    """Forward to the previous page, as the plugin's mutators do."""
    base = f"{request.url.scheme}://{request.url.netloc}"
    return RedirectResponse(
        request.headers.get("referer") or f"{base}{ROOT}/", HTTPStatus.FOUND
    )


def _status(code: int, text: str) -> Response:
    return PlainTextResponse(text, code)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


async def _register(request: Request) -> Response:
    if _plugin_missing(request):
        return not_found()
    return JSONResponse(views.lockable(state_of(request)))


async def _page(request: Request) -> Response:
    if _plugin_missing(request):
        return not_found()
    return PlainTextResponse("Lockable Resources")


def _named(request: Request, fields: dict[str, str]) -> Resource | None:
    return state_of(request).resources.get(fields.get("resource", ""))


async def _reserve(request: Request, fields: dict[str, str]) -> Response:
    resource = _named(request, fields)
    if resource is None:
        return not_found()
    if not resource.free:
        return _status(HTTP_LOCKED, f"{resource.name} is already reserved or locked")
    user = state_of(request).user(request.state.identity)
    resource.reserved_by = user.id
    resource.reserved_by_email = user.email
    resource.reserved_at = _now()
    resource.note = fields.get("reason", resource.note)
    return _back(request)


async def _unreserve(request: Request, fields: dict[str, str]) -> Response:
    resource = _named(request, fields)
    if resource is None:
        return not_found()
    user = state_of(request).user(request.state.identity)
    if resource.reserved_by and resource.reserved_by != user.id and not user.admin:
        return _status(
            HTTPStatus.FORBIDDEN,
            f"{resource.name} is reserved by {resource.reserved_by}",
        )
    # Observed on 1576.v217259b_f9976: 200 even when nobody holds it.
    resource.reserved_by = resource.reserved_by_email = resource.reserved_at = None
    return _back(request)


async def _unlock(request: Request, fields: dict[str, str]) -> Response:
    resource = _named(request, fields)
    if resource is None:
        return not_found()
    resource.locked_by = None
    return _back(request)


async def _reset(request: Request, fields: dict[str, str]) -> Response:
    resource = _named(request, fields)
    if resource is None:
        return not_found()
    resource.locked_by = None
    resource.reserved_by = resource.reserved_by_email = resource.reserved_at = None
    resource.stolen = False
    return _back(request)


async def _create(request: Request, fields: dict[str, str]) -> Response:
    name = fields.get("name", "").strip()
    if not name:
        return _status(HTTPStatus.BAD_REQUEST, "a resource needs a name")
    resources = state_of(request).resources
    if name in resources:
        return _status(HTTPStatus.CONFLICT, f"{name} already exists")
    resources[name] = Resource(
        name=name,
        description=fields.get("description", ""),
        labels=fields.get("labels", "").split(),
    )
    return _back(request)


async def _delete(request: Request, fields: dict[str, str]) -> Response:
    resource = _named(request, fields)
    if resource is None:
        return not_found()
    if not resource.free:
        return _status(HTTP_LOCKED, f"{resource.name} is not free")
    del state_of(request).resources[resource.name]
    return _back(request)


_MUTATORS: dict[str, Mutator] = {
    "reserve": _reserve,
    "unreserve": _unreserve,
    "unlock": _unlock,
    "reset": _reset,
    "createResource": _create,
    "deleteResource": _delete,
}


def _endpoint(name: str, mutator: Mutator) -> Callable[[Request], Awaitable[Response]]:
    async def handle(request: Request) -> Response:
        if _plugin_missing(request, name):
            return not_found()
        if request.method != "POST":
            # Every mutator is @RequirePOST upstream.
            return PlainTextResponse(
                "This URL requires POST", HTTPStatus.METHOD_NOT_ALLOWED
            )
        refused = check_crumb(request)
        if refused is not None:
            return refused
        return await mutator(request, await form(request))

    return handle


def lockable_routes() -> list[BaseRoute]:
    return [
        Route(f"{ROOT}/", _page),
        Route(f"{ROOT}/api/json", _register),
        *(
            Route(f"{ROOT}/{name}", _endpoint(name, mutator), methods=["GET", "POST"])
            for name, mutator in _MUTATORS.items()
        ),
    ]
