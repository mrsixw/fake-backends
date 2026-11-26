"""Runtime detection, with stand-in CLIs on PATH.

The real runtimes are exercised by test_container.py; these cover what a
consumer sees when none is usable, which CI cannot arrange for real.
"""

import os
import stat

import pytest

from fake_backends.testing import RuntimeUnavailable
from fake_backends.testing.runtime import RuntimeKind, detect_runtime


def _stub(directory, name, exit_code):
    path = directory / name
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    return tmp_path


def test_no_runtime_installed_names_each_candidate(bin_dir):
    with pytest.raises(RuntimeUnavailable) as caught:
        detect_runtime()
    assert "docker: not installed" in str(caught.value)
    assert "container: not installed" in str(caught.value)


def test_a_stopped_docker_says_so(bin_dir):
    _stub(bin_dir, "docker", os.EX_UNAVAILABLE)
    with pytest.raises(RuntimeUnavailable, match="is Docker running"):
        detect_runtime()


def test_a_stopped_apple_container_says_how_to_start_it(bin_dir):
    _stub(bin_dir, "container", os.EX_UNAVAILABLE)
    with pytest.raises(RuntimeUnavailable, match="container system start"):
        detect_runtime()


def test_docker_is_preferred_when_both_answer(bin_dir):
    _stub(bin_dir, "docker", os.EX_OK)
    _stub(bin_dir, "container", os.EX_OK)
    assert detect_runtime().kind is RuntimeKind.DOCKER


def test_a_stopped_docker_falls_back_to_apple_container(bin_dir):
    _stub(bin_dir, "docker", os.EX_UNAVAILABLE)
    _stub(bin_dir, "container", os.EX_OK)
    assert detect_runtime().kind is RuntimeKind.APPLE
