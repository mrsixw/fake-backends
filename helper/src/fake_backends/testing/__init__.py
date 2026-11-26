"""Start, seed, fault and inspect fake-backends images from a test suite.

This package never imports ``fake_backends.server``: it talks to a fake only
over HTTP, the way the tool under test does. See ADR 0003.
"""

from fake_backends.testing.client import FakeClient, FakeControlError
from fake_backends.testing.launch import (
    FakeStartError,
    Mode,
    RunningFake,
    start_fake,
)
from fake_backends.testing.plugin import fake_fixture
from fake_backends.testing.runtime import RuntimeUnavailable

__all__ = [
    "FakeClient",
    "FakeControlError",
    "FakeStartError",
    "Mode",
    "RunningFake",
    "RuntimeUnavailable",
    "fake_fixture",
    "start_fake",
]
