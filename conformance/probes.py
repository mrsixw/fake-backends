"""The requests each comparison sends, to the real service and to the fake alike.

GitHub probes read the frozen ``mrsixw/breakfast-fixtures`` repository, which
fake-github's ``breakfast-fixtures`` seed copies, so values the fixture fixes
can be compared exactly. The GraphQL documents are the consumers' own, from
tests/github/consumer_queries.py.

Jenkins probes read the instance conformance/jenkins.py provisions: jeeves'
end-to-end Jenkins, which fake-jenkins' ``jeeves-fixtures`` seed copies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.github.consumer_queries import (
    BREAKFAST_RATE_LIMIT_QUERY,
    BREAKFAST_REVIEW_DECISION_QUERY,
    BREAKFAST_SEARCH_QUERY,
    SEARCH_PAGE_SIZE,
    breakfast_review_batch_query,
    ghsnitch_contributions_query,
)

__all__ = ["GITHUB_PROBES", "JENKINS_PROBES", "Probe"]

OWNER = "mrsixw"
REPO = "breakfast-fixtures"
FULL = f"{OWNER}/{REPO}"
# The head commit of fixture PR 1, as on GitHub and in the seed.
PR1_HEAD = "6979952d29d39db1882a82b7628e2e6e5ffc2c46"
RATE_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "X-RateLimit-Used",
    "X-RateLimit-Resource",
)


@dataclass(frozen=True)
class Probe:
    name: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    # A GraphQL document, POSTed to the GraphQL endpoint instead of a GET.
    query: str | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    # Headers whose presence must agree.
    headers: tuple[str, ...] = ()
    # A Jenkins probe of plain text rather than JSON.
    text: bool = False


def _batch() -> tuple[str, dict[str, Any]]:
    keys = [(OWNER, REPO, 1), (OWNER, REPO, 8)]
    variables: dict[str, Any] = {}
    for i, (owner, repo, number) in enumerate(keys):
        variables.update({f"o{i}": owner, f"r{i}": repo, f"n{i}": number})
    return breakfast_review_batch_query(len(keys)), variables


_BATCH_QUERY, _BATCH_VARIABLES = _batch()
_CONTRIBUTIONS_QUERY, _CONTRIBUTIONS_VARIABLES = ghsnitch_contributions_query(
    [OWNER], "2026-01-01T00:00:00Z", "2026-06-30T00:00:00Z", include_breakdown=True
)


def _search(text: str) -> dict[str, Any]:
    return {
        "owner": OWNER,
        "searchQuery": text,
        "cursor": None,
        "pageSize": SEARCH_PAGE_SIZE,
        "checkOwner": True,
    }


GITHUB_PROBES = [
    Probe("user", "/user", headers=RATE_HEADERS),
    Probe("open pull", f"/repos/{FULL}/pulls/1", headers=RATE_HEADERS),
    Probe("merged pull", f"/repos/{FULL}/pulls/8"),
    Probe("reviews", f"/repos/{FULL}/pulls/1/reviews"),
    Probe(
        "unprotected branch",
        f"/repos/{FULL}/branches/main/protection/required_pull_request_reviews",
    ),
    Probe("check runs", f"/repos/{FULL}/commits/{PR1_HEAD}/check-runs"),
    Probe("unknown pull", f"/repos/{FULL}/pulls/999"),
    Probe(
        "search issues",
        "/search/issues",
        {"q": f"repo:{FULL} is:pr", "sort": "created", "order": "asc"},
        headers=RATE_HEADERS,
    ),
    Probe("list issues", f"/repos/{FULL}/issues", {"state": "all", "per_page": 100}),
    Probe(
        "graphql search",
        "/graphql",
        query=BREAKFAST_SEARCH_QUERY,
        variables=_search(f"repo:{FULL} is:pr sort:created-asc"),
        headers=RATE_HEADERS,
    ),
    Probe(
        "graphql search open",
        "/graphql",
        query=BREAKFAST_SEARCH_QUERY,
        variables=_search(f"repo:{FULL} is:pr is:open sort:created-asc"),
    ),
    Probe(
        "graphql review decision",
        "/graphql",
        query=BREAKFAST_REVIEW_DECISION_QUERY,
        variables={"owner": OWNER, "repo": REPO, "prNumber": 1},
    ),
    Probe(
        "graphql unknown pull",
        "/graphql",
        query=BREAKFAST_REVIEW_DECISION_QUERY,
        variables={"owner": OWNER, "repo": REPO, "prNumber": 999},
    ),
    Probe(
        "graphql review batch",
        "/graphql",
        query=_BATCH_QUERY,
        variables=_BATCH_VARIABLES,
    ),
    Probe("graphql rate limit", "/graphql", query=BREAKFAST_RATE_LIMIT_QUERY),
    Probe(
        "graphql contributions",
        "/graphql",
        query=_CONTRIBUTIONS_QUERY,
        variables=_CONTRIBUTIONS_VARIABLES,
    ),
]

JENKINS_PROBES = [
    Probe("root", "/api/json"),
    Probe("me", "/me/api/json"),
    Probe("crumb issuer", "/crumbIssuer/api/json"),
    # With a tree: plain /api/json on this folder is a 500 on the real
    # Jenkins (an NPE in View.getOwner for the AllView jeeves' folder.xml
    # declares), so the comparison asks for the fields jeeves reads.
    Probe(
        "folder",
        "/job/e2e-folder/api/json",
        {"tree": "name,url,description,jobs[name,url,color]"},
    ),
    Probe("job", "/job/e2e-freestyle/api/json"),
    Probe(
        "builds tree",
        "/job/e2e-freestyle/api/json",
        {
            "tree": "builds[number,result,timestamp,duration,url,building,"
            "actions[parameters[name,value],"
            "causes[shortDescription,userId,upstreamProject,upstreamBuild]]]{0,5}"
        },
    ),
    Probe("build", "/job/e2e-freestyle/1/api/json"),
    Probe(
        "changes tree",
        "/job/e2e-freestyle/1/api/json",
        {
            "tree": "number,url,culprits[fullName,absoluteUrl],"
            "changeSets[items[commitId,msg]],changeSet[items[commitId,msg]]"
        },
    ),
    Probe("missing permalink", "/job/e2e-freestyle/lastFailedBuild/api/json"),
    Probe("queue", "/queue/api/json"),
    Probe("nodes", "/computer/api/json", {"depth": 1}),
    Probe("lockable resources", "/lockable-resources/api/json"),
    Probe(
        "progressive log",
        "/job/e2e-freestyle/1/logText/progressiveText",
        {"start": 0},
        headers=("X-Text-Size", "X-More-Data"),
        text=True,
    ),
]
