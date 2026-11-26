"""fake-github's REST API, served at the root and again under ``/api/v3``."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from fake_backends.server.github import shapes
from fake_backends.server.github.model import (
    Bucket,
    Comment,
    GitHubState,
    Issue,
    Pull,
    Repo,
)
from fake_backends.server.github.ratelimit import spend
from fake_backends.server.github.search import (
    RESULT_CAP,
    SearchError,
    parse_sort,
    search,
)

__all__ = [
    "DOCS_URL",
    "ENTERPRISE_PREFIX",
    "PER_PAGE_DEFAULT",
    "PER_PAGE_MAX",
    "download_routes",
    "rest_routes",
]

ENTERPRISE_PREFIX = "/api/v3"
DOCS_URL = "https://docs.github.com/rest"
RATE_LIMIT_DOCS_URL = (
    "https://docs.github.com/rest/overview/rate-limits-for-the-rest-api"
)
# GitHub's page size when none is asked for, and the largest it allows.
PER_PAGE_DEFAULT = 30
PER_PAGE_MAX = 100
_FIRST_PAGE = 1

Handler = Callable[[Request], Awaitable[Response]]


def _error(status: HTTPStatus, message: str, docs: str = DOCS_URL) -> JSONResponse:
    return JSONResponse(
        {"message": message, "documentation_url": docs, "status": str(status.value)},
        status_code=status,
    )


def _not_found() -> JSONResponse:
    return _error(HTTPStatus.NOT_FOUND, "Not Found")


def _state(request: Request) -> GitHubState:
    return request.app.state.store.data


def _urls(request: Request) -> shapes.Urls:
    web = f"{request.url.scheme}://{request.url.netloc}"
    return shapes.Urls(api=web + request.scope.get("root_path", ""), web=web)


def _limited(bucket: Bucket, handler: Handler) -> Handler:
    """Spend from ``bucket`` for the caller, and answer 403 once it is empty."""

    async def wrapped(request: Request) -> Response:
        identity = request.state.identity
        if identity is None:  # a public route, called without a token
            return await handler(request)
        state = _state(request)
        snapshot = spend(state, identity, bucket)
        if not snapshot.allowed:
            user = state.ensure_user(identity)
            response: Response = _error(
                HTTPStatus.FORBIDDEN,
                f"API rate limit exceeded for user ID {user.id}.",
                RATE_LIMIT_DOCS_URL,
            )
        else:
            response = await handler(request)
        response.headers.update(snapshot.headers())
        return response

    return wrapped


def _repo(request: Request) -> Repo | None:
    params = request.path_params
    return _state(request).repo(params["owner"], params["repo"])


def _page_args(request: Request) -> tuple[int, int] | JSONResponse:
    try:
        per_page = int(request.query_params.get("per_page", PER_PAGE_DEFAULT))
        page = int(request.query_params.get("page", _FIRST_PAGE))
    except ValueError:
        return _error(HTTPStatus.UNPROCESSABLE_ENTITY, "Invalid page or per_page")
    return min(max(per_page, 1), PER_PAGE_MAX), max(page, _FIRST_PAGE)


def _link_header(request: Request, page: int, last: int) -> str | None:
    links = []
    for rel, number in (
        ("prev", page - 1),
        ("next", page + 1),
        ("first", 1),
        ("last", last),
    ):
        if rel == "prev" and page <= _FIRST_PAGE:
            continue
        if rel == "next" and page >= last:
            continue
        if rel in {"first", "last"} and last <= _FIRST_PAGE:
            continue
        query = dict(request.query_params)
        query["page"] = str(number)
        links.append(f'<{request.url.replace(query=urlencode(query))}>; rel="{rel}"')
    return ", ".join(links) or None


def _paged(
    request: Request, items: list[Any], status: HTTPStatus = HTTPStatus.OK
) -> Response:
    args = _page_args(request)
    if isinstance(args, JSONResponse):
        return args
    per_page, page = args
    start = (page - 1) * per_page
    response = JSONResponse(items[start : start + per_page], status_code=status)
    link = _link_header(request, page, max(math.ceil(len(items) / per_page), 1))
    if link:
        response.headers["Link"] = link
    return response


# --- endpoints -------------------------------------------------------------


async def _user(request: Request) -> Response:
    return JSONResponse(
        shapes.private_user(_state(request), _urls(request), request.state.identity)
    )


def _pull_of(request: Request) -> tuple[Repo, Pull] | None:
    repo = _repo(request)
    if repo is None:
        return None
    pr = repo.pulls.get(request.path_params["number"])
    return None if pr is None else (repo, pr)


async def _pull(request: Request) -> Response:
    found = _pull_of(request)
    if found is None:
        return _not_found()
    return JSONResponse(shapes.pull(_state(request), _urls(request), *found))


async def _reviews(request: Request) -> Response:
    found = _pull_of(request)
    if found is None:
        return _not_found()
    repo, pr = found
    state, urls = _state(request), _urls(request)
    return _paged(
        request, [shapes.review(state, urls, repo, pr, r) for r in pr.reviews]
    )


async def _protection(request: Request) -> Response:
    repo = _repo(request)
    if repo is None:
        return _not_found()
    branch = request.path_params["branch"]
    rules = repo.protection.get(branch)
    if rules is None:
        return _error(HTTPStatus.NOT_FOUND, "Branch not protected")
    return JSONResponse(shapes.protection(_urls(request), repo, branch, rules))


def _pulls_at(repo: Repo, ref: str) -> list[Pull]:
    return [p for p in repo.pulls.values() if ref in {p.head_sha, p.head}]


async def _check_runs(request: Request) -> Response:
    repo = _repo(request)
    if repo is None:
        return _not_found()
    ref = request.path_params["ref"]
    pulls = _pulls_at(repo, ref)
    if not pulls:
        return _error(
            HTTPStatus.UNPROCESSABLE_ENTITY, f"No commit found for SHA: {ref}"
        )
    runs = [run for p in pulls for run in p.check_runs]
    return JSONResponse(
        shapes.check_runs(
            _state(request), _urls(request), repo, pulls[0].head_sha, runs
        )
    )


async def _status(request: Request) -> Response:
    repo = _repo(request)
    if repo is None:
        return _not_found()
    ref = request.path_params["ref"]
    pulls = _pulls_at(repo, ref)
    if not pulls:
        return _error(
            HTTPStatus.UNPROCESSABLE_ENTITY, f"No commit found for SHA: {ref}"
        )
    statuses = [s for p in pulls for s in p.statuses]
    return JSONResponse(
        shapes.combined_status(
            _state(request), _urls(request), repo, pulls[0].head_sha, statuses
        )
    )


async def _search_issues(request: Request) -> Response:
    text = request.query_params.get("q")
    if not text:
        return _error(HTTPStatus.UNPROCESSABLE_ENTITY, "Validation Failed")
    args = _page_args(request)
    if isinstance(args, JSONResponse):
        return args
    per_page, page = args
    if (page - 1) * per_page >= RESULT_CAP:
        return _error(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            f"Only the first {RESULT_CAP} search results are available",
        )
    state, urls = _state(request), _urls(request)
    try:
        sort = parse_sort(
            request.query_params.get("sort"), request.query_params.get("order")
        )
        hits = search(state, text, request.state.identity, sort)
    except SearchError as exc:
        return _error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
    start = (page - 1) * per_page
    window = hits[:RESULT_CAP][start : start + per_page]
    return JSONResponse(
        {
            "total_count": len(hits),
            "incomplete_results": False,
            # The fake matches words literally, which is GitHub's lexical search.
            "search_type": "lexical",
            "items": [shapes.search_item(state, urls, h.repo, h.item) for h in window],
        }
    )


def _item_of(request: Request) -> tuple[Repo, Pull | Issue] | None:
    repo = _repo(request)
    if repo is None:
        return None
    number = request.path_params["number"]
    item = repo.issues.get(number) or repo.pulls.get(number)
    return None if item is None else (repo, item)


async def _list_issues(request: Request) -> Response:
    repo = _repo(request)
    if repo is None:
        return _not_found()
    wanted = request.query_params.get("state", "open")
    items = [
        i
        for i in [*repo.issues.values(), *repo.pulls.values()]
        if wanted == "all" or i.state == wanted
    ]
    items.sort(key=lambda i: (i.created_at, i.number), reverse=True)
    state, urls = _state(request), _urls(request)
    return _paged(request, [shapes.issue(state, urls, repo, i) for i in items])


async def _get_issue(request: Request) -> Response:
    found = _item_of(request)
    if found is None:
        return _not_found()
    return JSONResponse(shapes.issue(_state(request), _urls(request), *found))


def _validation_failed(field: str, code: str = "missing_field") -> JSONResponse:
    return JSONResponse(
        {
            "message": "Validation Failed",
            "errors": [{"resource": "Issue", "field": field, "code": code}],
            "documentation_url": DOCS_URL,
            "status": str(HTTPStatus.UNPROCESSABLE_ENTITY.value),
        },
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
    )


async def _json_body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return _error(HTTPStatus.BAD_REQUEST, "Problems parsing JSON")
    if not isinstance(body, dict):
        return _error(HTTPStatus.BAD_REQUEST, "Problems parsing JSON")
    return body


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


async def _create_issue(request: Request) -> Response:
    repo = _repo(request)
    if repo is None:
        return _not_found()
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    if not body.get("title"):
        return _validation_failed("title")
    state = _state(request)
    now = _now()
    assignees = [str(a) for a in body.get("assignees") or []]
    for login in assignees:
        state.ensure_user(login)
    issue = Issue(
        id=state.new_id(),
        number=repo.next_number(),
        title=str(body["title"]),
        body=body.get("body"),
        author=request.state.identity,
        state="open",
        state_reason=None,
        labels=[str(label) for label in body.get("labels") or []],
        assignees=assignees,
        created_at=now,
        updated_at=now,
        closed_at=None,
        comments=[],
    )
    repo.issues[issue.number] = issue
    return JSONResponse(
        shapes.issue(state, _urls(request), repo, issue), status_code=HTTPStatus.CREATED
    )


_EDITABLE = ("title", "body")
_STATE_REASONS = {"completed", "not_planned", "reopened", None}


async def _update_issue(request: Request) -> Response:
    found = _item_of(request)
    if found is None:
        return _not_found()
    repo, item = found
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    state = _state(request)
    if "state" in body and body["state"] not in {"open", "closed"}:
        return _validation_failed("state", "invalid")
    if "state_reason" in body and body["state_reason"] not in _STATE_REASONS:
        return _validation_failed("state_reason", "invalid")
    now = _now()
    for key in _EDITABLE:
        if key in body:
            setattr(item, key, body[key])
    if "labels" in body:
        item.labels = [
            str(label["name"] if isinstance(label, dict) else label)
            for label in body["labels"] or []
        ]
    if "assignees" in body:
        item.assignees = [str(a) for a in body["assignees"] or []]
        for login in item.assignees:
            state.ensure_user(login)
    if "state" in body and body["state"] != item.state:
        item.state = body["state"]
        item.closed_at = now if item.state == "closed" else None
        if isinstance(item, Issue):
            default_reason = "completed" if item.state == "closed" else "reopened"
            item.state_reason = body.get("state_reason", default_reason)
    elif isinstance(item, Issue) and "state_reason" in body:
        item.state_reason = body["state_reason"]
    item.updated_at = now
    return JSONResponse(shapes.issue(state, _urls(request), repo, item))


async def _list_comments(request: Request) -> Response:
    found = _item_of(request)
    if found is None:
        return _not_found()
    repo, item = found
    comments = item.comments if isinstance(item, Issue) else []
    state, urls = _state(request), _urls(request)
    return _paged(
        request, [shapes.comment(state, urls, repo, item.number, c) for c in comments]
    )


async def _add_comment(request: Request) -> Response:
    found = _item_of(request)
    if found is None:
        return _not_found()
    repo, item = found
    if not isinstance(item, Issue):
        # Comments on pull requests are not modelled; no consumer adds them.
        return _not_found()
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    if not body.get("body"):
        return _validation_failed("body")
    state = _state(request)
    now = _now()
    created = Comment(
        id=state.new_id(),
        author=request.state.identity,
        body=str(body["body"]),
        created_at=now,
        updated_at=now,
    )
    item.comments.append(created)
    item.updated_at = now
    return JSONResponse(
        shapes.comment(state, _urls(request), repo, item.number, created),
        status_code=HTTPStatus.CREATED,
    )


def _latest_release(repo: Repo):
    published = [
        r for r in repo.releases if not r.draft and not r.prerelease and r.published_at
    ]
    return max(published, key=lambda r: (r.published_at, r.id), default=None)


async def _release_latest(request: Request) -> Response:
    repo = _repo(request)
    release = None if repo is None else _latest_release(repo)
    if release is None:
        return _not_found()
    return JSONResponse(shapes.release(_state(request), _urls(request), repo, release))


def rest_routes() -> list[BaseRoute]:
    core, search_bucket = Bucket.CORE, Bucket.SEARCH
    repo = "/repos/{owner}/{repo}"
    return [
        Route("/user", _limited(core, _user)),
        Route(f"{repo}/pulls/{{number:int}}", _limited(core, _pull)),
        Route(f"{repo}/pulls/{{number:int}}/reviews", _limited(core, _reviews)),
        Route(
            f"{repo}/branches/{{branch:path}}/protection/required_pull_request_reviews",
            _limited(core, _protection),
        ),
        Route(f"{repo}/commits/{{ref}}/check-runs", _limited(core, _check_runs)),
        Route(f"{repo}/commits/{{ref}}/status", _limited(core, _status)),
        Route("/search/issues", _limited(search_bucket, _search_issues)),
        Route(f"{repo}/issues", _limited(core, _list_issues), methods=["GET"]),
        Route(f"{repo}/issues", _limited(core, _create_issue), methods=["POST"]),
        Route(
            f"{repo}/issues/{{number:int}}", _limited(core, _get_issue), methods=["GET"]
        ),
        Route(
            f"{repo}/issues/{{number:int}}",
            _limited(core, _update_issue),
            methods=["PATCH"],
        ),
        Route(
            f"{repo}/issues/{{number:int}}/comments",
            _limited(core, _list_comments),
            methods=["GET"],
        ),
        Route(
            f"{repo}/issues/{{number:int}}/comments",
            _limited(core, _add_comment),
            methods=["POST"],
        ),
        Route(f"{repo}/releases/latest", _limited(core, _release_latest)),
    ]


# --- release asset downloads (github.com web paths, no token needed) -----


async def _download(request: Request) -> Response:
    repo = _repo(request)
    if repo is None:
        return Response("Not Found", status_code=HTTPStatus.NOT_FOUND)
    tag = request.path_params.get("tag")
    release = (
        _latest_release(repo)
        if tag is None
        else next((r for r in repo.releases if r.tag == tag), None)
    )
    name = request.path_params["name"]
    asset = (
        None
        if release is None
        else next((a for a in release.assets if a.name == name), None)
    )
    if asset is None:
        return Response("Not Found", status_code=HTTPStatus.NOT_FOUND)
    return Response(asset.content, media_type=asset.content_type)


DOWNLOAD_PATTERNS = (
    "/{owner}/{repo}/releases/latest/download/{name}",
    "/{owner}/{repo}/releases/download/{tag}/{name}",
)


def download_routes() -> list[BaseRoute]:
    return [Route(pattern, _download) for pattern in DOWNLOAD_PATTERNS]
