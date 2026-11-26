"""Run one fake: ``python -m fake_backends.server``.

Configured by environment, which is how a container is configured:

- ``FAKE_BACKEND``: the backend to serve, a name from the registry (required).
- ``FAKE_HOST``: the address to bind, ``0.0.0.0`` by default.
- ``FAKE_PORT``: the port to bind, ``8080`` by default.
"""

import os
import sys

from fake_backends.server.app import create_app
from fake_backends.server.constants import DEFAULT_HOST, DEFAULT_PORT
from fake_backends.server.registry import UnknownBackendError, load_backend
from fake_backends.server.serve import build_server

__all__ = ["main"]

EXIT_USAGE = 2


def main() -> int:
    name = os.environ.get("FAKE_BACKEND")
    if not name:
        print("FAKE_BACKEND is not set", file=sys.stderr)
        return EXIT_USAGE
    try:
        backend = load_backend(name)
    except UnknownBackendError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    host = os.environ.get("FAKE_HOST", DEFAULT_HOST)
    try:
        port = int(os.environ.get("FAKE_PORT", DEFAULT_PORT))
    except ValueError:
        print("FAKE_PORT must be a port number", file=sys.stderr)
        return EXIT_USAGE
    build_server(create_app(backend), host=host, port=port).run()
    return os.EX_OK


if __name__ == "__main__":
    sys.exit(main())
