"""fake-github's REST endpoints, each checked against GitHub's OpenAPI schema."""

import pytest
import requests

from tests.conftest import IDENTITY
from tests.github.conftest import component_errors, schema_errors
from tests.github.seed import ORG, OTHER_USER, REPO, SEED

PREFIXES = ["", "/api/v3"]
REPO_PATH = f"/repos/{ORG}/{REPO}"


@pytest.fixture(autouse=True)
def seeded(seed):
    seed(SEED)


@pytest.fixture(params=PREFIXES, ids=["root", "enterprise"])
def api(request, base_url, auth):
    """GET/POST/PATCH under both the root and /api/v3."""
    prefix = request.param

    def call(method, path, **kwargs):
        return requests.request(
            method, f"{base_url}{prefix}{path}", headers=auth, **kwargs
        )

    return call


def _valid(operation, response, status=200):
    assert response.status_code == status, response.text
    body = response.json()
    assert schema_errors(operation, body) == []
    return body


def test_user(api):
    body = _valid("GET /user 200", api("GET", "/user"))
    assert body["login"] == IDENTITY


def test_pull(api, base_url):
    body = _valid(
        "GET /repos/{owner}/{repo}/pulls/{pull_number} 200",
        api("GET", f"{REPO_PATH}/pulls/1"),
    )
    assert body["title"] == "Add sprockets"
    assert body["html_url"] == f"{base_url}/{ORG}/{REPO}/pull/1"
    assert [label["name"] for label in body["labels"]] == ["enhancement"]
    assert [r["login"] for r in body["requested_reviewers"]] == [OTHER_USER]
    assert body["merged"] is False


def test_merged_pull(api):
    body = _valid(
        "GET /repos/{owner}/{repo}/pulls/{pull_number} 200",
        api("GET", f"{REPO_PATH}/pulls/3"),
    )
    assert (body["state"], body["merged"], body["merged_at"]) == (
        "closed",
        True,
        "2026-02-02T10:00:00Z",
    )


def test_an_unknown_pull_is_githubs_404(api):
    response = api("GET", f"{REPO_PATH}/pulls/99")
    assert response.status_code == 404
    assert component_errors("basic-error", response.json()) == []
    assert response.json()["message"] == "Not Found"


def test_reviews_page_by_number(api):
    body = _valid(
        "GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews 200",
        api("GET", f"{REPO_PATH}/pulls/1/reviews", params={"per_page": 1}),
    )
    assert [r["state"] for r in body] == ["APPROVED"]
    second = api(
        "GET", f"{REPO_PATH}/pulls/1/reviews", params={"per_page": 1, "page": 2}
    )
    assert [r["user"]["login"] for r in second.json()] == ["carol"]
    assert 'rel="prev"' in second.headers["Link"]


def test_branch_protection(api):
    path = f"{REPO_PATH}/branches/main/protection/required_pull_request_reviews"
    body = _valid(
        "GET /repos/{owner}/{repo}/branches/{branch}/protection/"
        "required_pull_request_reviews 200",
        api("GET", path),
    )
    assert body["required_approving_review_count"] == 2


def test_an_unprotected_branch_is_a_404(api):
    response = api(
        "GET", f"{REPO_PATH}/branches/dev/protection/required_pull_request_reviews"
    )
    assert response.status_code == 404
    assert response.json()["message"] == "Branch not protected"


def _head(api):
    return api("GET", f"{REPO_PATH}/pulls/1").json()["head"]["sha"]


def test_check_runs(api):
    body = _valid(
        "GET /repos/{owner}/{repo}/commits/{ref}/check-runs 200",
        api("GET", f"{REPO_PATH}/commits/{_head(api)}/check-runs"),
    )
    assert body["total_count"] == 2
    assert {(r["name"], r["status"], r["conclusion"]) for r in body["check_runs"]} == {
        ("test", "completed", "success"),
        ("lint", "in_progress", None),
    }


def test_combined_status(api):
    body = _valid(
        "GET /repos/{owner}/{repo}/commits/{ref}/status 200",
        api("GET", f"{REPO_PATH}/commits/{_head(api)}/status"),
    )
    assert (body["state"], body["total_count"]) == ("success", 1)


def test_an_unknown_commit_is_a_422(api):
    assert api("GET", f"{REPO_PATH}/commits/deadbeef/status").status_code == 422


def test_search_issues(api):
    body = _valid(
        "GET /search/issues 200",
        api(
            "GET",
            "/search/issues",
            params={"q": f"is:issue repo:{ORG}/{REPO} state:open", "sort": "updated"},
        ),
    )
    assert body["total_count"] == 1
    assert body["items"][0]["title"] == "Sprockets squeak"


def test_search_pages_by_number(api):
    params = {"q": f"org:{ORG} is:pr archived:false", "per_page": 2, "page": 2}
    body = _valid("GET /search/issues 200", api("GET", "/search/issues", params=params))
    assert body["total_count"] == 4
    assert len(body["items"]) == 2


def test_search_needs_a_query(api):
    assert api("GET", "/search/issues").status_code == 422


def test_list_issues_includes_pull_requests(api):
    body = _valid(
        "GET /repos/{owner}/{repo}/issues 200",
        api("GET", f"{REPO_PATH}/issues", params={"state": "all", "per_page": 100}),
    )
    assert {i["number"] for i in body} == {1, 2, 3, 4, 5, 6}
    assert all(("pull_request" in i) == (i["number"] <= 4) for i in body)


def test_get_issue(api):
    body = _valid(
        "GET /repos/{owner}/{repo}/issues/{issue_number} 200",
        api("GET", f"{REPO_PATH}/issues/5"),
    )
    assert body["body"] == "They squeak when wet."
    assert "pull_request" not in body


def test_a_pull_request_read_as_an_issue_says_so(api):
    """grind refuses an item with a pull_request key."""
    body = api("GET", f"{REPO_PATH}/issues/1").json()
    assert body["pull_request"]["url"].endswith(f"{REPO_PATH}/pulls/1")


def test_create_issue(api):
    response = api(
        "POST",
        f"{REPO_PATH}/issues",
        json={
            "title": "New",
            "body": "Text",
            "labels": ["bug"],
            "assignees": [OTHER_USER],
        },
    )
    body = _valid("POST /repos/{owner}/{repo}/issues 201", response, 201)
    assert body["number"] == 7  # after PRs 1-4 and issues 5-6: one sequence
    assert body["user"]["login"] == IDENTITY
    assert [a["login"] for a in body["assignees"]] == [OTHER_USER]
    assert api("GET", f"{REPO_PATH}/issues/7").json()["title"] == "New"


def test_creating_an_issue_needs_a_title(api):
    response = api("POST", f"{REPO_PATH}/issues", json={"body": "no title"})
    assert response.status_code == 422
    assert component_errors("validation-error", response.json()) == []


def test_update_issue(api):
    response = api(
        "PATCH",
        f"{REPO_PATH}/issues/5",
        json={"title": "Renamed", "labels": ["bug", "p1"], "assignees": []},
    )
    body = _valid("PATCH /repos/{owner}/{repo}/issues/{issue_number} 200", response)
    assert (
        body["title"],
        [label["name"] for label in body["labels"]],
        body["assignees"],
    ) == (
        "Renamed",
        ["bug", "p1"],
        [],
    )


def test_closing_as_not_planned(api):
    response = api(
        "PATCH",
        f"{REPO_PATH}/issues/5",
        json={"state": "closed", "state_reason": "not_planned"},
    )
    body = _valid("PATCH /repos/{owner}/{repo}/issues/{issue_number} 200", response)
    assert (body["state"], body["state_reason"]) == ("closed", "not_planned")
    assert body["closed_at"]


def test_reopening(api):
    api("PATCH", f"{REPO_PATH}/issues/6", json={"state": "open"})
    body = api("GET", f"{REPO_PATH}/issues/6").json()
    assert (body["state"], body["state_reason"], body["closed_at"]) == (
        "open",
        "reopened",
        None,
    )


def test_comments(api):
    listed = _valid(
        "GET /repos/{owner}/{repo}/issues/{issue_number}/comments 200",
        api("GET", f"{REPO_PATH}/issues/5/comments"),
    )
    assert [c["body"] for c in listed] == ["Confirmed."]
    created = _valid(
        "POST /repos/{owner}/{repo}/issues/{issue_number}/comments 201",
        api("POST", f"{REPO_PATH}/issues/5/comments", json={"body": "Fixed."}),
        201,
    )
    assert created["user"]["login"] == IDENTITY
    again = api("GET", f"{REPO_PATH}/issues/5/comments").json()
    assert [c["body"] for c in again] == ["Confirmed.", "Fixed."]


def test_latest_release_skips_prereleases(api):
    body = _valid(
        "GET /repos/{owner}/{repo}/releases/latest 200",
        api("GET", f"{REPO_PATH}/releases/latest"),
    )
    assert body["tag_name"] == "v1.1.0"
    assert body["assets"][0]["name"] == "widgets"


def test_latest_release_needs_no_token(base_url):
    assert requests.get(f"{base_url}{REPO_PATH}/releases/latest").status_code == 200


def test_assets_download_from_web_paths(base_url):
    latest = requests.get(f"{base_url}/{ORG}/{REPO}/releases/latest/download/widgets")
    assert latest.text == "#!/bin/sh\necho new\n"
    pinned = requests.get(f"{base_url}/{ORG}/{REPO}/releases/download/v1.0.0/widgets")
    assert pinned.text == "#!/bin/sh\necho old\n"
    assert (
        requests.get(
            f"{base_url}/{ORG}/{REPO}/releases/download/v9/widgets"
        ).status_code
        == 404
    )


def test_rest_needs_a_token(base_url):
    response = requests.get(f"{base_url}/user")
    assert response.status_code == 401
    assert response.json()["message"] == "Bad credentials"
    assert component_errors("basic-error", response.json()) == []
