"""Furnish a real Jenkins like jeeves' end-to-end one, and drive both to the
same state.

The real instance is jeeves' own fixture image (tests/e2e/docker in
https://github.com/mrsixw/jeeves): the same plugins, pinned, and the same
Configuration-as-Code, which declares the user and the lockable resources.
This creates its jobs from jeeves' job XMLs, as jeeves' provision.py does,
then runs one build of e2e-freestyle on each side so there is a finished
build, a left queue item and a log to compare.

``JEEVES_DOCKER_DIR`` points at that directory in a jeeves checkout.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

__all__ = [
    "ADMIN_PASSWORD",
    "ADMIN_USER",
    "GREETING",
    "provision",
    "run_first_build",
]

ADMIN_USER = "jeeves-e2e"
ADMIN_PASSWORD = "jeeves-e2e-password"
GREETING = "conformance"
DOCKER_DIR_ENV = "JEEVES_DOCKER_DIR"
READY_TIMEOUT_SECONDS = 600
BUILD_TIMEOUT_SECONDS = 300
POLL_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 30
_OK = 200
_ALREADY_EXISTS = 400
# The jobs jeeves' fixture creates, in its order: (name, xml, folder).
_JOBS = (
    ("e2e-freestyle", "freestyle.xml", ""),
    ("e2e-pipeline", "pipeline.xml", ""),
    ("e2e-lock-holder", "lock_holder.xml", ""),
    ("e2e-folder", "folder.xml", ""),
    ("nested", "freestyle.xml", "e2e-folder"),
)
FIRST_BUILD = "/job/e2e-freestyle"


class ProvisionError(RuntimeError):
    """The real Jenkins could not be brought to the comparison's state."""


def _wait_ready(base: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            login = requests.get(f"{base}/login", timeout=REQUEST_TIMEOUT_SECONDS)
            if login.status_code == _OK:
                authed = requests.get(
                    f"{base}/api/json",
                    auth=(ADMIN_USER, ADMIN_PASSWORD),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if authed.status_code == _OK:
                    return
        except requests.exceptions.RequestException:
            pass
        time.sleep(POLL_SECONDS)
    raise ProvisionError(f"Jenkins at {base} was not ready in {READY_TIMEOUT_SECONDS}s")


def _crumbed(base: str, auth: tuple[str, str]) -> requests.Session:
    """A session with a crumb and the cookie it was issued against."""
    session = requests.Session()
    session.auth = auth
    crumb = session.get(f"{base}/crumbIssuer/api/json", timeout=REQUEST_TIMEOUT_SECONDS)
    crumb.raise_for_status()
    data = crumb.json()
    session.headers[data["crumbRequestField"]] = data["crumb"]
    return session


def _mint_token(base: str) -> str:
    session = _crumbed(base, (ADMIN_USER, ADMIN_PASSWORD))
    response = session.post(
        f"{base}/me/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
        data={"newTokenName": "conformance"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["data"]["tokenValue"]


def _create_jobs(base: str, token: str, jobs_dir: Path) -> None:
    session = _crumbed(base, (ADMIN_USER, token))
    for name, xml, folder in _JOBS:
        prefix = f"/job/{folder}" if folder else ""
        response = session.post(
            f"{base}{prefix}/createItem",
            params={"name": name},
            data=(jobs_dir / xml).read_bytes(),
            headers={"Content-Type": "application/xml; charset=utf-8"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code not in (_OK, _ALREADY_EXISTS):
            raise ProvisionError(f"creating {name} failed: {response.status_code}")


def _build_and_wait(session: requests.Session, base: str) -> None:
    """Trigger e2e-freestyle with GREETING and poll it to the end, as jeeves does."""
    response = session.post(
        f"{base}{FIRST_BUILD}/buildWithParameters",
        data={"GREETING": GREETING},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    item_url = response.headers["Location"].rstrip("/")
    deadline = time.monotonic() + BUILD_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        item = session.get(
            f"{item_url}/api/json", timeout=REQUEST_TIMEOUT_SECONDS
        ).json()
        number = (item.get("executable") or {}).get("number")
        if number:
            build = session.get(
                f"{base}{FIRST_BUILD}/{number}/api/json",
                timeout=REQUEST_TIMEOUT_SECONDS,
            ).json()
            if not build.get("building"):
                return
        time.sleep(POLL_SECONDS)
    raise ProvisionError(f"the first build did not finish in {BUILD_TIMEOUT_SECONDS}s")


def provision(base: str) -> str:
    """Make the real Jenkins match jeeves-fixtures plus one build; return a token."""
    jobs_dir = Path(os.environ.get(DOCKER_DIR_ENV, "")) / "jobs"
    if not jobs_dir.is_dir():
        raise ProvisionError(f"set {DOCKER_DIR_ENV} to jeeves' tests/e2e/docker")
    base = base.rstrip("/")
    _wait_ready(base)
    token = _mint_token(base)
    _create_jobs(base, token, jobs_dir)
    _build_and_wait(_crumbed(base, (ADMIN_USER, token)), base)
    return token


def run_first_build(fake) -> None:
    """The same build, on the fake. ``fake`` is a conformance.run.Target."""
    _build_and_wait(fake.session, fake.base)
