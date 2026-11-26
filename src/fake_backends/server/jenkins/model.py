"""fake-jenkins' state: jobs and folders, builds, the queue, nodes, users,
lockable resources and the security settings. docs/jenkins.md has the seed
format.

Builds move on when they are looked at, not when time passes, so every run of
a test sees the same sequence:

- A triggered build waits in the queue for its job's ``queue_polls`` reads
  of the queue item, then starts.
- A running build emits one chunk of its job's ``run_log`` each time its
  progressive log or its JSON is read, and finishes with ``run_result`` once
  the last chunk has gone out.
"""

from __future__ import annotations

import datetime as dt
import itertools
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "BUILT_IN_NODE",
    "PERMALINKS",
    "Build",
    "Change",
    "Job",
    "JobType",
    "JenkinsState",
    "Node",
    "Parameter",
    "PluginState",
    "QueueItem",
    "Resource",
    "Result",
    "Security",
    "User",
    "parse_state",
]

DEFAULT_TIMESTAMP = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
BUILT_IN_NODE = "(built-in)"
_MS_PER_SECOND = 1000
_DEFAULT_DURATION_MS = 1000
_DEFAULT_EXECUTORS = 2
_FIRST_QUEUE_ID = 1


class JobType(StrEnum):
    FREESTYLE = "freestyle"
    PIPELINE = "pipeline"
    FOLDER = "folder"
    MATRIX = "matrix"


class Result(StrEnum):
    SUCCESS = "SUCCESS"
    UNSTABLE = "UNSTABLE"
    FAILURE = "FAILURE"
    ABORTED = "ABORTED"
    NOT_BUILT = "NOT_BUILT"


class PluginState(StrEnum):
    """Which Lockable Resources plugin is installed, if any."""

    CURRENT = "current"
    # createResource and deleteResource are missing from older releases.
    OLD = "old"
    ABSENT = "absent"


# Jenkins' build permalinks, and the builds each one names.
PERMALINKS = {
    "lastBuild": lambda b: True,
    "lastCompletedBuild": lambda b: not b.building,
    "lastSuccessfulBuild": lambda b: b.result in {Result.SUCCESS, Result.UNSTABLE},
    "lastStableBuild": lambda b: b.result == Result.SUCCESS,
    "lastFailedBuild": lambda b: b.result == Result.FAILURE,
    "lastUnstableBuild": lambda b: b.result == Result.UNSTABLE,
    "lastUnsuccessfulBuild": lambda b: (
        not b.building and b.result not in {Result.SUCCESS, None}
    ),
}


@dataclass
class User:
    id: str
    full_name: str
    email: str
    # Administrators may release anyone's reservation. Nobody is one unless
    # the seed says so.
    admin: bool = False


@dataclass
class Parameter:
    name: str
    kind: str  # string, text, boolean, choice, password
    default: Any
    description: str
    choices: list[str]


@dataclass
class Change:
    commit_id: str
    msg: str
    timestamp: dt.datetime
    author: str
    author_email: str


@dataclass
class Build:
    number: int
    result: Result | None
    building: bool
    timestamp: dt.datetime
    duration_ms: int
    parameters: dict[str, Any]
    causes: list[dict[str, Any]]
    culprits: list[str]
    changes: list[Change]
    log: str
    queue_id: int
    # Chunks still to emit while running.
    pending_log: list[str] = field(default_factory=list)
    pending_result: Result = Result.SUCCESS
    # Inside the lock step, waiting for a resource someone else holds.
    waiting_for_locks: bool = False


@dataclass
class Job:
    path: tuple[str, ...]
    type: JobType
    description: str
    disabled: bool
    parameters: list[Parameter]
    builds: dict[int, Build]
    children: dict[str, Job]
    queue_polls: int
    run_log: list[str]
    run_result: Result
    locks: list[str]

    @property
    def name(self) -> str:
        return self.path[-1]

    @property
    def full_name(self) -> str:
        return "/".join(self.path)

    @property
    def url_path(self) -> str:
        return "".join(f"job/{part}/" for part in self.path)

    @property
    def next_number(self) -> int:
        return max(self.builds, default=0) + 1

    def newest_first(self) -> list[Build]:
        return [self.builds[n] for n in sorted(self.builds, reverse=True)]


@dataclass
class QueueItem:
    id: int
    job: tuple[str, ...]
    parameters: dict[str, Any]
    causes: list[dict[str, Any]]
    since: dt.datetime
    polls_left: int
    why: str = "Waiting for next available executor"
    build: int | None = None
    cancelled: bool = False

    @property
    def left(self) -> bool:
        return self.build is not None or self.cancelled


@dataclass
class Node:
    name: str
    description: str
    executors: int
    labels: list[str]
    offline: bool
    offline_reason: str
    host: str | None
    monitor: dict[str, Any]


@dataclass
class Resource:
    name: str
    description: str
    labels: list[str]
    note: str = ""
    reserved_by: str | None = None
    reserved_by_email: str | None = None
    reserved_at: dt.datetime | None = None
    locked_by: str | None = None  # "job #n" of the build holding the lock
    ephemeral: bool = False
    stolen: bool = False

    @property
    def free(self) -> bool:
        return self.reserved_by is None and self.locked_by is None


@dataclass
class Security:
    # Whether POSTs need a crumb, and whether API-token logins are exempt, as
    # they are by default since Jenkins 2.96.
    crumbs: bool = True
    api_tokens_exempt: bool = True
    # How an anonymous caller is turned away: a redirect to the login page
    # (SSO and reverse-proxy setups), or 403.
    login_redirect: bool = False


@dataclass
class JenkinsState:
    description: str = "Jenkins"
    executors: int = _DEFAULT_EXECUTORS
    users: dict[str, User] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    queue: dict[int, QueueItem] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    resources: dict[str, Resource] = field(default_factory=dict)
    plugin: PluginState = PluginState.CURRENT
    security: Security = field(default_factory=Security)
    _queue_ids: Iterator[int] = field(
        default_factory=lambda: itertools.count(_FIRST_QUEUE_ID)
    )

    def new_queue_id(self) -> int:
        return next(self._queue_ids)

    def user(self, user_id: str) -> User:
        found = self.users.get(user_id.lower())
        if found is None:
            found = User(user_id, user_id, f"{user_id}@example.com")
            self.users[user_id.lower()] = found
        return found

    def all_jobs(self) -> Iterator[Job]:
        """Every job, folders' contents included."""
        pending = list(self.jobs.values())
        while pending:
            job = pending.pop(0)
            yield job
            pending.extend(job.children.values())

    def running(self) -> int:
        return sum(1 for j in self.all_jobs() for b in j.builds.values() if b.building)

    def job(self, path: tuple[str, ...]) -> Job | None:
        level, found = self.jobs, None
        for part in path:
            found = level.get(part)
            if found is None:
                return None
            level = found.children
        return found

    # --- the lifecycle ---------------------------------------------------

    def enqueue(
        self, job: Job, parameters: dict[str, Any], identity: str, now: dt.datetime
    ) -> QueueItem:
        cause = {
            "_class": "hudson.model.Cause$UserIdCause",
            "shortDescription": f"Started by user {self.user(identity).full_name}",
            "userId": identity,
            "userName": self.user(identity).full_name,
        }
        item = QueueItem(
            id=self.new_queue_id(),
            job=job.path,
            parameters=parameters,
            causes=[cause],
            since=now,
            polls_left=job.queue_polls,
        )
        self.queue[item.id] = item
        if item.polls_left == 0:
            self.start(item, now)
        return item

    def poll(self, item: QueueItem, now: dt.datetime) -> None:
        """A read of a waiting item, which may let it start."""
        if item.left:
            return
        item.polls_left -= 1
        if item.polls_left <= 0:
            self.start(item, now)

    def start(self, item: QueueItem, now: dt.datetime) -> None:
        job = self.job(item.job)
        number = job.next_number
        build = Build(
            number=number,
            result=None,
            building=True,
            timestamp=now,
            duration_ms=0,
            parameters=item.parameters,
            causes=item.causes,
            culprits=[],
            changes=[],
            log="",
            queue_id=item.id,
            pending_log=[_expand(chunk, item.parameters) for chunk in job.run_log],
            pending_result=job.run_result,
        )
        job.builds[number] = build
        item.build = number
        if not self._acquire(job, build):
            # As the lock step does: the build starts, then waits inside it.
            build.waiting_for_locks = True
            build.log += f"Trying to acquire lock on [{', '.join(job.locks)}]\n"
            return
        if not build.pending_log:
            self.finish(job, build, build.pending_result, now)

    def _acquire(self, job: Job, build: Build) -> bool:
        """Lock every resource the job needs, or none if any is taken."""
        holder = f"{job.full_name} #{build.number}"
        wanted = [self.resources[n] for n in job.locks if n in self.resources]
        if any(not r.free and r.locked_by != holder for r in wanted):
            return False
        for resource in wanted:
            resource.locked_by = holder
        return True

    def advance(self, job: Job, build: Build, now: dt.datetime) -> None:
        """Emit the next chunk of a running build's log, finishing after the last."""
        if not build.building:
            return
        if build.waiting_for_locks:
            if not self._acquire(job, build):
                return
            build.waiting_for_locks = False
            build.log += f"Lock acquired on [{', '.join(job.locks)}]\n"
            return
        if build.pending_log:
            build.log += build.pending_log.pop(0)
        if not build.pending_log:
            self.finish(job, build, build.pending_result, now)

    def finish(self, job: Job, build: Build, result: Result, now: dt.datetime) -> None:
        build.building = False
        build.result = result
        build.duration_ms = max(
            int((now - build.timestamp).total_seconds() * _MS_PER_SECOND), 1
        )
        holder = f"{job.full_name} #{build.number}"
        for resource in self.resources.values():
            if resource.locked_by == holder:
                resource.locked_by = None


_PARAMETER_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(chunk: str, parameters: Mapping[str, Any]) -> str:
    """``${NAME}`` in a run log becomes the build's parameter, as a shell would."""
    return _PARAMETER_REFERENCE.sub(
        lambda m: str(parameters.get(m.group(1), m.group(0))), chunk
    )


# --- parsing -------------------------------------------------------------


def _timestamp(value: Any, where: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        parsed = dt.datetime.combine(value, dt.time())
    elif isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{where}: {value!r} is not an ISO 8601 time") from None
    else:
        raise ValueError(f"{where}: expected an ISO 8601 time, got {value!r}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _list(value: Any, where: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{where}: expected a list")
    return value


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where}: expected a mapping")
    return value


def _required(item: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in item:
        raise ValueError(f"{where}: '{key}' is required")
    return item[key]


def _enum(kind: type[StrEnum], value: Any, where: str) -> Any:
    try:
        return kind(str(value))
    except ValueError:
        raise ValueError(
            f"{where}: {value!r} must be one of {[k.value for k in kind]}"
        ) from None


_PARAMETER_KINDS = {"string", "text", "boolean", "choice", "password"}


def _parse_parameter(raw: Any, where: str) -> Parameter:
    item = _mapping(raw, where)
    kind = str(item.get("type", "string"))
    if kind not in _PARAMETER_KINDS:
        raise ValueError(
            f"{where}.type: {kind!r} must be one of {sorted(_PARAMETER_KINDS)}"
        )
    choices = [str(c) for c in _list(item.get("choices"), f"{where}.choices")]
    if kind == "choice" and not choices:
        raise ValueError(f"{where}.choices: a choice parameter needs choices")
    default = item.get(
        "default", choices[0] if choices else False if kind == "boolean" else ""
    )
    return Parameter(
        name=str(_required(item, "name", where)),
        kind=kind,
        default=default,
        description=str(item.get("description", "")),
        choices=choices,
    )


def _parse_build(state: JenkinsState, job: Job, raw: Any, where: str) -> Build:
    item = _mapping(raw, where)
    number = int(_required(item, "number", where))
    building = bool(item.get("building", False))
    result = (
        None
        if building
        else _enum(Result, item.get("result", Result.SUCCESS), f"{where}.result")
    )
    defaults = {p.name: p.default for p in job.parameters}
    parameters = {
        **defaults,
        **_mapping(item.get("parameters", {}), f"{where}.parameters"),
    }
    started_by = item.get("started_by")
    starter = state.user(str(started_by)).full_name if started_by else ""
    causes = [
        (
            {
                "_class": "hudson.model.Cause$UserIdCause",
                "shortDescription": f"Started by user {starter}",
                "userId": str(started_by),
                "userName": state.user(str(started_by)).full_name,
            }
            if started_by
            else {
                "_class": "hudson.triggers.SCMTrigger$SCMTriggerCause",
                "shortDescription": "Started by an SCM change",
            }
        )
    ]
    changes = []
    for i, entry in enumerate(_list(item.get("changes"), f"{where}.changes")):
        at = f"{where}.changes[{i}]"
        entry = _mapping(entry, at)
        author = str(entry.get("author", "someone"))
        state.user(author)
        changes.append(
            Change(
                commit_id=str(entry.get("commit", f"{number:07x}{i:033x}")),
                msg=str(entry.get("msg", "")),
                timestamp=_timestamp(
                    entry.get("timestamp", DEFAULT_TIMESTAMP), f"{at}.timestamp"
                ),
                author=author,
                author_email=str(entry.get("email", state.user(author).email)),
            )
        )
    culprits = [str(c) for c in _list(item.get("culprits"), f"{where}.culprits")]
    culprits = culprits or sorted({c.author for c in changes})
    log = item.get(
        "log", f"Started\nFinished: {result.value if result else 'RUNNING'}\n"
    )
    return Build(
        number=number,
        result=result,
        building=building,
        timestamp=_timestamp(
            item.get("timestamp", DEFAULT_TIMESTAMP), f"{where}.timestamp"
        ),
        duration_ms=int(
            item.get("duration_ms", 0 if building else _DEFAULT_DURATION_MS)
        ),
        parameters=parameters,
        causes=causes,
        culprits=culprits,
        changes=changes,
        log=str(log),
        queue_id=state.new_queue_id(),
        pending_log=list(job.run_log) if building else [],
        pending_result=job.run_result,
    )


def _parse_job(
    state: JenkinsState, raw: Any, parent: tuple[str, ...], where: str
) -> Job:
    item = _mapping(raw, where)
    name = str(_required(item, "name", where))
    if "/" in name:
        raise ValueError(f"{where}.name: nest jobs with 'jobs', not a '/' in the name")
    kind = _enum(JobType, item.get("type", JobType.FREESTYLE), f"{where}.type")
    run_log = item.get("run_log", ["Started\n", "Finished: SUCCESS\n"])
    if isinstance(run_log, str):
        run_log = [run_log]
    job = Job(
        path=(*parent, name),
        type=kind,
        description=str(item.get("description", "")),
        disabled=bool(item.get("disabled", False)),
        parameters=[
            _parse_parameter(p, f"{where}.parameters[{i}]")
            for i, p in enumerate(_list(item.get("parameters"), f"{where}.parameters"))
        ],
        builds={},
        children={},
        queue_polls=int(item.get("queue_polls", 0)),
        run_log=[str(chunk) for chunk in _list(run_log, f"{where}.run_log")],
        run_result=_enum(
            Result, item.get("run_result", Result.SUCCESS), f"{where}.run_result"
        ),
        locks=[str(r) for r in _list(item.get("locks"), f"{where}.locks")],
    )
    if kind is JobType.FOLDER:
        if item.get("builds"):
            raise ValueError(f"{where}: a folder has no builds")
        for i, child in enumerate(_list(item.get("jobs"), f"{where}.jobs")):
            parsed = _parse_job(state, child, job.path, f"{where}.jobs[{i}]")
            job.children[parsed.name] = parsed
    elif item.get("jobs"):
        raise ValueError(f"{where}: only a folder holds jobs")
    for i, build in enumerate(_list(item.get("builds"), f"{where}.builds")):
        parsed = _parse_build(state, job, build, f"{where}.builds[{i}]")
        if parsed.number in job.builds:
            raise ValueError(
                f"{where}.builds[{i}]: build {parsed.number} is declared twice"
            )
        job.builds[parsed.number] = parsed
    return job


def _parse_node(raw: Any, where: str) -> Node:
    item = _mapping(raw, where)
    return Node(
        name=str(_required(item, "name", where)),
        description=str(item.get("description", "")),
        executors=int(item.get("executors", 1)),
        labels=[str(label) for label in _list(item.get("labels"), f"{where}.labels")],
        offline=bool(item.get("offline", False)),
        offline_reason=str(item.get("offline_reason", "")),
        host=item.get("host"),
        monitor=dict(_mapping(item.get("monitor", {}), f"{where}.monitor")),
    )


def _parse_resource(state: JenkinsState, raw: Any, where: str) -> Resource:
    item = _mapping(raw, where)
    labels = item.get("labels", [])
    if isinstance(labels, str):
        labels = labels.split()
    reserved_by = item.get("reserved_by")
    return Resource(
        name=str(_required(item, "name", where)),
        description=str(item.get("description", "")),
        labels=[str(label) for label in labels],
        note=str(item.get("note", "")),
        reserved_by=str(reserved_by) if reserved_by else None,
        reserved_by_email=state.user(str(reserved_by)).email if reserved_by else None,
        reserved_at=(
            _timestamp(
                item.get("reserved_at", DEFAULT_TIMESTAMP), f"{where}.reserved_at"
            )
            if reserved_by
            else None
        ),
        locked_by=item.get("locked_by"),
    )


_KNOWN_KEYS = {"jenkins", "users", "jobs", "nodes", "lockable_resources", "security"}


def parse_state(data: Mapping[str, Any]) -> JenkinsState:
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(
            f"unknown seed keys {unknown}; fake-jenkins reads tokens and "
            f"{sorted(_KNOWN_KEYS)}"
        )
    state = JenkinsState()
    settings = _mapping(data.get("jenkins", {}), "jenkins")
    state.description = str(settings.get("description", state.description))
    state.executors = int(settings.get("executors", state.executors))
    security = _mapping(data.get("security", {}), "security")
    state.security = Security(
        crumbs=bool(security.get("crumbs", True)),
        api_tokens_exempt=bool(security.get("api_tokens_exempt", True)),
        login_redirect=bool(security.get("login_redirect", False)),
    )
    for i, raw in enumerate(_list(data.get("users"), "users")):
        where = f"users[{i}]"
        item = _mapping(raw, where)
        user_id = str(_required(item, "id", where))
        state.users[user_id.lower()] = User(
            id=user_id,
            full_name=str(item.get("full_name", user_id)),
            email=str(item.get("email", f"{user_id}@example.com")),
            admin=bool(item.get("admin", False)),
        )
    for i, raw in enumerate(_list(data.get("jobs"), "jobs")):
        job = _parse_job(state, raw, (), f"jobs[{i}]")
        if job.name in state.jobs:
            raise ValueError(f"jobs[{i}]: {job.name} is declared twice")
        state.jobs[job.name] = job
    state.nodes[BUILT_IN_NODE] = Node(
        name=BUILT_IN_NODE,
        description="the Jenkins controller's built-in node",
        executors=state.executors,
        labels=["built-in"],
        offline=False,
        offline_reason="",
        host=None,
        monitor={},
    )
    for i, raw in enumerate(_list(data.get("nodes"), "nodes")):
        node = _parse_node(raw, f"nodes[{i}]")
        state.nodes[node.name] = node
    lockable = _mapping(data.get("lockable_resources", {}), "lockable_resources")
    state.plugin = _enum(
        PluginState,
        lockable.get("plugin", PluginState.CURRENT),
        "lockable_resources.plugin",
    )
    for i, raw in enumerate(
        _list(lockable.get("resources"), "lockable_resources.resources")
    ):
        resource = _parse_resource(state, raw, f"lockable_resources.resources[{i}]")
        state.resources[resource.name] = resource
    return state
