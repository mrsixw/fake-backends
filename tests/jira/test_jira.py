"""grind's JIRA requests, in both modes, against the published specs.

The requests copy src/grind/jira.py at https://github.com/mrsixw/grind
(2026-11-26): ``_FIELDS``, the JQL ``_jql`` builds, the search loop, and the
write payloads.
"""

import pytest

from fake_backends.server.jira import Mode
from fake_backends.server.jira.adf import text_to_adf
from tests.conftest import IDENTITY, TOKEN
from tests.jira.conftest import EMAIL, basic

# grind jira.py _FIELDS
GRIND_FIELDS = (
    "summary,status,issuetype,assignee,reporter,labels,created,updated,description"
)
PAGE = 2

SEED = {
    "users": [
        {"name": IDENTITY, "display_name": "Alice Example", "email": EMAIL},
        {"name": "bob", "display_name": "Bob Builder"},
        {"name": "bobby", "display_name": "Bobby Tables"},
    ],
    "projects": [
        {"key": "PROJ", "name": "Project"},
        {
            "key": "OPS",
            "statuses": [
                {"name": "Open", "category": "new"},
                {"name": "Fixing", "category": "indeterminate"},
                {"name": "Closed", "category": "done"},
            ],
            "transitions": [
                {"id": "5", "name": "Start", "to": "Fixing", "from": ["Open"]},
                {"id": "6", "name": "Close", "to": "Closed"},
            ],
            "issue_types": ["Incident"],
        },
    ],
    "issues": [
        {
            "key": "PROJ-1",
            "summary": "Coffee machine leaks",
            "description": "It drips.\n\nEvery morning.",
            "type": "Bug",
            "assignee": IDENTITY,
            "reporter": "bob",
            "labels": ["kitchen"],
            "created": "2026-03-01T09:00:00Z",
            "updated": "2026-03-04T09:00:00Z",
            "comments": [{"author": "bob", "body": "Still leaking."}],
        },
        {
            "key": "PROJ-2",
            "summary": "Order beans",
            "assignee": IDENTITY,
            "status": "In Progress",
            "updated": "2026-03-03T09:00:00Z",
        },
        {
            "key": "PROJ-3",
            "summary": "Descale",
            "assignee": "bob",
            "updated": "2026-03-02T09:00:00Z",
        },
        {
            "key": "PROJ-4",
            "summary": "Old grinder",
            "status": "Done",
            "assignee": IDENTITY,
            "updated": "2026-03-05T09:00:00Z",
        },
        {"key": "OPS-1", "summary": "Pager", "type": "Incident", "assignee": IDENTITY},
    ],
}


@pytest.fixture(autouse=True)
def seeded(seed):
    seed(SEED)


def _jql(*clauses):
    """What grind's _jql builds: clauses AND'd, newest update first."""
    return " AND ".join(clauses) + " ORDER BY updated DESC"


def _search_all(api, mode, jql, page_size):
    """grind's list loop, for either pagination style."""
    keys, cursor, pages = [], {}, 0
    path = "/search/jql" if mode is Mode.CLOUD else "/search"
    while True:
        params = {"jql": jql, "fields": GRIND_FIELDS, "maxResults": page_size, **cursor}
        page = api("GET", path, params=params).json()
        pages += 1
        issues = page.get("issues", [])
        keys += [i["key"] for i in issues]
        if mode is Mode.CLOUD:
            if not page.get("nextPageToken"):
                break
            cursor = {"nextPageToken": page["nextPageToken"]}
        else:
            if not issues or len(keys) >= page.get("total", 0):
                break
            cursor = {"startAt": len(keys)}
    return keys, pages


def _text(mode, body):
    return text_to_adf(body) if mode is Mode.CLOUD else body


# --- reading -------------------------------------------------------------


def test_myself(api, valid, mode):
    body = valid("GET /myself 200", api("GET", "/myself"))
    assert body["displayName"] == "Alice Example"
    if mode is Mode.CLOUD:
        assert body["accountId"]
    else:
        assert body["name"] == IDENTITY


def test_grinds_mine_query_spans_pages(api, mode):
    jql = _jql('project = "PROJ"', "assignee = currentUser()", "statusCategory != Done")
    keys, pages = _search_all(api, mode, jql, page_size=1)
    assert keys == ["PROJ-1", "PROJ-2"]
    assert pages == 2


def test_both_paging_styles_return_every_issue_once(api, mode):
    keys, pages = _search_all(api, mode, _jql('project in ("PROJ", "OPS")'), PAGE)
    assert sorted(keys) == ["OPS-1", "PROJ-1", "PROJ-2", "PROJ-3", "PROJ-4"]
    assert pages == 3


def test_search_responses_match_the_spec(api, valid, mode, cloud):
    body = valid(
        "GET /search/jql 200",
        api(
            "GET",
            "/search/jql",
            params={
                "jql": _jql('project = "PROJ"'),
                "fields": GRIND_FIELDS,
                "maxResults": PAGE,
            },
        ),
    )
    assert body["isLast"] is False
    assert (
        body["issues"][0]["fields"]["description"] is None
        or "type" in body["issues"][0]["fields"]["description"]
    )


def test_server_search_matches_the_spec(api, valid, server):
    body = valid(
        "GET /search 200",
        api(
            "GET",
            "/search",
            params={
                "jql": _jql('project = "PROJ"'),
                "fields": GRIND_FIELDS,
                "maxResults": PAGE,
            },
        ),
    )
    assert (body["total"], body["startAt"], len(body["issues"])) == (4, 0, PAGE)


@pytest.mark.parametrize(
    ("clauses", "expected"),
    [
        (['project = "PROJ"', "statusCategory = Done"], ["PROJ-4"]),
        (['project = "PROJ"', 'text ~ "morning"'], ["PROJ-1"]),
        (['project = "PROJ"', 'text ~ "still leaking"'], ["PROJ-1"]),
        (['project = "PROJ"', "(labels = kitchen)"], ["PROJ-1"]),
        (['project = "PROJ"', 'status = "In Progress"'], ["PROJ-2"]),
        (
            ["assignee = currentUser()", "statusCategory != Done"],
            ["PROJ-1", "PROJ-2", "OPS-1"],
        ),
        (['project = "PROJ"', "assignee = bob"], ["PROJ-3"]),
        (["issuetype = Incident"], ["OPS-1"]),
    ],
)
def test_jql_clauses(api, mode, clauses, expected):
    keys, _ = _search_all(api, mode, _jql(*clauses), page_size=50)
    assert sorted(keys) == sorted(expected)


def test_order_by_updated_desc(api, mode):
    keys, _ = _search_all(api, mode, _jql('project = "PROJ"'), page_size=50)
    assert keys == ["PROJ-4", "PROJ-1", "PROJ-2", "PROJ-3"]


@pytest.mark.parametrize(
    "jql",
    [
        'project = "PROJ" OR project = "OPS"',
        'NOT project = "PROJ"',
        'project = "PROJ" AND sprint in openSprints()',
        'project = "PROJ" AND created >= -7d',
        'project = "PROJ" AND statusCategory = Waiting',
    ],
)
def test_jql_outside_the_subset_is_a_400(api, mode, error_valid, jql):
    path = "/search/jql" if mode is Mode.CLOUD else "/search"
    body = error_valid(api("GET", path, params={"jql": jql}), 400)
    assert body["errorMessages"]


def test_cloud_refuses_an_unbounded_search(api, error_valid, cloud):
    body = error_valid(
        api("GET", "/search/jql", params={"jql": "ORDER BY updated DESC"}), 400
    )
    assert "Unbounded" in body["errorMessages"][0]


def test_get_issue_with_comments(api, valid, mode):
    body = valid(
        "GET /issue/{issueIdOrKey} 200",
        api("GET", "/issue/PROJ-1", params={"fields": GRIND_FIELDS + ",comment"}),
    )
    fields = body["fields"]
    assert fields["summary"] == "Coffee machine leaks"
    assert fields["status"]["statusCategory"]["key"] == "new"
    assert fields["reporter"]["displayName"] == "Bob Builder"
    assert fields["description"] == _text(mode, "It drips.\n\nEvery morning.")
    [comment] = fields["comment"]["comments"]
    assert comment["body"] == _text(mode, "Still leaking.")
    assert set(fields) == set(GRIND_FIELDS.split(",")) | {"comment"}


def test_an_unknown_issue_is_jiras_404(api, error_valid):
    body = error_valid(api("GET", "/issue/PROJ-99"), 404)
    assert "does not exist" in body["errorMessages"][0]


# --- writing -------------------------------------------------------------


def test_create_issue_round_trips_its_body(api, valid, mode):
    description = _text(mode, "Line one\nline two\n\nSecond paragraph")
    created = valid(
        "POST /issue 201",
        api(
            "POST",
            "/issue",
            json={
                "fields": {
                    "project": {"key": "PROJ"},
                    "summary": "Buy milk",
                    "issuetype": {"name": "Task"},
                    "description": description,
                    "labels": ["fridge"],
                }
            },
        ),
        201,
    )
    assert created["key"] == "PROJ-5"
    fields = api(
        "GET", f"/issue/{created['key']}", params={"fields": GRIND_FIELDS}
    ).json()["fields"]
    assert fields["description"] == description
    assert fields["reporter"]["displayName"] == "Alice Example"
    assert fields["status"]["name"] == "To Do"
    assert fields["labels"] == ["fridge"]


def test_create_with_an_assignee(api, mode):
    ref = (
        {"accountId": api("GET", "/myself").json()["accountId"]}
        if mode is Mode.CLOUD
        else {"name": IDENTITY}
    )
    created = api(
        "POST",
        "/issue",
        json={
            "fields": {
                "project": {"key": "OPS"},
                "summary": "Mine",
                "issuetype": {"name": "Incident"},
                "assignee": ref,
            }
        },
    ).json()
    issue = api("GET", f"/issue/{created['key']}").json()
    assert issue["fields"]["assignee"]["displayName"] == "Alice Example"


def test_the_wrong_body_kind_for_the_mode_is_refused(api, mode, error_valid):
    wrong = "plain text" if mode is Mode.CLOUD else text_to_adf("adf")
    body = error_valid(
        api(
            "POST",
            "/issue",
            json={
                "fields": {
                    "project": {"key": "PROJ"},
                    "summary": "x",
                    "issuetype": {"name": "Task"},
                    "description": wrong,
                }
            },
        ),
        400,
    )
    assert "description" in body["errors"]


def test_create_names_every_bad_field(api, error_valid):
    body = error_valid(
        api(
            "POST",
            "/issue",
            json={
                "fields": {"project": {"key": "NOPE"}, "issuetype": {"name": "Task"}}
            },
        ),
        400,
    )
    assert set(body["errors"]) == {"project", "summary"}


def test_edit_summary_description_and_labels(api, mode):
    response = api(
        "PUT",
        "/issue/PROJ-1",
        json={
            "fields": {"summary": "Leak fixed?", "description": _text(mode, "Maybe.")},
            "update": {"labels": [{"add": "plumbing"}, {"remove": "kitchen"}]},
        },
    )
    assert response.status_code == 204
    fields = api("GET", "/issue/PROJ-1", params={"fields": GRIND_FIELDS}).json()[
        "fields"
    ]
    assert (fields["summary"], fields["description"], fields["labels"]) == (
        "Leak fixed?",
        _text(mode, "Maybe."),
        ["plumbing"],
    )


def test_comment_round_trips(api, valid, mode):
    body = _text(mode, "Fixed it.\n\nWith tape.")
    created = valid(
        "POST /issue/{issueIdOrKey}/comment 201",
        api("POST", "/issue/PROJ-1/comment", json={"body": body}),
        201,
    )
    assert created["body"] == body
    assert created["author"]["displayName"] == "Alice Example"
    comments = api("GET", "/issue/PROJ-1", params={"fields": "comment"}).json()[
        "fields"
    ]["comment"]
    assert [c["body"] for c in comments["comments"]][-1] == body


def test_assign_and_unassign(api, mode):
    if mode is Mode.CLOUD:
        [bob] = api("GET", "/user/search", params={"query": "Bob Builder"}).json()
        ref, empty = {"accountId": bob["accountId"]}, {"accountId": None}
    else:
        ref, empty = {"name": "bob"}, {"name": None}
    assert api("PUT", "/issue/PROJ-2/assignee", json=ref).status_code == 204
    assert (
        api("GET", "/issue/PROJ-2").json()["fields"]["assignee"]["displayName"]
        == "Bob Builder"
    )
    assert api("PUT", "/issue/PROJ-2/assignee", json=empty).status_code == 204
    assert api("GET", "/issue/PROJ-2").json()["fields"]["assignee"] is None


def test_assigning_an_unknown_user_is_a_400(api, mode, error_valid):
    ref = {"accountId": "nobody"} if mode is Mode.CLOUD else {"name": "nobody"}
    body = error_valid(api("PUT", "/issue/PROJ-2/assignee", json=ref), 400)
    assert "does not exist" in body["errors"]["assignee"]


def test_user_search_finds_partial_matches(api, cloud):
    names = [
        u["displayName"]
        for u in api("GET", "/user/search", params={"query": "bob"}).json()
    ]
    assert names == ["Bob Builder", "Bobby Tables"]


def test_user_search_matches_the_spec(api, valid, cloud):
    valid("GET /user/search 200", api("GET", "/user/search", params={"query": "alice"}))


def test_server_has_no_user_search(api, server):
    assert api("GET", "/user/search", params={"query": "bob"}).status_code == 404


def test_default_workflow_transitions(api, valid):
    body = valid(
        "GET /issue/{issueIdOrKey}/transitions 200",
        api("GET", "/issue/PROJ-1/transitions"),
    )
    assert [t["to"]["name"] for t in body["transitions"]] == ["In Progress", "Done"]
    done = next(t for t in body["transitions"] if t["to"]["name"] == "Done")
    assert (
        api(
            "POST", "/issue/PROJ-1/transitions", json={"transition": {"id": done["id"]}}
        ).status_code
        == 204
    )
    fields = api("GET", "/issue/PROJ-1").json()["fields"]
    assert fields["status"]["statusCategory"]["key"] == "done"


def test_a_projects_own_workflow_limits_transitions(api, error_valid):
    names = [
        t["name"] for t in api("GET", "/issue/OPS-1/transitions").json()["transitions"]
    ]
    assert names == ["Start", "Close"]
    api("POST", "/issue/OPS-1/transitions", json={"transition": {"id": "5"}})
    names = [
        t["name"] for t in api("GET", "/issue/OPS-1/transitions").json()["transitions"]
    ]
    assert names == ["Close"]  # Start only leaves Open
    body = error_valid(
        api("POST", "/issue/OPS-1/transitions", json={"transition": {"id": "5"}}), 400
    )
    assert "not valid" in body["errorMessages"][0]


# --- auth ----------------------------------------------------------------


def test_no_credentials_is_a_401(api, error_valid):
    error_valid(api("GET", "/myself", headers={}), 401)


def test_a_wrong_token_is_a_401(api):
    assert api("GET", "/myself", headers=basic(EMAIL, "wrong")).status_code == 401


def test_cloud_refuses_a_bearer_token(api, cloud):
    assert (
        api("GET", "/myself", headers={"Authorization": f"Bearer {TOKEN}"}).status_code
        == 401
    )


def test_server_accepts_a_personal_access_token(api, server):
    """grind's Server mode with no user: a Bearer PAT."""
    assert (
        api("GET", "/myself", headers={"Authorization": f"Bearer {TOKEN}"}).status_code
        == 200
    )


def test_the_other_modes_api_is_not_served(base_url, mode, auth):
    import requests

    other = "/rest/api/2" if mode is Mode.CLOUD else "/rest/api/3"
    assert requests.get(f"{base_url}{other}/myself", headers=auth).status_code == 404
