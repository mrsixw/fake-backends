"""Seeds fake-github refuses, and the ready-made breakfast-fixtures one."""

import pytest
import requests

from tests.github.consumer_queries import BREAKFAST_SEARCH_QUERY, SEARCH_PAGE_SIZE

FIXTURE_TOKEN = "token-mrsixw"
FIXTURE_REPO = "mrsixw/breakfast-fixtures"


@pytest.mark.parametrize(
    ("seed", "message"),
    [
        ({"repositories": []}, "unknown seed keys"),
        ({"repos": [{"name": "x"}]}, "'owner' is required"),
        (
            {"repos": [{"owner": "a", "name": "x"}, {"owner": "A", "name": "X"}]},
            "declared twice",
        ),
        (
            {
                "repos": [
                    {
                        "owner": "a",
                        "name": "x",
                        "pulls": [{"number": 1}],
                        "issues": [{"number": 1}],
                    }
                ]
            },
            "share one sequence",
        ),
        (
            {
                "repos": [
                    {
                        "owner": "a",
                        "name": "x",
                        "pulls": [{"number": 1, "state": "shut"}],
                    }
                ]
            },
            "'shut'",
        ),
        (
            {
                "repos": [
                    {
                        "owner": "a",
                        "name": "x",
                        "pulls": [{"number": 1, "created_at": "soon"}],
                    }
                ]
            },
            "ISO 8601",
        ),
        (
            {
                "repos": [
                    {
                        "owner": "a",
                        "name": "x",
                        "pulls": [
                            {"number": 1, "reviews": [{"author": "b", "state": "LGTM"}]}
                        ],
                    }
                ]
            },
            "'LGTM'",
        ),
        ({"generate": [{"repo": "a/missing", "pulls": 1}]}, "not declared"),
        ({"rate_limits": {"rest": {"limit": 1}}}, "'rest'"),
        (
            {
                "users": [
                    {
                        "login": "a",
                        "contributions": [{"date": "2026-01-01", "kind": "tweet"}],
                    }
                ]
            },
            "'tweet'",
        ),
    ],
)
def test_a_bad_seed_is_refused_with_its_location(base_url, seed, message):
    response = requests.put(f"{base_url}/_fake/state", json=seed)
    assert response.status_code == 400
    assert message in response.json()["error"]


def test_yaml_seeds_parse_dates(base_url, auth):
    body = (
        "repos:\n  - owner: a\n    name: x\n    pulls:\n"
        "      - number: 1\n        created_at: 2026-05-01T09:00:00Z\n"
    )
    response = requests.put(
        f"{base_url}/_fake/state",
        data="tokens:\n  token-alice: alice\n" + body,
        headers={"Content-Type": "application/yaml"},
    )
    assert response.status_code == 204, response.text
    pull = requests.get(f"{base_url}/repos/a/x/pulls/1", headers=auth).json()
    assert pull["created_at"] == "2026-05-01T09:00:00Z"


def test_the_fixture_is_listed(base_url):
    assert requests.get(f"{base_url}/_fake/fixtures").json() == ["breakfast-fixtures"]


@pytest.fixture
def fixture_count(base_url):
    assert (
        requests.post(f"{base_url}/_fake/fixtures/breakfast-fixtures").status_code
        == 204
    )

    def count(*qualifiers):
        query = " ".join([f"repo:{FIXTURE_REPO}", "is:pr", *qualifiers])
        response = requests.post(
            f"{base_url}/graphql",
            json={
                "query": BREAKFAST_SEARCH_QUERY,
                "variables": {
                    "owner": "mrsixw",
                    "searchQuery": query,
                    "cursor": None,
                    "pageSize": SEARCH_PAGE_SIZE,
                    "checkOwner": True,
                },
            },
            headers={"Authorization": f"Bearer {FIXTURE_TOKEN}"},
        )
        return response.json()["data"]["search"]["issueCount"]

    return count


def test_the_fixture_matches_breakfasts_inventory(fixture_count):
    """The counts docs/design/testing.md in breakfast derives from the 8 PRs."""
    assert fixture_count("is:open") == 6
    assert fixture_count() == 8
    assert fixture_count("is:merged") == 1
    assert fixture_count("is:closed", "is:unmerged") == 1
    assert fixture_count("is:open", "draft:false") == 4
    assert fixture_count("is:open", "draft:true") == 2
    assert fixture_count("is:open", "label:bug") == 2
    assert fixture_count("is:open", "author:mrsixw") == 6
    assert fixture_count("is:open", "author:octocat") == 0


def test_the_fixture_repo_is_archived(fixture_count):
    assert fixture_count("is:open", "archived:false") == 0
