"""Assemble a fake: the control API, the backend's routes and the fault layer."""

from starlette.applications import Starlette
from starlette.middleware import Middleware

from fake_backends.server.backend import Backend
from fake_backends.server.control import control_routes
from fake_backends.server.faults import FaultBook
from fake_backends.server.journal import Journal
from fake_backends.server.middleware import FaultMiddleware
from fake_backends.server.store import Store

__all__ = ["create_app"]


def create_app(backend: Backend) -> Starlette:
    store = Store(parse=backend.parse_state)
    faults = FaultBook()
    journal = Journal()
    app = Starlette(
        routes=[*control_routes(), *backend.routes()],
        middleware=[
            Middleware(
                FaultMiddleware,
                backend=backend,
                store=store,
                faults=faults,
                journal=journal,
            )
        ],
    )
    app.state.backend = backend
    app.state.store = store
    app.state.faults = faults
    app.state.journal = journal
    return app
