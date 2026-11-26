from collections.abc import Iterator
from pathlib import Path

import pytest

from fake_backends.testing import FakeClient, RunningFake, start_fake

pytest_plugins = ["pytester"]

# The fake-backends checkout these tests live in: process mode runs its server.
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def running_core() -> Iterator[RunningFake]:
    fake = start_fake("core", mode="process", source=REPO_ROOT)
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture
def core(running_core: RunningFake) -> FakeClient:
    running_core.client.reset()
    return running_core.client
