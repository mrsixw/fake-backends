"""fake-github on a real server, plus helpers for seeding, GraphQL and schemas."""

import json
from pathlib import Path

import pytest
import requests
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012

from fake_backends.server.github import GitHubBackend
from tests.conftest import IDENTITY, TOKEN, serve

OPENAPI = json.loads((Path(__file__).parent / "openapi_subset.json").read_text())
_COMPONENTS_URI = "urn:github:openapi"
_REGISTRY = Registry().with_resource(
    _COMPONENTS_URI, DRAFT202012.create_resource(OPENAPI)
)


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


def _rewrite_refs(node):
    """Point the description's local $refs at the registered document."""
    if isinstance(node, dict):
        return {
            key: (
                f"{_COMPONENTS_URI}{value}"
                if key == "$ref" and isinstance(value, str) and value.startswith("#/")
                else _rewrite_refs(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_refs(item) for item in node]
    return node


def schema_errors(operation: str, body) -> list[str]:
    """Every way ``body`` breaks GitHub's schema for ``operation``.

    ``operation`` is "METHOD /path/template STATUS", as listed in
    utils/trim_github_openapi.py.
    """
    schema = _rewrite_refs(OPENAPI["operations"][operation])
    validator = Draft202012Validator(schema, registry=_REGISTRY)
    return [
        f"{'/'.join(map(str, error.absolute_path))}: {error.message}"
        for error in validator.iter_errors(body)
    ]


def component_errors(name: str, body) -> list[str]:
    return schema_errors_for({"$ref": f"#/components/schemas/{name}"}, body)


def schema_errors_for(schema, body) -> list[str]:
    validator = Draft202012Validator(_rewrite_refs(schema), registry=_REGISTRY)
    return [error.message for error in validator.iter_errors(body)]
