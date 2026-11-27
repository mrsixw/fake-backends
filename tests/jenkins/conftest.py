"""fake-jenkins on a real server, with a client shaped like jeeves'."""

import base64

import pytest
import requests

from fake_backends.server.jenkins import JenkinsBackend
from tests.conftest import IDENTITY, TOKEN, serve


@pytest.fixture(scope="session")
def base_url():
    yield from serve(JenkinsBackend())


def basic(user: str, token: str) -> dict[str, str]:
    return {
        "Authorization": "Basic "
        + base64.b64encode(f"{user}:{token}".encode()).decode()
    }


@pytest.fixture
def auth():
    return basic(IDENTITY, TOKEN)


@pytest.fixture
def seed(base_url):
    def _seed(document):
        body = {"tokens": {TOKEN: IDENTITY}, **document}
        response = requests.put(f"{base_url}/_fake/state", json=body)
        assert response.status_code == 204, response.text

    return _seed


class Jenkins:
    """The request shapes of jeeves' JenkinsClient (src/jeeves/jenkins.py).

    Copied from https://github.com/mrsixw/jeeves, 2026-11-26: the paths, the
    tree selectors, the form bodies, and the crumb fetched before a POST.
    """

    def __init__(self, base_url: str, headers: dict[str, str]) -> None:
        self.base = base_url
        self.session = requests.Session()
        self.session.headers.update(headers)
        self.crumb_fetched = False

    def get(self, path, params=None):
        suffix = f"/{path.strip('/')}" if path else ""
        return self.session.get(f"{self.base}{suffix}/api/json", params=params)

    def json(self, path, params=None):
        response = self.get(path, params)
        assert response.status_code == 200, response.text
        return response.json()

    def fetch_crumb(self):
        response = self.session.get(f"{self.base}/crumbIssuer/api/json")
        if response.status_code == 200:
            data = response.json()
            self.session.headers[data["crumbRequestField"]] = data["crumb"]
        self.crumb_fetched = True

    def post(self, path, data=None):
        if not self.crumb_fetched:
            self.fetch_crumb()
        return self.session.post(f"{self.base}/{path.lstrip('/')}", data=data)

    def text(self, path, params=None):
        return self.session.get(f"{self.base}/{path}", params=params)


@pytest.fixture
def jenkins(base_url, auth):
    return Jenkins(base_url, auth)


def job_path(path: str) -> str:
    """jeeves' normalize_jenkins_path."""
    parts = [p for p in path.split("/") if p]
    return "job/" + "/job/".join(parts)
