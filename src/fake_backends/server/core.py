"""A backend with no API of its own, for proving the image before any fake.

It serves only what every fake serves: ``/healthz`` and the ``/_fake/``
control API. Any other path is a 404 (or the 401 every fake gives an
unauthenticated caller).
"""

from starlette.routing import BaseRoute

from fake_backends.server.backend import Backend

__all__ = ["CoreBackend"]


class CoreBackend(Backend):
    name = "core"

    def routes(self) -> list[BaseRoute]:
        return []
