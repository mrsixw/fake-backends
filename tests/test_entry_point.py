"""The entry point the image runs, driven as a real subprocess."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from fake_backends.server.__main__ import EXIT_USAGE
from fake_backends.server.core import CoreBackend
from fake_backends.server.registry import (
    UnknownBackendError,
    load_backend,
    published_backends,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STARTUP_TIMEOUT = 10
POLL_INTERVAL = 0.05
EXIT_TIMEOUT = 5


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "fake_backends.server"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=EXIT_TIMEOUT,
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_core_backend_is_registered():
    assert isinstance(load_backend("core"), CoreBackend)


def test_an_unknown_backend_names_the_known_ones():
    with pytest.raises(UnknownBackendError, match="core"):
        load_backend("nope")


def test_the_core_image_is_never_published():
    assert "core" not in published_backends()


def test_missing_backend_is_a_usage_error():
    env = {k: v for k, v in os.environ.items() if k != "FAKE_BACKEND"}
    result = subprocess.run(
        [sys.executable, "-m", "fake_backends.server"],
        env=env,
        capture_output=True,
        text=True,
        timeout=EXIT_TIMEOUT,
    )
    assert result.returncode == EXIT_USAGE
    assert "FAKE_BACKEND" in result.stderr


def test_unknown_backend_is_a_usage_error():
    result = _run({"FAKE_BACKEND": "nope"})
    assert result.returncode == EXIT_USAGE
    assert "core" in result.stderr


def test_bad_port_is_a_usage_error():
    result = _run({"FAKE_BACKEND": "core", "FAKE_PORT": "eighty"})
    assert result.returncode == EXIT_USAGE
    assert "FAKE_PORT" in result.stderr


def test_serves_the_control_api_on_the_configured_port():
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "fake_backends.server"],
        env={
            **os.environ,
            "FAKE_BACKEND": "core",
            "FAKE_HOST": "127.0.0.1",
            "FAKE_PORT": str(port),
        },
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while True:
            try:
                health = requests.get(f"{base_url}/healthz")
                break
            except requests.exceptions.ConnectionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(POLL_INTERVAL)
        assert health.json() == {"status": "ok"}
        seeded = requests.put(f"{base_url}/_fake/state", json={"tokens": {}})
        assert seeded.status_code == 204
    finally:
        process.terminate()
        process.wait(timeout=EXIT_TIMEOUT)


def test_list_images_reads_the_registry():
    script = REPO_ROOT / "utils" / "list_images.py"
    every = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True
    )
    assert {"backend": "core", "image": "fake-core"} in json.loads(every.stdout)
    published = subprocess.run(
        [sys.executable, str(script), "--published"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(published.stdout) == [
        {"backend": name, "image": f"fake-{name}"} for name in published_backends()
    ]
