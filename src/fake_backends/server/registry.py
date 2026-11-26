"""Every backend an image can be built for, and which of them are released.

This is the single list the Dockerfile's ``BACKEND`` build argument, the
entry point and CI's build matrix all read, so a new fake is added in one
place. ``utils/list_images.py`` turns it into the matrix.
"""

import importlib
from dataclasses import dataclass

from fake_backends.server.backend import Backend

__all__ = [
    "BACKENDS",
    "IMAGE_PREFIX",
    "BackendEntry",
    "UnknownBackendError",
    "image_name",
    "load_backend",
    "published_backends",
]

IMAGE_PREFIX = "fake-"


@dataclass(frozen=True)
class BackendEntry:
    # "package.module:ClassName", imported only when that backend is run.
    target: str
    # Whether a release publishes this backend's image to GHCR.
    published: bool


BACKENDS: dict[str, BackendEntry] = {
    # The core with no fake API of its own: only /healthz and /_fake/. It is
    # built and smoke-tested on every PR so the image itself is proven before
    # any fake exists, and is never published.
    "core": BackendEntry("fake_backends.server.core:CoreBackend", published=False),
}


class UnknownBackendError(LookupError):
    """No backend is registered under the requested name."""


def load_backend(name: str) -> Backend:
    try:
        entry = BACKENDS[name]
    except KeyError:
        known = ", ".join(sorted(BACKENDS))
        raise UnknownBackendError(
            f"unknown backend {name!r}; expected one of: {known}"
        ) from None
    module_name, _, class_name = entry.target.partition(":")
    backend_class = getattr(importlib.import_module(module_name), class_name)
    return backend_class()


def published_backends() -> list[str]:
    return sorted(name for name, entry in BACKENDS.items() if entry.published)


def image_name(backend: str) -> str:
    return f"{IMAGE_PREFIX}{backend}"
