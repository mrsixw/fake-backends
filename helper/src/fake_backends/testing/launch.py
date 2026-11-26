"""Start a fake, wait until it answers, and stop it again.

Two modes:

- ``container`` (the default): run a released image under docker or Apple's
  ``container``. This is what CI uses, and what pins a consumer to an image
  digest.
- ``process``: run the server from a fake-backends checkout with ``uv run``,
  for working on a fake and its consumer side by side. Point
  ``FAKE_BACKENDS_SOURCE`` at the checkout.

``FAKE_BACKENDS_MODE`` picks the mode when the caller does not.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from fake_backends.testing.client import FakeClient
from fake_backends.testing.runtime import ContainerRuntime, detect_runtime

__all__ = [
    "CONTAINER_PORT",
    "DEFAULT_STARTUP_TIMEOUT_SECONDS",
    "MODE_ENV",
    "SOURCE_ENV",
    "FakeStartError",
    "Mode",
    "RunningFake",
    "start_fake",
]

MODE_ENV = "FAKE_BACKENDS_MODE"
SOURCE_ENV = "FAKE_BACKENDS_SOURCE"

# The port every image listens on: the Dockerfile's PORT argument.
CONTAINER_PORT = 8080
# How long a fake gets to answer /healthz. A first container run may include
# booting Apple's VM; the server itself is up in well under a second.
DEFAULT_STARTUP_TIMEOUT_SECONDS = 60
_POLL_INTERVAL_SECONDS = 0.1
_STOP_TIMEOUT_SECONDS = 10
_HEALTH_PATH = "/healthz"
_LOOPBACK = "127.0.0.1"


class Mode(StrEnum):
    CONTAINER = "container"
    PROCESS = "process"


class FakeStartError(RuntimeError):
    """A fake could not be started, with the reason and its logs."""


@dataclass
class RunningFake:
    """One started fake. ``client`` drives it; ``stop()`` removes it."""

    backend: str
    client: FakeClient
    _logs: Callable[[], str] = field(repr=False)
    _stop: Callable[[], None] = field(repr=False)
    # The container's name in container mode; None for a process.
    container: str | None = None

    @property
    def base_url(self) -> str:
        return self.client.base_url

    def logs(self) -> str:
        return self._logs()

    def stop(self) -> None:
        self._stop()


def start_fake(
    backend: str,
    *,
    image: str | None = None,
    mode: Mode | str | None = None,
    source: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
) -> RunningFake:
    """Start ``backend`` and return it once ``/healthz`` answers.

    ``env`` configures the fake, for example ``{"FAKE_JIRA_MODE": "server"}``.
    """
    chosen = _resolve_mode(mode)
    extra = dict(env or {})
    if chosen is Mode.PROCESS:
        return _start_process(
            backend, source=source, env=extra, startup_timeout=startup_timeout
        )
    if image is None:
        raise FakeStartError(
            f"container mode needs an image for {backend!r}; "
            f"pass image=, or set {MODE_ENV}={Mode.PROCESS} to run from source"
        )
    return _start_container(
        backend, image=image, env=extra, startup_timeout=startup_timeout
    )


def _resolve_mode(mode: Mode | str | None) -> Mode:
    raw = mode if mode is not None else os.environ.get(MODE_ENV, Mode.CONTAINER)
    try:
        return Mode(raw)
    except ValueError:
        known = ", ".join(m.value for m in Mode)
        raise FakeStartError(
            f"unknown fake-backends mode {raw!r}; expected one of: {known}"
        ) from None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((_LOOPBACK, 0))
        return sock.getsockname()[1]


def _wait_healthy(
    client: FakeClient, timeout: float, alive: Callable[[], bool]
) -> bool:
    """Poll until healthy, the deadline passes, or ``alive`` says it died."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.healthy():
            return True
        if not alive():
            return False
        time.sleep(_POLL_INTERVAL_SECONDS)
    return client.healthy()


# --- container mode ------------------------------------------------------


def _start_container(
    backend: str, *, image: str, env: dict[str, str], startup_timeout: float
) -> RunningFake:
    runtime = detect_runtime()
    name = f"fake-{backend}-{uuid.uuid4().hex[:8]}"
    host_port = _free_port()
    # Whatever goes wrong, even a timeout or an interrupt, the container must
    # not outlive a failed start: `run` may have created it before failing.
    started = False
    try:
        runtime.start(
            image=image,
            name=name,
            host_port=host_port,
            container_port=CONTAINER_PORT,
            env={**env, "FAKE_BACKEND": backend},
        )
        client = _reach_container(runtime, name, host_port, startup_timeout)
        started = True
    finally:
        if not started:
            runtime.remove(name)
    return RunningFake(
        backend=backend,
        client=client,
        _logs=lambda: runtime.logs(name),
        _stop=lambda: runtime.remove(name),
        container=name,
    )


def _reach_container(
    runtime: ContainerRuntime, name: str, host_port: int, timeout: float
) -> FakeClient:
    """A client for the container, through its published port if that works.

    On Apple's runtime the container's own address is polled alongside it:
    on some Macs that address answers while published ports never do, and
    waiting out the whole timeout first would make every session slow.
    """
    published = FakeClient(f"http://{_LOOPBACK}:{host_port}")
    direct: FakeClient | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if published.healthy():
            return published
        if direct is None:
            address = runtime.container_ip(name)
            if address is not None:
                direct = FakeClient(f"http://{address}:{CONTAINER_PORT}")
        if direct is not None and direct.healthy():
            return direct
        if not runtime.running(name):
            break
        time.sleep(_POLL_INTERVAL_SECONDS)

    logs = runtime.logs(name)
    if not runtime.running(name):
        raise FakeStartError(f"container {name} exited during startup\n{logs}")
    if not runtime.serves_internally(name, CONTAINER_PORT, _HEALTH_PATH):
        raise FakeStartError(
            f"container {name} did not answer {_HEALTH_PATH} within {timeout}s\n{logs}"
        )
    raise FakeStartError(
        f"container {name} answers inside itself, but not on "
        f"{_LOOPBACK}:{host_port}. That is the host's port forwarding, not the "
        f"fake: restart {runtime.name}, or set {MODE_ENV}={Mode.PROCESS}."
    )


# --- process mode --------------------------------------------------------


def _start_process(
    backend: str,
    *,
    source: str | os.PathLike[str] | None,
    env: dict[str, str],
    startup_timeout: float,
) -> RunningFake:
    checkout = source if source is not None else os.environ.get(SOURCE_ENV)
    if not checkout:
        raise FakeStartError(
            f"process mode needs a fake-backends checkout: set {SOURCE_ENV}"
        )
    checkout = Path(checkout)
    if not (checkout / "pyproject.toml").is_file():
        raise FakeStartError(f"{checkout} is not a fake-backends checkout")
    uv = shutil.which("uv")
    if uv is None:
        raise FakeStartError("process mode needs uv on PATH")

    port = _free_port()
    log = tempfile.TemporaryFile(mode="w+")
    process = subprocess.Popen(
        [uv, "run", "--project", str(checkout), "--no-dev", "--quiet"]
        + ["python", "-m", "fake_backends.server"],
        env={
            **os.environ,
            **env,
            "FAKE_BACKEND": backend,
            "FAKE_HOST": _LOOPBACK,
            "FAKE_PORT": str(port),
        },
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    def read_log() -> str:
        log.seek(0)
        return log.read()

    def stop() -> None:
        process.terminate()
        try:
            process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()

    client = FakeClient(f"http://{_LOOPBACK}:{port}")
    # As in container mode, nothing outlives a failed start.
    started = False
    try:
        if not _wait_healthy(
            client, startup_timeout, alive=lambda: process.poll() is None
        ):
            exited = process.poll() is not None
            reason = "exited during startup" if exited else "did not answer in time"
            raise FakeStartError(f"fake {backend!r} {reason}\n{read_log()}")
        started = True
    finally:
        if not started:
            stop()
    return RunningFake(backend=backend, client=client, _logs=read_log, _stop=stop)
