"""Run the echo backend on a real uvicorn server, in a thread, for the session.

The faults that break a connection can only be proved against a real socket,
so every test talks to the server over HTTP with `requests`, exactly as a
consumer's binary would.
"""

import threading
import time
from collections.abc import Iterator

import pytest
import requests

from fake_backends.server import Backend, build_server, create_app
from tests.echo_backend import EchoBackend

LOOPBACK = "127.0.0.1"
# Ask the OS for any free port, so parallel runs never collide.
ANY_FREE_PORT = 0
STARTUP_TIMEOUT_SECONDS = 10
STARTUP_POLL_SECONDS = 0.01
TOKEN = "token-alice"
IDENTITY = "alice"


def serve(backend: Backend) -> Iterator[str]:
    """Run ``backend`` on a real server in a thread, yielding its base URL."""
    server = build_server(create_app(backend), host=LOOPBACK, port=ANY_FREE_PORT)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the fake server did not start")
        time.sleep(STARTUP_POLL_SECONDS)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://{LOOPBACK}:{port}"
    server.should_exit = True
    thread.join(timeout=STARTUP_TIMEOUT_SECONDS)


@pytest.fixture(scope="session")
def base_url():
    """The echo backend; a test package overrides this to serve its own fake."""
    yield from serve(EchoBackend())


@pytest.fixture(autouse=True)
def fresh(base_url):
    """Every test starts from an empty fake with one known token."""
    requests.post(f"{base_url}/_fake/reset").raise_for_status()
    requests.put(
        f"{base_url}/_fake/state", json={"tokens": {TOKEN: IDENTITY}}
    ).raise_for_status()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def add_fault(base_url):
    def _add(rule):
        response = requests.post(f"{base_url}/_fake/faults", json=rule)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return _add


@pytest.fixture
def journal(base_url):
    def _read():
        response = requests.get(f"{base_url}/_fake/requests")
        response.raise_for_status()
        return response.json()

    return _read
