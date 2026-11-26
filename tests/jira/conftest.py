"""fake-jira in both modes on real servers; every test runs against each.

A test that only makes sense in one mode asks for ``cloud`` or ``server``,
which skip in the other.
"""

import base64
from pathlib import Path

import pytest
import requests

from fake_backends.server.jira import JiraBackend, Mode
from fake_backends.server.jira.api import API_PREFIXES
from tests.conftest import IDENTITY, TOKEN, serve
from tests.openapi import Schemas

SCHEMAS = {
    Mode.CLOUD: Schemas(Path(__file__).parent / "openapi_cloud.json"),
    Mode.SERVER: Schemas(Path(__file__).parent / "openapi_server.json"),
}
EMAIL = f"{IDENTITY}@example.com"


@pytest.fixture(scope="session")
def servers():
    cloud = serve(JiraBackend(Mode.CLOUD))
    server = serve(JiraBackend(Mode.SERVER))
    urls = {Mode.CLOUD: next(cloud), Mode.SERVER: next(server)}
    yield urls
    for running in (cloud, server):
        next(running, None)


@pytest.fixture(params=[Mode.CLOUD, Mode.SERVER], ids=["cloud", "server"])
def mode(request):
    return request.param


@pytest.fixture
def base_url(servers, mode):
    return servers[mode]


@pytest.fixture
def cloud(mode):
    if mode is not Mode.CLOUD:
        pytest.skip("Cloud only")


@pytest.fixture
def server(mode):
    if mode is not Mode.SERVER:
        pytest.skip("Server only")


def basic(user: str, token: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


@pytest.fixture
def auth():
    """What grind sends: Basic with the user's email and the token."""
    return basic(EMAIL, TOKEN)


@pytest.fixture
def api(base_url, mode, auth):
    prefix = API_PREFIXES[mode]

    def call(method, path, **kwargs):
        headers = kwargs.pop("headers", auth)
        return requests.request(
            method, f"{base_url}{prefix}{path}", headers=headers, **kwargs
        )

    return call


@pytest.fixture
def seed(base_url):
    def _seed(document):
        body = {"tokens": {TOKEN: IDENTITY}, **document}
        response = requests.put(f"{base_url}/_fake/state", json=body)
        assert response.status_code == 204, response.text

    return _seed


@pytest.fixture
def valid(mode):
    """Assert a response's body matches the mode's published schema.

    Also checks each issue field against its own component, since an
    IssueBean's fields are free-form in both specs.
    """
    schemas = SCHEMAS[mode]
    components = (
        {
            "status": "StatusDetails",
            "issuetype": "IssueTypeDetails",
            "assignee": "UserDetails",
            "reporter": "UserDetails",
            "priority": "Priority",
            "comment": "PageOfComments",
            "project": "ProjectDetails",
        }
        if mode is Mode.CLOUD
        else {
            "status": "StatusJsonBean",
            "issuetype": "IssueTypeJsonBean",
            "assignee": "UserJsonBean",
            "reporter": "UserJsonBean",
            "priority": "PriorityJsonBean",
            "comment": "CommentsWithPaginationJsonBean",
            "project": "ProjectJsonBean",
        }
    )

    def check_issue(issue):
        problems = []
        for name, value in (issue.get("fields") or {}).items():
            if name in components and value is not None:
                problems += [
                    f"fields.{name}: {p}"
                    for p in schemas.component_errors(components[name], value)
                ]
        return problems

    def _valid(operation, response, status=200):
        assert response.status_code == status, response.text
        body = response.json()
        problems = schemas.errors(operation, body)
        issues = body.get("issues", []) if isinstance(body, dict) else []
        if isinstance(body, dict) and "fields" in body:
            issues = [body]
        for issue in issues:
            problems += check_issue(issue)
        assert problems == []
        return body

    return _valid


@pytest.fixture
def error_valid(mode):
    def _check(response, status):
        assert response.status_code == status, response.text
        body = response.json()
        assert SCHEMAS[mode].component_errors("ErrorCollection", body) == []
        return body

    return _check
