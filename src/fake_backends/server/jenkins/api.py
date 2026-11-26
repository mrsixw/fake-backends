"""fake-jenkins' HTTP API: the paths jeeves calls, plus the pages they redirect to."""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import BaseRoute, Route

from fake_backends.server.jenkins import tree, views
from fake_backends.server.jenkins.model import (
    BUILT_IN_NODE,
    PERMALINKS,
    Build,
    JenkinsState,
    Job,
    JobType,
    Result,
)

__all__ = [
    "CRUMB_FIELD",
    "LOGIN_PATH",
    "check_crumb",
    "crumb_for",
    "error_page",
    "form",
    "jenkins_routes",
    "not_found",
    "state_of",
]

CRUMB_FIELD = "Jenkins-Crumb"
LOGIN_PATH = "/securityRealm/commenceLogin"
_CRUMB_DIGITS = 64
_FORM_TYPE = "application/x-www-form-urlencoded"
# A nested job is two path segments: "job" and its name.
_NESTED_JOB_SEGMENTS = 2


def state_of(request: Request) -> JenkinsState:
    return request.app.state.store.data


def _base(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def error_page(status: HTTPStatus, text: str, **headers: str) -> Response:
    """Jenkins' HTML error page, as its Jetty serves one."""
    body = f"<html><body><h2>HTTP ERROR {status.value} {text}</h2></body></html>"
    return HTMLResponse(body, status, headers=headers or None)


def not_found() -> Response:
    return error_page(HTTPStatus.NOT_FOUND, "Not Found")


def crumb_for(identity: str) -> str:
    return hashlib.sha256(f"crumb:{identity}".encode()).hexdigest()[:_CRUMB_DIGITS]


def check_crumb(request: Request) -> Response | None:
    """403 unless the POST carries a valid crumb, or needs none."""
    security = state_of(request).security
    if not security.crumbs or security.api_tokens_exempt:
        return None
    if request.headers.get(CRUMB_FIELD) == crumb_for(request.state.identity):
        return None
    return error_page(
        HTTPStatus.FORBIDDEN, "No valid crumb was included in the request"
    )


async def form(request: Request) -> dict[str, str]:
    """A POST's form fields, merged over its query string, as Jenkins reads them."""
    fields = {
        k: v[-1] for k, v in parse_qs(request.url.query, keep_blank_values=True).items()
    }
    if request.headers.get("content-type", "").split(";")[0].strip() == _FORM_TYPE:
        body = (await request.body()).decode()
        fields.update(
            {k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()}
        )
    return fields


def _json(request: Request, render: Callable[[int], dict[str, Any]]) -> Response:
    """Render at the asked ``depth``, or deep and then cut to ``tree``."""
    spec = request.query_params.get("tree")
    try:
        if spec:
            return JSONResponse(tree.apply(render(views.TREE_DEPTH), tree.parse(spec)))
        depth = int(request.query_params.get("depth", 0))
    except tree.TreeError as exc:
        return PlainTextResponse(str(exc), HTTPStatus.BAD_REQUEST)
    except ValueError:
        return PlainTextResponse("depth must be a number", HTTPStatus.BAD_REQUEST)
    return JSONResponse(render(depth))


# --- the root, the user, crumbs, queue, nodes ------------------------------


async def _root(request: Request) -> Response:
    state = state_of(request)
    return _json(request, lambda depth: views.root(state, _base(request), depth))


async def _me(request: Request) -> Response:
    state = state_of(request)
    return _json(
        request, lambda depth: views.me(state, _base(request), request.state.identity)
    )


async def _crumb(request: Request) -> Response:
    if not state_of(request).security.crumbs:
        return not_found()
    return JSONResponse(
        {
            "_class": "hudson.security.csrf.DefaultCrumbIssuer",
            "crumb": crumb_for(request.state.identity),
            "crumbRequestField": CRUMB_FIELD,
        }
    )


async def _queue(request: Request) -> Response:
    state = state_of(request)
    return _json(request, lambda depth: views.queue(state, _base(request)))


async def _queue_item(request: Request) -> Response:
    state = state_of(request)
    item = state.queue.get(request.path_params["item"])
    if item is None:
        return not_found()
    state.poll(item, _now())
    return _json(request, lambda depth: views.queue_item(state, _base(request), item))


async def _computers(request: Request) -> Response:
    state = state_of(request)
    return _json(request, lambda depth: views.computers(state, depth))


async def _node_config(request: Request) -> Response:
    node = state_of(request).nodes.get(request.path_params["name"])
    if node is None or node.name == BUILT_IN_NODE:
        return not_found()
    launcher = (
        '  <launcher class="hudson.slaves.JNLPLauncher"/>\n'
        if node.host is None
        else (
            '  <launcher class="hudson.plugins.sshslaves.SSHLauncher" '
            'plugin="ssh-slaves">\n'
            f"    <host>{node.host}</host>\n"
            "    <port>22</port>\n"
            "  </launcher>\n"
        )
    )
    xml = (
        "<?xml version='1.1' encoding='UTF-8'?>\n<slave>\n"
        f"  <name>{node.name}</name>\n  <description>{node.description}</description>\n"
        "  <remoteFS>/home/jenkins</remoteFS>\n"
        f"  <numExecutors>{node.executors}</numExecutors>\n"
        f"{launcher}  <label>{' '.join(node.labels)}</label>\n</slave>\n"
    )
    return Response(xml, media_type="application/xml")


async def _login(request: Request) -> Response:
    return HTMLResponse("<html><body><h1>Sign in to Jenkins</h1></body></html>")


async def _page(request: Request) -> Response:
    """The HTML pages Jenkins' POSTs redirect back to."""
    return HTMLResponse("<html><body>Jenkins</body></html>")


# --- jobs and builds ------------------------------------------------------


def _walk(state: JenkinsState, rest: str) -> tuple[Job | None, list[str]]:
    """``a/job/b/42/api/json`` -> (job a/b, ["42", "api", "json"])."""
    parts = [p for p in rest.split("/") if p]
    if not parts:
        return None, []
    path = [parts.pop(0)]
    while len(parts) >= _NESTED_JOB_SEGMENTS and parts[0] == "job":
        parts.pop(0)
        path.append(parts.pop(0))
    return state.job(tuple(path)), parts


def _find_build(item: Job, ref: str) -> Build | None:
    if ref.isdigit():
        return item.builds.get(int(ref))
    test = PERMALINKS.get(ref)
    if test is None:
        return None
    return next((b for b in item.newest_first() if test(b)), None)


def _is_build_ref(ref: str) -> bool:
    return ref.isdigit() or ref in PERMALINKS


async def _job_get(request: Request) -> Response:
    state = state_of(request)
    item, rest = _walk(state, request.path_params["rest"])
    if item is None:
        return not_found()
    base = _base(request)
    if rest in ([], [""]):
        return await _page(request)
    if rest == ["api", "json"]:
        return _json(request, lambda depth: views.job(state, base, item, depth))
    if item.type is JobType.FOLDER or not _is_build_ref(rest[0]):
        return not_found()
    found = _find_build(item, rest[0])
    if found is None:
        return not_found()
    action = rest[1:]
    if action in ([], [""]):
        return await _page(request)
    if action == ["api", "json"]:
        state.advance(item, found, _now())
        return _json(
            request, lambda depth: views.build(state, base, item, found, depth)
        )
    if action == ["consoleText"]:
        return PlainTextResponse(found.log)
    if action == ["logText", "progressiveText"]:
        return _progressive(request, state, item, found)
    return not_found()


def _progressive(
    request: Request, state: JenkinsState, item: Job, found: Build
) -> Response:
    """The log from ``start`` on; ``X-More-Data`` while the build still runs."""
    try:
        start = max(int(request.query_params.get("start", 0)), 0)
    except ValueError:
        start = 0
    state.advance(item, found, _now())
    data = found.log.encode()
    headers = {"X-Text-Size": str(len(data))}
    if found.building:
        headers["X-More-Data"] = "true"
    return Response(
        data[start:], media_type="text/plain;charset=UTF-8", headers=headers
    )


async def _job_post(request: Request) -> Response:
    state = state_of(request)
    item, rest = _walk(state, request.path_params["rest"])
    if item is None:
        return not_found()
    refused = check_crumb(request)
    if refused is not None:
        return refused
    base = _base(request)
    match rest:
        case ["build" | "buildWithParameters" as kind] if (
            item.type is not JobType.FOLDER
        ):
            return await _trigger(request, state, item, kind, base)
        case [ref, "stop"] if _is_build_ref(ref):
            return _stop(request, state, item, ref, base)
    return not_found()


def _stop(
    request: Request, state: JenkinsState, item: Job, ref: str, base: str
) -> Response:
    found = _find_build(item, ref)
    if found is None:
        return not_found()
    if found.building:
        who = state.user(request.state.identity).full_name
        found.log += f"Aborted by {who}\nFinished: ABORTED\n"
        found.pending_log.clear()
        state.finish(item, found, Result.ABORTED, _now())
    return RedirectResponse(f"{base}/{item.url_path}{found.number}/", HTTPStatus.FOUND)


async def _trigger(
    request: Request, state: JenkinsState, item: Job, kind: str, base: str
) -> Response:
    if item.disabled:
        return PlainTextResponse(f"{item.full_name} is disabled", HTTPStatus.CONFLICT)
    submitted = await form(request)
    if kind == "buildWithParameters" and not item.parameters:
        return PlainTextResponse(
            f"{item.full_name} is not parameterized", HTTPStatus.BAD_REQUEST
        )
    if kind == "build" and item.parameters and not submitted:
        # Jenkins answers a bare /build on a parameterized job with the
        # parameters form; from the API that is a 405-like refusal.
        return PlainTextResponse(
            "This page expects a form submission", HTTPStatus.BAD_REQUEST
        )
    parameters = {p.name: submitted.get(p.name, p.default) for p in item.parameters}
    queued = state.enqueue(item, parameters, request.state.identity, _now())
    return Response(
        status_code=HTTPStatus.CREATED,
        headers={"Location": f"{base}/queue/item/{queued.id}/"},
    )


def jenkins_routes() -> list[BaseRoute]:
    return [
        Route("/", _page),
        Route("/api/json", _root),
        Route("/me/api/json", _me),
        Route("/crumbIssuer/api/json", _crumb),
        Route("/queue/api/json", _queue),
        Route("/queue/item/{item:int}/api/json", _queue_item),
        Route("/computer/api/json", _computers),
        Route("/computer/{name}/config.xml", _node_config),
        Route(LOGIN_PATH, _login),
        Route("/job/{rest:path}", _job_get, methods=["GET"]),
        Route("/job/{rest:path}", _job_post, methods=["POST"]),
    ]
