"""Run a fake on uvicorn, with the ability to break a connection on purpose.

ASGI has no way for an application to close its connection without
answering, and that is exactly what the ``drop`` and ``truncate`` faults
need. The server therefore runs on a thin subclass of uvicorn's h11 protocol
that publishes each connection's transport in a context variable.

uvicorn creates a request's task from within ``data_received``, and an
asyncio task inherits the context it was created in. Setting the variable
around ``data_received`` is enough for the request to see its own
transport. ``data_received`` is the standard asyncio.Protocol method, so no
uvicorn internals are patched.
"""

import asyncio
from contextvars import ContextVar

import uvicorn
from starlette.types import ASGIApp
from uvicorn.protocols.http.h11_impl import H11Protocol

__all__ = [
    "AbortableH11Protocol",
    "ConnectionAbortUnavailable",
    "abort_connection",
    "build_server",
]

_CURRENT_TRANSPORT: ContextVar[asyncio.Transport | None] = ContextVar(
    "fake_backends_transport", default=None
)


class ConnectionAbortUnavailable(RuntimeError):
    """The fake is not running on the protocol that can break connections."""


class AbortableH11Protocol(H11Protocol):
    """uvicorn's h11 protocol, publishing the transport to the request task."""

    def data_received(self, data: bytes) -> None:
        token = _CURRENT_TRANSPORT.set(self.transport)
        try:
            super().data_received(data)
        finally:
            _CURRENT_TRANSPORT.reset(token)


async def abort_connection() -> None:
    """Close the current request's connection at once, sending nothing more."""
    transport = _CURRENT_TRANSPORT.get()
    if transport is None:
        raise ConnectionAbortUnavailable(
            "drop and truncate faults need the server from build_server()"
        )
    transport.abort()
    # Let connection_lost run, so uvicorn sees the client as gone and does not
    # try to answer on the dead connection.
    await asyncio.sleep(0)


def build_server(app: ASGIApp, *, host: str, port: int) -> uvicorn.Server:
    """A uvicorn server for ``app`` that supports every fault action."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        http=AbortableH11Protocol,
        lifespan="off",
        # A fresh context per task would hide the transport from the request.
        reset_contextvars=False,
        log_level="warning",
    )
    return uvicorn.Server(config)
