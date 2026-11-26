"""The consumers' own GraphQL documents, answered by fake-github."""

import pytest

from tests.conftest import TOKEN
from tests.github.consumer_queries import (
    BREAKFAST_RATE_LIMIT_QUERY,
    BREAKFAST_REPOSITORY_NAMES_QUERY,
    BREAKFAST_REVIEW_DECISION_QUERY,
    BREAKFAST_SEARCH_QUERY,
    SEARCH_PAGE_SIZE,
    breakfast_review_batch_query,
    ghsnitch_contributions_query,
)
from tests.github.seed import OLD_REPO, ORG, OTHER_USER, REPO, SEED

SMALL_PAGE = 2


@pytest.fixture(autouse=True)
def seeded(seed):
    seed(SEED)


def _data(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body
    return body["data"]


def _search(
    gql, search, *, owner=ORG, check_owner=True, page_size=SEARCH_PAGE_SIZE, cursor=None
):
    return gql(
        BREAKFAST_SEARCH_QUERY,
        {
            "owner": owner,
            "searchQuery": search,
            "cursor": cursor,
            "pageSize": page_size,
            "checkOwner": check_owner,
        },
    )


# --- breakfast ---------------------------------------------------------


def test_breakfast_search_finds_open_prs_in_live_repos(gql, base_url):
    data = _data(
        _search(gql, f"org:{ORG} is:pr is:open archived:false sort:created-asc")
    )
    assert data["owner"] == {"login": ORG}
    assert data["search"]["issueCount"] == 2
    assert data["search"]["nodes"] == [
        {"url": f"{base_url}/{ORG}/{REPO}/pull/1", "repository": {"name": REPO}},
        {"url": f"{base_url}/{ORG}/{REPO}/pull/2", "repository": {"name": REPO}},
    ]
    assert data["search"]["pageInfo"]["hasNextPage"] is False


def test_breakfast_search_skips_the_owner_check_when_asked(gql):
    data = _data(_search(gql, f"org:{ORG} is:pr", check_owner=False))
    assert "owner" not in data


def test_an_unknown_owner_is_null_without_an_error(gql):
    data = _data(_search(gql, "org:nobody is:pr", owner="nobody"))
    assert data["owner"] is None
    assert data["search"]["issueCount"] == 0


def test_breakfast_search_pages_with_cursors(gql):
    search = f"org:{ORG} is:pr sort:created-asc"
    first = _data(_search(gql, search, page_size=SMALL_PAGE))["search"]
    assert first["issueCount"] == 5
    assert first["pageInfo"]["hasNextPage"] is True
    seen = [n["url"] for n in first["nodes"]]
    cursor = first["pageInfo"]["endCursor"]
    while cursor:
        page = _data(_search(gql, search, page_size=SMALL_PAGE, cursor=cursor))[
            "search"
        ]
        seen += [n["url"] for n in page["nodes"]]
        cursor = (
            page["pageInfo"]["endCursor"] if page["pageInfo"]["hasNextPage"] else None
        )
    assert len(seen) == len(set(seen)) == 5


def test_breakfast_repository_names_filter_archived(gql):
    def names(archived):
        data = _data(
            gql(
                BREAKFAST_REPOSITORY_NAMES_QUERY,
                {
                    "owner": ORG,
                    "cursor": None,
                    "pageSize": SEARCH_PAGE_SIZE,
                    "archived": archived,
                },
            )
        )
        return [n["name"] for n in data["repositoryOwner"]["repositories"]["nodes"]]

    assert names(None) == [OLD_REPO, REPO]
    assert names(False) == [REPO]
    assert names(True) == [OLD_REPO]


def test_breakfast_review_decision(gql):
    data = _data(
        gql(
            BREAKFAST_REVIEW_DECISION_QUERY, {"owner": ORG, "repo": REPO, "prNumber": 1}
        )
    )
    assert data["repository"]["pullRequest"]["reviewDecision"] == "APPROVED"
    data = _data(
        gql(
            BREAKFAST_REVIEW_DECISION_QUERY, {"owner": ORG, "repo": REPO, "prNumber": 2}
        )
    )
    assert data["repository"]["pullRequest"]["reviewDecision"] is None


def test_breakfast_aliased_review_batch(gql):
    keys = [(ORG, REPO, 1), (ORG, REPO, 2)]
    variables = {}
    for i, (owner, repo, number) in enumerate(keys):
        variables.update({f"o{i}": owner, f"r{i}": repo, f"n{i}": number})
    data = _data(gql(breakfast_review_batch_query(len(keys)), variables))
    assert data["pr0"]["pullRequest"] == {
        "reviewDecision": "APPROVED",
        "reviews": {
            "totalCount": 2,
            "nodes": [
                {"author": {"login": OTHER_USER}, "state": "APPROVED"},
                {"author": {"login": "carol"}, "state": "COMMENTED"},
            ],
        },
    }
    assert data["pr1"]["pullRequest"]["reviews"] == {"totalCount": 0, "nodes": []}


def test_an_unknown_pull_request_is_a_not_found_error(gql):
    body = gql(
        BREAKFAST_REVIEW_DECISION_QUERY, {"owner": ORG, "repo": REPO, "prNumber": 99}
    ).json()
    assert body["data"]["repository"]["pullRequest"] is None
    [error] = body["errors"]
    assert error["type"] == "NOT_FOUND"
    assert error["path"] == ["repository", "pullRequest"]
    assert "number of 99" in error["message"]


def test_an_unknown_repository_is_a_not_found_error(gql):
    body = gql(
        BREAKFAST_REVIEW_DECISION_QUERY, {"owner": ORG, "repo": "nope", "prNumber": 1}
    ).json()
    assert body["data"] == {"repository": None}
    assert body["errors"][0]["type"] == "NOT_FOUND"


def test_rate_limit_agrees_with_the_headers(gql):
    response = gql(BREAKFAST_RATE_LIMIT_QUERY)
    rate = _data(response)["rateLimit"]
    assert rate["cost"] == 1
    assert rate["remaining"] == int(response.headers["X-RateLimit-Remaining"])
    assert rate["used"] == int(response.headers["X-RateLimit-Used"])
    assert response.headers["X-RateLimit-Resource"] == "graphql"
    assert rate["resetAt"].endswith("Z")


def test_page_sizes_over_a_hundred_are_refused(gql):
    body = _search(gql, f"org:{ORG} is:pr", page_size=101).json()
    assert "exceeds the `first` limit of 100 records" in body["errors"][0]["message"]


# --- gh-snitch ---------------------------------------------------------


def test_ghsnitch_contribution_batch(gql):
    query, variables = ghsnitch_contributions_query(
        ["alice", OTHER_USER], "2026-01-01T00:00:00Z", "2026-12-31T23:59:59Z"
    )
    data = _data(gql(query, variables))
    assert data["user_0"] == {
        "login": "alice",
        "contributionsCollection": {"contributionCalendar": {"totalContributions": 11}},
    }
    assert data["user_1"]["contributionsCollection"]["contributionCalendar"] == {
        "totalContributions": 4
    }


def test_ghsnitch_breakdown_counters(gql):
    query, variables = ghsnitch_contributions_query(
        ["alice"],
        "2026-01-01T00:00:00Z",
        "2026-12-31T23:59:59Z",
        include_breakdown=True,
    )
    collection = _data(gql(query, variables))["user_0"]["contributionsCollection"]
    assert collection["totalCommitContributions"] == 5
    assert collection["totalPullRequestContributions"] == 2
    assert collection["totalPullRequestReviewContributions"] == 3
    assert collection["totalIssueContributions"] == 1
    assert collection["totalRepositoryContributions"] == 0
    assert collection["restrictedContributionsCount"] == 0


def test_an_unknown_operative_is_not_found_and_spares_the_rest(gql):
    query, variables = ghsnitch_contributions_query(
        ["alice", "ghost"], "2026-01-01T00:00:00Z", "2026-12-31T23:59:59Z"
    )
    body = gql(query, variables).json()
    assert body["data"]["user_0"]["login"] == "alice"
    assert body["data"]["user_1"] is None
    [error] = body["errors"]
    assert (error["type"], error["path"]) == ("NOT_FOUND", ["user_1"])
    assert "login of 'ghost'" in error["message"]


def test_a_range_over_a_year_is_refused(gql):
    query, variables = ghsnitch_contributions_query(
        ["alice"], "2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z"
    )
    body = gql(query, variables).json()
    assert "must not exceed 1 year" in body["errors"][0]["message"]


# --- transport ---------------------------------------------------------


@pytest.mark.parametrize("path", ["/graphql", "/api/graphql"])
def test_both_graphql_paths_answer(gql, path):
    assert _data(gql(BREAKFAST_RATE_LIMIT_QUERY, path=path))["rateLimit"]


def test_no_token_is_a_401_with_githubs_body(gql):
    response = gql(BREAKFAST_RATE_LIMIT_QUERY, headers={})
    assert response.status_code == 401
    assert response.json()["message"] == "Bad credentials"


def test_an_unknown_token_is_a_401(gql):
    response = gql(
        BREAKFAST_RATE_LIMIT_QUERY, headers={"Authorization": "bearer wrong"}
    )
    assert response.status_code == 401


def test_the_token_scheme_is_accepted(gql):
    assert _data(
        gql(BREAKFAST_RATE_LIMIT_QUERY, headers={"Authorization": f"token {TOKEN}"})
    )


def test_a_syntax_error_is_reported_as_github_does(gql):
    body = gql("query { rateLimit {").json()
    assert "data" not in body
    assert "Syntax Error" in body["errors"][0]["message"]


def test_the_journal_names_root_fields_and_aliases(gql, journal):
    gql(breakfast_review_batch_query(1), {"o0": ORG, "r0": REPO, "n0": 1})
    gql(BREAKFAST_RATE_LIMIT_QUERY)
    first, second = journal()[-2:]
    assert first["details"]["graphql_fields"] == ["repository"]
    assert first["details"]["graphql_aliases"] == ["pr0"]
    assert second["details"]["graphql_fields"] == ["rateLimit"]


def test_a_fault_can_target_one_root_field(gql, add_fault):
    add_fault(
        {
            "match": {"graphql_fields": "search"},
            "action": {
                "type": "hook",
                "name": "graphql_error",
                "error_type": "RESOURCE_LIMITS_EXCEEDED",
            },
        }
    )
    assert _data(gql(BREAKFAST_RATE_LIMIT_QUERY))["rateLimit"]
    body = _search(gql, f"org:{ORG} is:pr").json()
    assert body["data"] is None
    assert body["errors"][0]["type"] == "RESOURCE_LIMITS_EXCEEDED"
