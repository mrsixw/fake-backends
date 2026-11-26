"""Separate core, search and graphql buckets per account, with GitHub's headers."""

import pytest
import requests

from tests.conftest import IDENTITY, TOKEN
from tests.github.consumer_queries import BREAKFAST_RATE_LIMIT_QUERY

SECOND_TOKEN = "token-bob"
# A second personal token for the same account.
ALICE_SECOND_TOKEN = "token-alice-2"
LIMIT = 3


@pytest.fixture(autouse=True)
def seeded(base_url):
    response = requests.put(
        f"{base_url}/_fake/state",
        json={
            "tokens": {
                TOKEN: IDENTITY,
                SECOND_TOKEN: "bob",
                ALICE_SECOND_TOKEN: IDENTITY,
            },
            "repos": [{"owner": IDENTITY, "name": "r"}],
            "rate_limits": {
                "core": {"limit": LIMIT},
                "search": {"limit": LIMIT},
                "graphql": {"limit": LIMIT},
            },
        },
    )
    assert response.status_code == 204, response.text


def _headers(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _rest(base_url, path="/user", token=TOKEN):
    return requests.get(f"{base_url}{path}", headers=_headers(token))


def _search(base_url, token=TOKEN):
    return _rest(base_url, f"/search/issues?q=user:{IDENTITY}", token)


def _graphql(base_url, token=TOKEN):
    return requests.post(
        f"{base_url}/graphql",
        json={"query": BREAKFAST_RATE_LIMIT_QUERY},
        headers=_headers(token),
    )


def test_every_response_carries_its_buckets_headers(base_url):
    for response, resource in (
        (_rest(base_url), "core"),
        (_search(base_url), "search"),
        (_graphql(base_url), "graphql"),
    ):
        assert response.headers["X-RateLimit-Resource"] == resource
        assert response.headers["X-RateLimit-Limit"] == str(LIMIT)
        assert response.headers["X-RateLimit-Remaining"] == str(LIMIT - 1)
        assert response.headers["X-RateLimit-Used"] == "1"
        assert int(response.headers["X-RateLimit-Reset"]) > 0


def test_buckets_count_down_independently(base_url):
    _rest(base_url)
    _rest(base_url)
    assert _search(base_url).headers["X-RateLimit-Remaining"] == str(LIMIT - 1)
    assert _graphql(base_url).headers["X-RateLimit-Remaining"] == str(LIMIT - 1)
    assert _rest(base_url).headers["X-RateLimit-Remaining"] == "0"


def test_an_exhausted_rest_bucket_is_a_403(base_url):
    for _ in range(LIMIT):
        assert _rest(base_url).status_code == 200
    response = _rest(base_url)
    assert response.status_code == 403
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.json()["message"].startswith("API rate limit exceeded for user ID")


def test_an_exhausted_search_bucket_leaves_core_alone(base_url):
    for _ in range(LIMIT):
        _search(base_url)
    assert _search(base_url).status_code == 403
    assert _rest(base_url).status_code == 200


def test_an_exhausted_graphql_bucket_is_rate_limited(base_url):
    for _ in range(LIMIT):
        assert "errors" not in _graphql(base_url).json()
    response = _graphql(base_url)
    assert response.json()["errors"][0]["type"] == "RATE_LIMITED"
    assert response.headers["X-RateLimit-Remaining"] == "0"


def test_each_account_has_its_own_buckets(base_url):
    for _ in range(LIMIT):
        _rest(base_url)
    assert _rest(base_url).status_code == 403
    assert _rest(base_url, token=SECOND_TOKEN).status_code == 200


def test_tokens_of_one_account_share_its_buckets(base_url):
    """GitHub limits a user, not each of their personal tokens."""
    _rest(base_url)
    response = _rest(base_url, token=ALICE_SECOND_TOKEN)
    assert response.headers["X-RateLimit-Used"] == "2"


def test_graphql_rate_limit_field_counts_down(base_url):
    first = _graphql(base_url).json()["data"]["rateLimit"]
    second = _graphql(base_url).json()["data"]["rateLimit"]
    assert (first["remaining"], second["remaining"]) == (LIMIT - 1, LIMIT - 2)
    assert first["resetAt"] == second["resetAt"]


def test_reset_refills_every_bucket(base_url):
    for _ in range(LIMIT):
        _rest(base_url)
    requests.post(f"{base_url}/_fake/reset")
    requests.put(
        f"{base_url}/_fake/state", json={"tokens": {TOKEN: IDENTITY}}
    ).raise_for_status()
    assert _rest(base_url).status_code == 200


def test_an_unauthenticated_release_check_spends_nothing(base_url):
    response = requests.get(f"{base_url}/repos/{IDENTITY}/r/releases/latest")
    assert response.status_code == 404
    assert "X-RateLimit-Remaining" not in response.headers
