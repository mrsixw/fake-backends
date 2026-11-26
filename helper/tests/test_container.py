"""The launcher against a real image, under docker or Apple's container.

Runs only when FAKE_BACKENDS_TEST_IMAGE names a built core image, as CI's
helper-image job does after building one (make image BACKEND=core locally).
"""

import os

import pytest

from fake_backends.testing import FakeStartError, start_fake
from fake_backends.testing.runtime import detect_runtime

IMAGE_ENV = "FAKE_BACKENDS_TEST_IMAGE"

pytestmark = pytest.mark.skipif(
    not os.environ.get(IMAGE_ENV), reason=f"{IMAGE_ENV} is not set"
)


def test_starts_seeds_and_removes_a_container():
    runtime = detect_runtime()
    fake = start_fake("core", image=os.environ[IMAGE_ENV], mode="container")
    try:
        assert fake.client.healthy()
        fake.client.seed({"tokens": {"t": "smoke"}})
        assert fake.client.requests() == []
        assert runtime.running(fake.container)
    finally:
        fake.stop()
    assert not fake.client.healthy()
    assert not runtime.running(fake.container)


def test_a_container_that_exits_reports_its_logs():
    with pytest.raises(FakeStartError) as caught:
        start_fake("nope", image=os.environ[IMAGE_ENV], mode="container")
    message = str(caught.value)
    assert "exited during startup" in message
    assert "unknown backend 'nope'" in message


def test_a_failed_start_leaves_no_container_behind():
    runtime = detect_runtime()
    with pytest.raises(FakeStartError):
        # No time to answer, so the start fails while the container is up.
        start_fake("core", image=os.environ[IMAGE_ENV], startup_timeout=0)
    listing = runtime.run("ps" if runtime.name == "docker" else "ls", timeout=60)
    assert "fake-core-" not in listing.stdout
