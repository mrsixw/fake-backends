"""Starting fakes: the errors a consumer sees when something is wrong."""

import pytest

from fake_backends.testing import FakeStartError, start_fake
from fake_backends.testing.launch import MODE_ENV, SOURCE_ENV
from tests.conftest import REPO_ROOT

QUICK_TIMEOUT = 30


def test_an_unknown_mode_names_the_known_ones():
    with pytest.raises(FakeStartError, match="container, process"):
        start_fake("core", mode="vm")


def test_the_mode_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "vm")
    with pytest.raises(FakeStartError, match="'vm'"):
        start_fake("core")


def test_container_mode_needs_an_image():
    with pytest.raises(FakeStartError, match="needs an image"):
        start_fake("core", mode="container")


def test_process_mode_needs_a_checkout(monkeypatch):
    monkeypatch.delenv(SOURCE_ENV, raising=False)
    with pytest.raises(FakeStartError, match=SOURCE_ENV):
        start_fake("core", mode="process")


def test_process_mode_rejects_a_directory_that_is_not_a_checkout(tmp_path):
    with pytest.raises(FakeStartError, match="not a fake-backends checkout"):
        start_fake("core", mode="process", source=tmp_path)


def test_a_fake_that_dies_on_startup_reports_its_output():
    with pytest.raises(FakeStartError) as caught:
        start_fake(
            "nope", mode="process", source=REPO_ROOT, startup_timeout=QUICK_TIMEOUT
        )
    message = str(caught.value)
    assert "exited during startup" in message
    assert "unknown backend 'nope'" in message


def test_process_mode_reads_the_checkout_from_the_environment(monkeypatch):
    monkeypatch.setenv(SOURCE_ENV, str(REPO_ROOT))
    fake = start_fake("core", mode="process")
    try:
        assert fake.client.healthy()
    finally:
        fake.stop()
    assert not fake.client.healthy()
