"""fake-jira's REST API, under ``/rest/api/3`` (Cloud) or ``/rest/api/2`` (Server)."""

from __future__ import annotations

import base64
import datetime as dt
from collections.abc import Callable
from http import HTTPStatus
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from fake_backends.server.jira import shapes
from fake_backends.server.jira.adf import is_adf
from fake_backends.server.jira.jql import JqlError, parse
from fake_backends.server.jira.model import (
    DEFAULT_PRIORITY,
    Comment,
    Issue,
    JiraState,
    Mode,
    User,
)

__all__ = [
    "API_PREFIXES",
    "CLOUD_MAX_RESULTS",
    "DEFAULT_MAX_RESULTS",
    "SERVER_MAX_RESULTS",
    "api_routes",
]

API_PREFIXES = {Mode.CLOUD: "/rest/api/3", Mode.SERVER: "/rest/api/2"}
# JIRA's page size when none is asked for, and the largest each mode allows.
DEFAULT_MAX_RESULTS = 50
CLOUD_MAX_RESULTS = 5000
SERVER_MAX_RESULTS = 1000
_NEXT_PAGE_PREFIX = "page:"


def _error(status: HTTPStatus, *messages: str, **errors: str) -> JSONResponse:
    return JSONResponse(shapes.error(messages, errors), status_code=status)


def _no_issue() -> JSONResponse:
    return _error(
        HTTPStatus.NOT_FOUND,
        "Issue does not exist or you do not have permission to see it.",
    )


def _state(request: Request) -> JiraState:
    return request.app.state.store.data


def _urls(request: Request) -> tuple[str, str]:
    base = f"{request.url.scheme}://{request.url.netloc}"
    return base, base + API_PREFIXES[_state(request).mode]


def _me(request: Request) -> User:
    return _state(request).ensure_user(request.state.identity)


def _fields(request: Request, default: tuple[str, ...]) -> list[str]:
    raw = request.query_params.get("fields")
    if raw is None:
        return list(default)
    names: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if part in {"*all", "*navigable"}:
            names += shapes.ALL_FIELDS
        elif part and not part.startswith("-"):
            names.append(part)
    return names


async def _json(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return _error(
            HTTPStatus.BAD_REQUEST, "Unexpected character in the request body."
        )
    if not isinstance(body, dict):
        return _error(HTTPStatus.BAD_REQUEST, "The request body must be a JSON object.")
    return body


def _body_problem(state: JiraState, value: Any) -> str | None:
    """Why a description or comment body is wrong for this mode, if it is."""
    if state.mode is Mode.CLOUD and not is_adf(value):
        return (
            "Operation value must be an Atlassian Document "
            "(see the Atlassian Document Format)"
        )
    if state.mode is Mode.SERVER and not isinstance(value, str):
        return "Operation value must be a string"
    return None


def _person(state: JiraState, ref: Any) -> tuple[User | None, str | None]:
    """The user a ``{"accountId"}`` (Cloud) or ``{"name"}`` (Server) names.

    Returns (user, problem). A null id is (None, None): unassigned.
    """
    key = "accountId" if state.mode is Mode.CLOUD else "name"
    if not isinstance(ref, dict) or key not in ref:
        return None, f"Specify the user by {key}."
    value = ref[key]
    if value is None:
        return None, None
    found = (
        state.user_by_account(str(value))
        if state.mode is Mode.CLOUD
        else state.user(str(value))
    )
    if found is None:
        return None, f"User '{value}' does not exist."
    return found, None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --- endpoints -------------------------------------------------------------


async def _myself(request: Request) -> Response:
    base, api = _urls(request)
    return JSONResponse(shapes.user(_state(request), base, api, _me(request)))


def _query(request: Request):
    text = request.query_params.get("jql", "")
    try:
        query = parse(text)
    except JqlError as exc:
        return _error(HTTPStatus.BAD_REQUEST, str(exc))
    return query


def _matching(request: Request, query) -> list[Issue]:
    state = _state(request)
    identity = request.state.identity
    found = [i for i in state.issues.values() if query.matches(i, identity)]
    order = query.order or [("key", False)]
    for name, descending in reversed(order):
        key: Callable[[Issue], Any] = {
            "updated": lambda i: i.updated,
            "created": lambda i: i.created,
            "key": lambda i: (i.project, i.number),
            "priority": lambda i: i.priority,
            "status": lambda i: i.status.name,
        }[name]
        found.sort(key=key, reverse=descending)
    return found


def _max_results(request: Request, ceiling: int) -> int | JSONResponse:
    try:
        wanted = int(request.query_params.get("maxResults", DEFAULT_MAX_RESULTS))
    except ValueError:
        return _error(HTTPStatus.BAD_REQUEST, "maxResults must be a number.")
    return min(max(wanted, 1), ceiling)


async def _search_jql(request: Request) -> Response:
    """Cloud: ``GET /search/jql``, paged with ``nextPageToken``."""
    query = _query(request)
    if isinstance(query, JSONResponse):
        return query
    if not query.bounded:
        return _error(
            HTTPStatus.BAD_REQUEST,
            "Unbounded JQL queries are not allowed here. "
            "Please add a search restriction to your query.",
        )
    limit = _max_results(request, CLOUD_MAX_RESULTS)
    if isinstance(limit, JSONResponse):
        return limit
    token = request.query_params.get("nextPageToken")
    try:
        start = (
            0
            if token is None
            else int(
                base64.urlsafe_b64decode(token).decode().removeprefix(_NEXT_PAGE_PREFIX)
            )
        )
    except ValueError, UnicodeDecodeError:
        return _error(HTTPStatus.BAD_REQUEST, "The nextPageToken is not valid.")
    found = _matching(request, query)
    window = found[start : start + limit]
    end = start + len(window)
    # The new search returns only ids unless fields are asked for.
    fields = _fields(request, ())
    state = _state(request)
    base, api = _urls(request)
    body: dict[str, Any] = {
        "issues": [shapes.issue(state, base, api, i, fields) for i in window],
        "isLast": end >= len(found),
    }
    if end < len(found):
        body["nextPageToken"] = base64.urlsafe_b64encode(
            f"{_NEXT_PAGE_PREFIX}{end}".encode()
        ).decode()
    return JSONResponse(body)


async def _search(request: Request) -> Response:
    """Server: ``GET /search``, paged with ``startAt`` and ``total``."""
    query = _query(request)
    if isinstance(query, JSONResponse):
        return query
    limit = _max_results(request, SERVER_MAX_RESULTS)
    if isinstance(limit, JSONResponse):
        return limit
    try:
        start = max(int(request.query_params.get("startAt", 0)), 0)
    except ValueError:
        return _error(HTTPStatus.BAD_REQUEST, "startAt must be a number.")
    found = _matching(request, query)
    fields = _fields(request, shapes.ALL_FIELDS)
    state = _state(request)
    base, api = _urls(request)
    return JSONResponse(
        {
            "expand": "schema,names",
            "startAt": start,
            "maxResults": limit,
            "total": len(found),
            "issues": [
                shapes.issue(state, base, api, i, fields)
                for i in found[start : start + limit]
            ],
        }
    )


def _issue_of(request: Request) -> Issue | None:
    key = request.path_params["key"].upper()
    state = _state(request)
    found = state.issues.get(key)
    if found is None:  # an id works too
        found = next((i for i in state.issues.values() if i.id == key), None)
    return found


async def _get_issue(request: Request) -> Response:
    found = _issue_of(request)
    if found is None:
        return _no_issue()
    base, api = _urls(request)
    fields = _fields(request, shapes.ALL_FIELDS)
    return JSONResponse(shapes.issue(_state(request), base, api, found, fields))


async def _create_issue(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, JSONResponse):
        return body
    fields = body.get("fields")
    if not isinstance(fields, dict):
        return _error(HTTPStatus.BAD_REQUEST, "The request needs fields.")
    state = _state(request)
    problems: dict[str, str] = {}
    project = state.projects.get(
        str((fields.get("project") or {}).get("key", "")).upper()
    )
    if project is None:
        problems["project"] = "valid project is required"
    if not fields.get("summary"):
        problems["summary"] = "You must specify a summary of the issue."
    type_name = str((fields.get("issuetype") or {}).get("name", ""))
    if project is not None and type_name not in project.issue_types:
        problems["issuetype"] = "Specify a valid issue type"
    if fields.get("description") is not None:
        problem = _body_problem(state, fields["description"])
        if problem:
            problems["description"] = problem
    assignee = None
    if "assignee" in fields:
        assignee, problem = _person(state, fields["assignee"])
        if problem:
            problems["assignee"] = problem
    if problems:
        return _error(HTTPStatus.BAD_REQUEST, **problems)
    now = _now()
    number = max((i.number for i in state.project_issues(project.key)), default=0) + 1
    issue = Issue(
        id=state.new_id(),
        key=f"{project.key}-{number}",
        project=project.key,
        number=number,
        summary=str(fields["summary"]),
        description=fields.get("description"),
        type=type_name,
        status=project.initial_status,
        assignee=assignee.name if assignee else None,
        reporter=_me(request).name,
        priority=DEFAULT_PRIORITY,
        labels=[str(label) for label in fields.get("labels") or []],
        created=now,
        updated=now,
        comments=[],
    )
    state.issues[issue.key] = issue
    _, api = _urls(request)
    return JSONResponse(
        {"id": issue.id, "key": issue.key, "self": f"{api}/issue/{issue.id}"},
        status_code=HTTPStatus.CREATED,
    )


async def _edit_issue(request: Request) -> Response:
    found = _issue_of(request)
    if found is None:
        return _no_issue()
    body = await _json(request)
    if isinstance(body, JSONResponse):
        return body
    state = _state(request)
    fields = body.get("fields") or {}
    update = body.get("update") or {}
    problems: dict[str, str] = {}
    if "summary" in fields and not fields["summary"]:
        problems["summary"] = "You must specify a summary of the issue."
    if fields.get("description") is not None:
        problem = _body_problem(state, fields["description"])
        if problem:
            problems["description"] = problem
    for operation in update.get("labels") or []:
        if not isinstance(operation, dict) or not set(operation) <= {
            "add",
            "remove",
            "set",
        }:
            problems["labels"] = "Label operations must be add, remove or set."
    unknown = sorted((set(fields) | set(update)) - {"summary", "description", "labels"})
    for name in unknown:
        problems[name] = (
            f"Field '{name}' cannot be set. "
            "It is not on the appropriate screen, or unknown."
        )
    if problems:
        return _error(HTTPStatus.BAD_REQUEST, **problems)
    if "summary" in fields:
        found.summary = str(fields["summary"])
    if "description" in fields:
        found.description = fields["description"]
    if "labels" in fields:
        found.labels = [str(label) for label in fields["labels"] or []]
    for operation in update.get("labels") or []:
        if "set" in operation:
            found.labels = [str(label) for label in operation["set"] or []]
        if "add" in operation and operation["add"] not in found.labels:
            found.labels.append(str(operation["add"]))
        if "remove" in operation and operation["remove"] in found.labels:
            found.labels.remove(operation["remove"])
    found.updated = _now()
    return Response(status_code=HTTPStatus.NO_CONTENT)


async def _add_comment(request: Request) -> Response:
    found = _issue_of(request)
    if found is None:
        return _no_issue()
    body = await _json(request)
    if isinstance(body, JSONResponse):
        return body
    state = _state(request)
    if "body" not in body:
        return _error(HTTPStatus.BAD_REQUEST, body="Comment body can not be empty!")
    problem = _body_problem(state, body["body"])
    if problem:
        return _error(HTTPStatus.BAD_REQUEST, comment=problem)
    now = _now()
    created = Comment(
        id=state.new_id(),
        author=_me(request).name,
        body=body["body"],
        created=now,
        updated=now,
    )
    found.comments.append(created)
    found.updated = now
    base, api = _urls(request)
    return JSONResponse(
        shapes.comment(state, base, api, found, created), status_code=HTTPStatus.CREATED
    )


async def _assign(request: Request) -> Response:
    found = _issue_of(request)
    if found is None:
        return _no_issue()
    body = await _json(request)
    if isinstance(body, JSONResponse):
        return body
    person, problem = _person(_state(request), body)
    if problem:
        return _error(HTTPStatus.BAD_REQUEST, assignee=problem)
    found.assignee = person.name if person else None
    found.updated = _now()
    return Response(status_code=HTTPStatus.NO_CONTENT)


async def _transitions(request: Request) -> Response:
    found = _issue_of(request)
    if found is None:
        return _no_issue()
    project = _state(request).projects[found.project]
    base, api = _urls(request)
    return JSONResponse(
        shapes.transitions(base, api, project, project.available(found.status))
    )


async def _do_transition(request: Request) -> Response:
    found = _issue_of(request)
    if found is None:
        return _no_issue()
    body = await _json(request)
    if isinstance(body, JSONResponse):
        return body
    wanted = str((body.get("transition") or {}).get("id", ""))
    project = _state(request).projects[found.project]
    transition = next(
        (t for t in project.available(found.status) if t.id == wanted), None
    )
    if transition is None:
        return _error(
            HTTPStatus.BAD_REQUEST,
            f"Transition id '{wanted}' is not valid for this issue.",
        )
    found.status = project.statuses[transition.to.lower()]
    found.updated = _now()
    return Response(status_code=HTTPStatus.NO_CONTENT)


async def _user_search(request: Request) -> Response:
    """Cloud only: users whose name, display name or email contains ``query``."""
    text = request.query_params.get("query")
    if not text:
        return _error(
            HTTPStatus.BAD_REQUEST, "One of 'accountId' or 'query' must be specified."
        )
    wanted = text.lower()
    state = _state(request)
    base, api = _urls(request)
    found = [
        shapes.user(state, base, api, u)
        for u in sorted(state.users.values(), key=lambda u: u.name.lower())
        if wanted in u.name.lower()
        or wanted in u.display_name.lower()
        or wanted in u.email.lower()
    ]
    return JSONResponse(found)


def api_routes(mode: Mode) -> list[BaseRoute]:
    prefix = API_PREFIXES[mode]
    issue = f"{prefix}/issue/{{key}}"
    routes = [
        Route(f"{prefix}/myself", _myself),
        Route(f"{prefix}/issue", _create_issue, methods=["POST"]),
        Route(issue, _get_issue, methods=["GET"]),
        Route(issue, _edit_issue, methods=["PUT"]),
        Route(f"{issue}/comment", _add_comment, methods=["POST"]),
        Route(f"{issue}/assignee", _assign, methods=["PUT"]),
        Route(f"{issue}/transitions", _transitions, methods=["GET"]),
        Route(f"{issue}/transitions", _do_transition, methods=["POST"]),
    ]
    if mode is Mode.CLOUD:
        routes += [
            Route(f"{prefix}/search/jql", _search_jql),
            Route(f"{prefix}/user/search", _user_search),
        ]
    else:
        routes.append(Route(f"{prefix}/search", _search))
    return routes
