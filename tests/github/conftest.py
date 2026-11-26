"""fake-github on a real server, plus helpers for seeding, GraphQL and schemas."""

from pathlib import Path

import pytest
import requests

from fake_backends.server.github import GitHubBackend
from tests.conftest import IDENTITY, TOKEN, serve
from tests.openapi import Schemas

SCHEMAS = Schemas(Path(__file__).parent / "openapi_subset.json")


@pytest.fixture(scope="session")
def base_url():
    yield from serve(GitHubBackend())


@pytest.fixture
def seed(base_url):
    """Replace the state; the test token stays valid unless tokens are given."""

    def _seed(document):
        body = {"tokens": {TOKEN: IDENTITY}, **document}
        response = requests.put(f"{base_url}/_fake/state", json=body)
        assert response.status_code == 204, response.text

    return _seed


@pytest.fixture
def gql(base_url, auth):
    def _post(query, variables=None, *, headers=None, path="/graphql"):
        response = requests.post(
            f"{base_url}{path}",
            json={"query": query, "variables": variables or {}},
            headers=auth if headers is None else headers,
        )
        return response

    return _post


def schema_errors(operation: str, body) -> list[str]:
    """Every way ``body`` breaks GitHub's schema for ``operation``.

    ``operation`` is "METHOD /path/template STATUS", as listed for the github
    target in utils/trim_openapi.py.
    """
    return SCHEMAS.errors(operation, body)


def component_errors(name: str, body) -> list[str]:
    return SCHEMAS.component_errors(name, body)
