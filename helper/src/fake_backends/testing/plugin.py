"""pytest fixtures for the fakes.

pytest loads this plugin automatically once the helper is installed (the
``pytest11`` entry point). A consumer's conftest.py only declares its fakes:

    from fake_backends.testing import fake_fixture

    fake_github = fake_fixture(
        "github", image="ghcr.io/mrsixw/fake-github:0.2.0@sha256:..."
    )

Each fixture made by ``fake_fixture`` starts its fake once per session, resets
it before every test, and yields a ``FakeClient``. When the session ends every
fake is stopped and removed, and if any test failed, each fake's logs are
printed first.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from fake_backends.testing.client import FakeClient
from fake_backends.testing.launch import (
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    Mode,
    RunningFake,
    start_fake,
)

__all__ = ["FakePool", "fake_backends_pool", "fake_fixture"]

_PoolKey = tuple[str, str | None, str | None, str | None, float]


class FakePool:
    """Every fake started this session, keyed by how it was started."""

    def __init__(self) -> None:
        self._running: dict[_PoolKey, RunningFake] = {}

    def get(
        self,
        backend: str,
        *,
        image: str | None,
        mode: Mode | str | None,
        source: str | os.PathLike[str] | None,
        startup_timeout: float,
    ) -> RunningFake:
        key = (
            backend,
            image,
            None if mode is None else str(mode),
            None if source is None else os.fspath(source),
            startup_timeout,
        )
        if key not in self._running:
            self._running[key] = start_fake(
                backend,
                image=image,
                mode=mode,
                source=source,
                startup_timeout=startup_timeout,
            )
        return self._running[key]

    def logs(self) -> dict[str, str]:
        return {fake.backend: fake.logs() for fake in self._running.values()}

    def stop_all(self) -> None:
        for fake in self._running.values():
            fake.stop()
        self._running.clear()


def _report(config: pytest.Config, logs: Mapping[str, str]) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    for backend, text in logs.items():
        title = f"fake-backends: {backend} logs"
        if reporter is not None:
            reporter.write_sep("-", title)
            reporter.write_line(text or "(no output)")
        else:
            print(f"--- {title} ---\n{text}", file=sys.stderr)


@pytest.fixture(scope="session")
def fake_backends_pool(request: pytest.FixtureRequest) -> Iterator[FakePool]:
    pool = FakePool()
    try:
        yield pool
    finally:
        if request.session.testsfailed:
            _report(request.config, pool.logs())
        pool.stop_all()


def fake_fixture(
    backend: str,
    *,
    image: str | None = None,
    mode: Mode | str | None = None,
    source: str | os.PathLike[str] | None = None,
    seed: Mapping[str, Any] | None = None,
    fixture: str | None = None,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
    name: str | None = None,
) -> Any:
    """Make a function-scoped fixture that yields a reset ``FakeClient``.

    Assign it to a module-level name in a conftest.py; that name is the
    fixture's name unless ``name`` is given. After every reset the fake loads
    ``fixture`` (one of its ready-made seeds) or ``seed``, if either is given.
    """
    if seed is not None and fixture is not None:
        raise ValueError("give a fake either seed= or fixture=, not both")

    @pytest.fixture(name=name)
    def _fake(fake_backends_pool: FakePool) -> Iterator[FakeClient]:
        fake = fake_backends_pool.get(
            backend,
            image=image,
            mode=mode,
            source=source,
            startup_timeout=startup_timeout,
        )
        fake.client.reset()
        if fixture is not None:
            fake.client.load_fixture(fixture)
        if seed is not None:
            fake.client.seed(seed)
        yield fake.client

    return _fake
