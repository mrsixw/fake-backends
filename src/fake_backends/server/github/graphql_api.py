"""fake-github's GraphQL API, at ``/graphql`` and ``/api/graphql``.

Queries run on graphql-core against ``schema.graphql``, a trimmed copy of
GitHub's public schema, so aliases, variables, fragments and ``@include``
behave as they do on github.com. Errors take GitHub's shape: a top-level
``type`` such as ``NOT_FOUND`` beside ``message``, ``path`` and ``locations``.

Every query costs one point from the caller's ``graphql`` bucket. GitHub
charges more for large connections; the consumers read ``remaining`` and
``resetAt``, not the cost, so the flat charge is enough.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from functools import cache
from http import HTTPStatus
from importlib.resources import files
from typing import Any

from graphql import (
    GraphQLError,
    GraphQLSchema,
    OperationDefinitionNode,
    build_schema,
    graphql,
    parse,
)
from graphql.error import GraphQLSyntaxError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from fake_backends.server.github import shapes
from fake_backends.server.github.model import (
    Bucket,
    ContributionKind,
    GitHubState,
    Issue,
    Pull,
    Repo,
    User,
)
from fake_backends.server.github.ratelimit import RateSnapshot, spend
from fake_backends.server.github.search import RESULT_CAP, SearchError, search

__all__ = [
    "GRAPHQL_PATHS",
    "MAX_PAGE_SIZE",
    "MAX_CONTRIBUTION_SPAN",
    "graphql_error",
    "graphql_routes",
    "operation_details",
]

GRAPHQL_PATHS = ("/graphql", "/api/graphql")
# GitHub's largest page for any connection.
MAX_PAGE_SIZE = 100
# contributionsCollection refuses a from..to longer than a year.
MAX_CONTRIBUTION_SPAN = dt.timedelta(days=366)
# With no from, GitHub reports the year up to ``to``.
DEFAULT_CONTRIBUTION_SPAN = dt.timedelta(days=365)
_CURSOR_PREFIX = "cursor:"
QUERY_COST = 1


@cache
def _schema() -> GraphQLSchema:
    text = files("fake_backends.server.github").joinpath("schema.graphql").read_text()
    schema = build_schema(text)
    schema.type_map["SearchResultItem"].resolve_type = lambda value, *_: (
        "PullRequest" if value["__kind"] == "pull" else "Issue"
    )
    for name in ("RepositoryOwner", "Actor"):
        schema.type_map[name].resolve_type = lambda value, *_: value["__typename"]
    return schema


class _Error(GraphQLError):
    """A resolver error that GitHub reports with a ``type``."""

    def __init__(self, message: str, kind: str | None = None) -> None:
        super().__init__(message, extensions={"type": kind} if kind else None)


def _cursor(index: int) -> str:
    return base64.b64encode(f"{_CURSOR_PREFIX}{index}".encode()).decode()


def _offset(after: str | None) -> int:
    if after is None:
        return 0
    try:
        decoded = base64.b64decode(after).decode()
    except ValueError, UnicodeDecodeError:
        raise _Error(f"`{after}` does not appear to be a valid cursor.") from None
    if not decoded.startswith(_CURSOR_PREFIX):
        raise _Error(f"`{after}` does not appear to be a valid cursor.")
    try:
        return int(decoded.removeprefix(_CURSOR_PREFIX))
    except ValueError:
        raise _Error(f"`{after}` does not appear to be a valid cursor.") from None


def _page(
    items: list[Any], first: int | None, after: str | None, connection: str
) -> dict[str, Any]:
    if first is None:
        raise _Error(
            "You must provide a `first` or `last` value to properly paginate "
            f"the `{connection}` connection."
        )
    if first > MAX_PAGE_SIZE:
        raise _Error(
            f"Requesting {first} records on the `{connection}` connection exceeds the "
            f"`first` limit of {MAX_PAGE_SIZE} records."
        )
    start = _offset(after)
    window = items[start : start + first]
    end = start + len(window)
    return {
        "nodes": window,
        "pageInfo": {
            "startCursor": _cursor(start + 1) if window else None,
            "endCursor": _cursor(end) if window else None,
            "hasNextPage": end < len(items),
            "hasPreviousPage": start > 0,
        },
    }


# --- object views ----------------------------------------------------------


class _Views:
    """Turns model objects into the dicts graphql-core resolves fields from."""

    def __init__(
        self, state: GitHubState, urls: shapes.Urls, identity: str, rate: RateSnapshot
    ) -> None:
        self.state = state
        self.urls = urls
        self.identity = identity
        self.rate = rate

    def owner(self, user: User) -> dict[str, Any]:
        def repositories(
            info, first=None, after=None, isArchived=None, isFork=None, **_
        ):
            repos = [
                r
                for r in self.state.repos_of(user.login)
                if (isArchived is None or r.archived == isArchived)
                and (isFork is None or r.fork == isFork)
            ]
            page = _page(repos, first, after, "repositories")
            page["nodes"] = [self.repo(r) for r in page["nodes"]]
            page["totalCount"] = len(repos)
            return page

        view: dict[str, Any] = {
            "__typename": "Organization" if user.is_org else "User",
            "login": user.login,
            "repositories": repositories,
        }
        if not user.is_org:
            view["contributionsCollection"] = lambda info, **args: self.contributions(
                user, **args
            )
        return view

    def contributions(self, user: User, **args: Any) -> dict[str, Any]:
        to = _datetime(args.get("to")) or dt.datetime.now(dt.UTC)
        start = _datetime(args.get("from")) or to - DEFAULT_CONTRIBUTION_SPAN
        if to - start > MAX_CONTRIBUTION_SPAN:
            raise _Error(
                "The total time spanned by 'from' and 'to' must not exceed 1 year"
            )
        totals = dict.fromkeys(ContributionKind, 0)
        for entry in user.contributions:
            if start.date() <= entry.date <= to.date():
                totals[entry.kind] += entry.count
        return {
            "contributionCalendar": {"totalContributions": sum(totals.values())},
            "totalCommitContributions": totals[ContributionKind.COMMIT],
            "totalPullRequestContributions": totals[ContributionKind.PULL_REQUEST],
            "totalPullRequestReviewContributions": totals[
                ContributionKind.PULL_REQUEST_REVIEW
            ],
            "totalIssueContributions": totals[ContributionKind.ISSUE],
            "totalRepositoryContributions": totals[ContributionKind.REPOSITORY],
            "restrictedContributionsCount": totals[ContributionKind.RESTRICTED],
        }

    def repo(self, repo: Repo) -> dict[str, Any]:
        def pull_request(info, number):
            pr = repo.pulls.get(number)
            if pr is None:
                raise _Error(
                    f"Could not resolve to a PullRequest with the number of {number}.",
                    "NOT_FOUND",
                )
            return self.pull(repo, pr)

        return {
            "name": repo.name,
            "nameWithOwner": repo.full_name,
            "isArchived": repo.archived,
            "url": f"{self.urls.web}/{repo.full_name}",
            "owner": lambda info: self.owner(self.state.ensure_user(repo.owner)),
            "pullRequest": pull_request,
        }

    def pull(self, repo: Repo, pr: Pull) -> dict[str, Any]:
        def reviews(info, first=None, after=None, states=None, **_):
            wanted = [r for r in pr.reviews if states is None or r.state in states]
            page = _page(wanted, first, after, "reviews")
            page["nodes"] = [
                {"state": r.state, "author": {"__typename": "User", "login": r.author}}
                for r in page["nodes"]
            ]
            page["totalCount"] = len(wanted)
            return page

        state = "MERGED" if pr.merged else pr.state.upper()
        return {
            "__kind": "pull",
            "number": pr.number,
            "title": pr.title,
            "url": f"{self.urls.web}/{repo.full_name}/pull/{pr.number}",
            "state": state,
            "isDraft": pr.draft,
            "repository": lambda info: self.repo(repo),
            "reviewDecision": pr.review_decision,
            "reviews": reviews,
        }

    def issue(self, repo: Repo, issue: Issue) -> dict[str, Any]:
        return {
            "__kind": "issue",
            "number": issue.number,
            "title": issue.title,
            "url": f"{self.urls.web}/{repo.full_name}/issues/{issue.number}",
            "state": issue.state.upper(),
            "repository": lambda info: self.repo(repo),
        }

    def root(self) -> dict[str, Any]:
        def search_field(info, query, type, first=None, after=None, **_):
            if type != "ISSUE":
                raise _Error(f"The fake only searches issues, not {type}.")
            try:
                hits = search(self.state, query, self.identity)
            except SearchError as exc:
                raise _Error(str(exc)) from None
            page = _page(hits[:RESULT_CAP], first, after, "search")
            page["nodes"] = [
                self.pull(h.repo, h.item) if h.is_pr else self.issue(h.repo, h.item)
                for h in page["nodes"]
            ]
            page["issueCount"] = len(hits)
            return page

        def repository_owner(info, login):
            # Any account the seed names exists; anything else is null, with no
            # error, as on GitHub.
            user = self.state.user(login)
            return None if user is None else self.owner(user)

        def repository(info, owner, name, **_):
            repo = self.state.repo(owner, name)
            if repo is None:
                raise _Error(
                    "Could not resolve to a Repository with the name "
                    f"'{owner}/{name}'.",
                    "NOT_FOUND",
                )
            return self.repo(repo)

        def user(info, login):
            found = self.state.user(login)
            if found is None or found.is_org:
                raise _Error(
                    f"Could not resolve to a User with the login of '{login}'.",
                    "NOT_FOUND",
                )
            return self.owner(found)

        def rate_limit(info, **_):
            return {
                "cost": QUERY_COST,
                "limit": self.rate.limit,
                "nodeCount": 0,
                "remaining": self.rate.remaining,
                "resetAt": shapes.timestamp(
                    dt.datetime.fromtimestamp(self.rate.reset, dt.UTC)
                ),
                "used": self.rate.used,
            }

        return {
            "search": search_field,
            "repositoryOwner": repository_owner,
            "repository": repository,
            "user": user,
            "rateLimit": rate_limit,
        }


def _datetime(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise _Error(f"{value!r} is not a valid DateTime") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _format(error: GraphQLError) -> dict[str, Any]:
    formatted = dict(error.formatted)
    extensions = formatted.pop("extensions", None) or {}
    if "type" in extensions:
        formatted["type"] = extensions["type"]
    return formatted


def graphql_error(kind: str, message: str) -> JSONResponse:
    """A GraphQL response carrying one error of ``kind``, as GitHub sends it."""
    return JSONResponse({"data": None, "errors": [{"type": kind, "message": message}]})


async def _endpoint(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            {
                "message": "Problems parsing JSON",
                "documentation_url": "https://docs.github.com/graphql",
            },
            status_code=HTTPStatus.BAD_REQUEST,
        )
    query = body.get("query") if isinstance(body, dict) else None
    if not isinstance(query, str):
        return JSONResponse(
            {
                "errors": [
                    {
                        "message": "A query attribute must be specified "
                        "and must be a string."
                    }
                ]
            },
            status_code=HTTPStatus.OK,
        )
    state: GitHubState = request.app.state.store.data
    identity: str = request.state.identity
    snapshot = spend(state, identity, Bucket.GRAPHQL, QUERY_COST)
    if not snapshot.allowed:
        user = state.ensure_user(identity)
        response = graphql_error(
            "RATE_LIMITED", f"API rate limit exceeded for user ID {user.id}."
        )
    else:
        web = f"{request.url.scheme}://{request.url.netloc}"
        views = _Views(state, shapes.Urls(api=web, web=web), identity, snapshot)
        result = await graphql(
            _schema(),
            query,
            root_value=views.root(),
            variable_values=body.get("variables") or None,
            operation_name=body.get("operationName"),
        )
        payload: dict[str, Any] = {}
        if result.errors:
            payload["errors"] = [_format(e) for e in result.errors]
        if result.data is not None or not result.errors:
            payload["data"] = result.data
        response = JSONResponse(payload)
    response.headers.update(snapshot.headers())
    return response


def operation_details(body: bytes) -> dict[str, Any]:
    """Root fields, aliases and operation name, for the journal and fault rules."""
    try:
        payload = json.loads(body)
        document = parse(payload["query"])
    except ValueError, KeyError, TypeError, GraphQLSyntaxError:
        return {}
    fields, aliases, operation = [], [], None
    for definition in document.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            continue
        operation = operation or (definition.name.value if definition.name else None)
        for selection in definition.selection_set.selections:
            name = getattr(selection, "name", None)
            if name is None:
                continue
            fields.append(name.value)
            alias = getattr(selection, "alias", None)
            if alias is not None:
                aliases.append(alias.value)
    return {
        "graphql_operation": payload.get("operationName") or operation,
        "graphql_fields": sorted(set(fields)),
        "graphql_aliases": aliases,
    }


def graphql_routes() -> list[BaseRoute]:
    return [Route(path, _endpoint, methods=["POST"]) for path in GRAPHQL_PATHS]
