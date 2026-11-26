"""The search qualifiers, through REST /search/issues (GraphQL shares the code)."""

import pytest
import requests

from tests.github.consumer_queries import BREAKFAST_SEARCH_QUERY, SEARCH_SLICE_TARGET
from tests.github.seed import OLD_REPO, ORG, OTHER_USER, REPO, SEED

ALL = {(REPO, n) for n in range(1, 7)} | {(OLD_REPO, 1)}


@pytest.fixture
def found(base_url, auth, seed):
    seed(SEED)

    def _search(q, **params):
        response = requests.get(
            f"{base_url}/search/issues",
            params={"q": q, "per_page": 100, **params},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        return [
            (item["repository_url"].rsplit("/", 1)[-1], item["number"])
            for item in response.json()["items"]
        ]

    return _search


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (f"org:{ORG}", ALL),
        (f"user:{ORG}", ALL),
        (f"repo:{ORG}/{REPO}", ALL - {(OLD_REPO, 1)}),
        (f"repo:{ORG}/{REPO} repo:{ORG}/{OLD_REPO}", ALL),
        ("org:nobody", set()),
        (
            f"org:{ORG} is:pr",
            {(REPO, 1), (REPO, 2), (REPO, 3), (REPO, 4), (OLD_REPO, 1)},
        ),
        (f"org:{ORG} type:issue", {(REPO, 5), (REPO, 6)}),
        (f"org:{ORG} is:pr is:open archived:false", {(REPO, 1), (REPO, 2)}),
        (f"org:{ORG} is:pr archived:true", {(OLD_REPO, 1)}),
        (f"org:{ORG} is:pr is:merged", {(REPO, 3)}),
        (f"org:{ORG} is:pr is:closed is:unmerged", {(REPO, 4)}),
        (
            f"org:{ORG} is:pr is:unmerged archived:false",
            {(REPO, 1), (REPO, 2), (REPO, 4)},
        ),
        (f"org:{ORG} state:closed", {(REPO, 3), (REPO, 4), (REPO, 6)}),
        (f"org:{ORG} draft:true", {(REPO, 2)}),
        (f"org:{ORG} author:{OTHER_USER}", {(REPO, 2), (REPO, 4), (REPO, 5)}),
        (f"org:{ORG} assignee:@me", {(REPO, 1), (REPO, 5)}),
        (f"org:{ORG} label:bug", {(REPO, 5)}),
        (f"org:{ORG} squeak", {(REPO, 5)}),
        (f"org:{ORG} wet", {(REPO, 5)}),  # the body is searched too
        (f'org:{ORG} "rework cogs"', {(REPO, 2)}),
        (f"org:{ORG} created:2026-03-01..2026-03-02", {(REPO, 1), (REPO, 2)}),
        (f"org:{ORG} created:2026-03-01T10:00:00Z..2026-03-01T10:00:00Z", {(REPO, 1)}),
        (f"org:{ORG} created:>=2026-03-02", {(REPO, 2), (REPO, 5)}),
        (f"org:{ORG} created:>2026-03-02", {(REPO, 5)}),
        (f"org:{ORG} created:<2026-01-15", {(OLD_REPO, 1), (REPO, 6)}),
        (f"org:{ORG} created:<=2026-01-15", {(OLD_REPO, 1), (REPO, 4), (REPO, 6)}),
        (f"org:{ORG} created:2026-03-01..*", {(REPO, 1), (REPO, 2), (REPO, 5)}),
        (f"org:{ORG} updated:2026-03-05", {(REPO, 1)}),
    ],
)
def test_qualifiers(found, query, expected):
    assert set(found(query)) == expected


def test_newest_first_by_default(found):
    assert found(f"repo:{ORG}/{REPO} is:pr") == [
        (REPO, 2),
        (REPO, 1),
        (REPO, 3),
        (REPO, 4),
    ]


def test_sort_created_asc(found):
    assert found(f"repo:{ORG}/{REPO} is:pr sort:created-asc") == [
        (REPO, 4),
        (REPO, 3),
        (REPO, 1),
        (REPO, 2),
    ]


def test_rest_sort_and_order_override_the_qualifier(found):
    """grind sends sort=updated&order=desc."""
    assert found(
        f"repo:{ORG}/{REPO} is:pr sort:created-asc", sort="updated", order="desc"
    )[0] == (
        REPO,
        1,
    )


def test_an_invalid_date_is_a_422(base_url, auth, seed):
    seed(SEED)
    response = requests.get(
        f"{base_url}/search/issues", params={"q": "created:yesterday"}, headers=auth
    )
    assert response.status_code == 422


def _issue_count(base_url, auth, query):
    response = requests.post(
        f"{base_url}/graphql",
        json={
            "query": BREAKFAST_SEARCH_QUERY,
            "variables": {
                "owner": ORG,
                "searchQuery": query,
                "cursor": None,
                "pageSize": 1,
                "checkOwner": False,
            },
        },
        headers=auth,
    )
    return response.json()["data"]["search"]["issueCount"]


@pytest.mark.parametrize("count", [SEARCH_SLICE_TARGET, SEARCH_SLICE_TARGET + 1])
def test_generated_prs_reach_breakfasts_slicing_threshold(base_url, auth, seed, count):
    """breakfast slices a search by created: once issueCount passes 300.

    At the threshold it does not; one above, it does. The created: halves of
    the range must account for every PR, or slicing would lose some.
    """
    seed(
        {
            "orgs": [{"login": ORG}],
            "repos": [{"owner": ORG, "name": "big"}],
            "generate": [
                {"repo": f"{ORG}/big", "pulls": count, "start": "2026-01-01T00:00:00Z"}
            ],
        }
    )
    total = _issue_count(base_url, auth, f"org:{ORG} is:pr")
    assert total == count
    assert (total > SEARCH_SLICE_TARGET) is (count > SEARCH_SLICE_TARGET)
    early = _issue_count(
        base_url, auth, f"org:{ORG} is:pr created:*..2026-01-07T00:00:00Z"
    )
    late = _issue_count(
        base_url, auth, f"org:{ORG} is:pr created:2026-01-07T00:00:01Z..*"
    )
    assert early + late == count


def test_only_the_first_thousand_results_are_retrievable(base_url, auth, seed):
    seed(
        {
            "repos": [{"owner": ORG, "name": "huge"}],
            "generate": [{"repo": f"{ORG}/huge", "pulls": 1001, "step_minutes": 1}],
        }
    )
    response = requests.get(
        f"{base_url}/search/issues",
        params={"q": f"org:{ORG}", "per_page": 100, "page": 11},
        headers=auth,
    )
    assert response.status_code == 422
    assert "first 1000" in response.json()["message"]
    last = requests.get(
        f"{base_url}/search/issues",
        params={"q": f"org:{ORG}", "per_page": 100, "page": 10},
        headers=auth,
    ).json()
    assert last["total_count"] == 1001
    assert len(last["items"]) == 100
