"""fake-jira's state: users, projects with their workflows, and issues.

A seed has these keys besides ``tokens`` (docs/jira.md has every field):

- ``users``: ``name`` (the Server username, and the identity a token maps
  to), with optional ``account_id`` (Cloud), ``display_name`` and ``email``.
- ``projects``: ``key`` and ``name``, optional ``statuses``, ``transitions``
  and ``issue_types``. The default workflow is To Do, In Progress, Done, with
  a global transition into each.
- ``issues``: keyed ``PROJ-12``, with summary, type, status, people,
  priority, labels, timestamps and comments.

Bodies (descriptions and comments) are held the way the mode speaks them:
ADF documents in Cloud, plain strings in Server. A seed may write either; it
is converted once, here.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fake_backends.server.jira.adf import adf_to_text, is_adf, text_to_adf

__all__ = [
    "DEFAULT_ISSUE_TYPES",
    "DEFAULT_PRIORITY",
    "DEFAULT_TIMESTAMP",
    "Category",
    "Comment",
    "Issue",
    "JiraState",
    "Mode",
    "Project",
    "Status",
    "Transition",
    "User",
    "parse_state",
]

DEFAULT_TIMESTAMP = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
DEFAULT_ISSUE_TYPES = ("Task", "Bug", "Story", "Epic")
DEFAULT_PRIORITY = "Medium"
PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")
_FIRST_ID = 10000
# Cloud account IDs are opaque; these are a stable digest of the username.
_ACCOUNT_ID_PREFIX = "557058:"
_ACCOUNT_ID_DIGITS = 32


class Mode(StrEnum):
    CLOUD = "cloud"
    SERVER = "server"


class Category(StrEnum):
    """JIRA's three status categories, by their keys."""

    NEW = "new"
    IN_PROGRESS = "indeterminate"
    DONE = "done"


CATEGORY_NAMES = {
    Category.NEW: "To Do",
    Category.IN_PROGRESS: "In Progress",
    Category.DONE: "Done",
}
# JIRA's own ids for the categories.
CATEGORY_IDS = {Category.NEW: 2, Category.IN_PROGRESS: 4, Category.DONE: 3}
CATEGORY_COLOURS = {
    Category.NEW: "blue-gray",
    Category.IN_PROGRESS: "yellow",
    Category.DONE: "green",
}


@dataclass
class User:
    name: str
    account_id: str
    display_name: str
    email: str
    active: bool = True


@dataclass
class Status:
    id: str
    name: str
    category: Category


@dataclass
class Transition:
    id: str
    name: str
    to: str
    # The statuses it leaves from; None for a global transition.
    sources: frozenset[str] | None


@dataclass
class Project:
    id: str
    key: str
    name: str
    statuses: dict[str, Status]
    transitions: list[Transition]
    issue_types: tuple[str, ...]

    def status(self, name: str) -> Status | None:
        return self.statuses.get(name.lower())

    @property
    def initial_status(self) -> Status:
        return next(iter(self.statuses.values()))

    def available(self, current: Status) -> list[Transition]:
        return [
            t
            for t in self.transitions
            if t.to.lower() != current.name.lower()
            and (t.sources is None or current.name.lower() in t.sources)
        ]


@dataclass
class Comment:
    id: str
    author: str
    body: Any  # ADF in Cloud, a string in Server
    created: dt.datetime
    updated: dt.datetime


@dataclass
class Issue:
    id: str
    key: str
    project: str
    number: int
    summary: str
    description: Any  # ADF in Cloud, a string in Server, or None
    type: str
    status: Status
    assignee: str | None
    reporter: str
    priority: str
    labels: list[str]
    created: dt.datetime
    updated: dt.datetime
    comments: list[Comment]

    def text(self) -> str:
        """Everything ``text ~`` searches: summary, description and comments."""
        parts = [self.summary, adf_to_text(self.description)]
        parts += [adf_to_text(c.body) for c in self.comments]
        return "\n".join(parts)


@dataclass
class JiraState:
    mode: Mode
    users: dict[str, User] = field(default_factory=dict)
    projects: dict[str, Project] = field(default_factory=dict)
    issues: dict[str, Issue] = field(default_factory=dict)
    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(_FIRST_ID))

    def new_id(self) -> str:
        return str(next(self._ids))

    def user(self, name: str) -> User | None:
        return self.users.get(name.lower())

    def user_by_account(self, account_id: str) -> User | None:
        return next(
            (u for u in self.users.values() if u.account_id == account_id), None
        )

    def ensure_user(self, name: str) -> User:
        found = self.user(name)
        if found is None:
            found = _make_user(name)
            self.users[name.lower()] = found
        return found

    def body(self, value: Any, where: str) -> Any:
        """A seed's body, held the way this mode speaks it."""
        if value is None:
            return None
        if isinstance(value, str):
            return text_to_adf(value) if self.mode is Mode.CLOUD else value
        if is_adf(value):
            return value if self.mode is Mode.CLOUD else adf_to_text(value)
        raise ValueError(f"{where}: expected text or an ADF document")

    def project_issues(self, key: str) -> list[Issue]:
        return [i for i in self.issues.values() if i.project == key]


def _make_user(name: str, **overrides: Any) -> User:
    digest = hashlib.sha256(name.lower().encode()).hexdigest()[:_ACCOUNT_ID_DIGITS]
    return User(
        name=name,
        account_id=str(overrides.get("account_id") or f"{_ACCOUNT_ID_PREFIX}{digest}"),
        display_name=str(overrides.get("display_name") or name.title()),
        email=str(overrides.get("email") or f"{name}@example.com"),
        active=bool(overrides.get("active", True)),
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


_DEFAULT_STATUSES = (
    ("To Do", Category.NEW),
    ("In Progress", Category.IN_PROGRESS),
    ("Done", Category.DONE),
)
# JIRA's default global transition ids step by ten.
_TRANSITION_ID_STEP = 10
_FIRST_TRANSITION_ID = 11


def _parse_project(state: JiraState, raw: Any, where: str) -> None:
    item = _mapping(raw, where)
    key = str(_required(item, "key", where)).upper()
    if key in state.projects:
        raise ValueError(f"{where}: project {key} is declared twice")
    statuses: dict[str, Status] = {}
    raw_statuses = item.get("statuses") or [
        {"name": name, "category": category.value}
        for name, category in _DEFAULT_STATUSES
    ]
    for i, entry in enumerate(_list(raw_statuses, f"{where}.statuses")):
        at = f"{where}.statuses[{i}]"
        entry = _mapping(entry, at)
        name = str(_required(entry, "name", at))
        category = str(entry.get("category", Category.IN_PROGRESS))
        if category not in {c.value for c in Category}:
            raise ValueError(
                f"{at}.category: {category!r} must be one of new, indeterminate, done"
            )
        statuses[name.lower()] = Status(state.new_id(), name, Category(category))
    if not statuses:
        raise ValueError(f"{where}.statuses: a project needs at least one status")

    transitions = []
    raw_transitions = item.get("transitions")
    if raw_transitions is None:
        raw_transitions = [{"name": s.name, "to": s.name} for s in statuses.values()]
    for i, entry in enumerate(_list(raw_transitions, f"{where}.transitions")):
        at = f"{where}.transitions[{i}]"
        entry = _mapping(entry, at)
        target = str(_required(entry, "to", at))
        if target.lower() not in statuses:
            raise ValueError(
                f"{at}.to: {target!r} is not one of the project's statuses"
            )
        sources = entry.get("from")
        if sources is not None:
            sources = frozenset(str(s).lower() for s in _list(sources, f"{at}.from"))
            unknown = sorted(sources - statuses.keys())
            if unknown:
                raise ValueError(f"{at}.from: {unknown} are not the project's statuses")
        transitions.append(
            Transition(
                id=str(entry.get("id", _FIRST_TRANSITION_ID + i * _TRANSITION_ID_STEP)),
                name=str(entry.get("name", target)),
                to=statuses[target.lower()].name,
                sources=sources,
            )
        )
    state.projects[key] = Project(
        id=state.new_id(),
        key=key,
        name=str(item.get("name", key)),
        statuses=statuses,
        transitions=transitions,
        issue_types=tuple(
            str(t) for t in item.get("issue_types") or DEFAULT_ISSUE_TYPES
        ),
    )


def _parse_issue(state: JiraState, raw: Any, where: str) -> None:
    item = _mapping(raw, where)
    key = str(_required(item, "key", where)).upper()
    project_key, _, number_text = key.rpartition("-")
    if not project_key or not number_text.isdigit():
        raise ValueError(f"{where}.key: {key!r} is not a key like PROJ-12")
    project = state.projects.get(project_key)
    if project is None:
        raise ValueError(
            f"{where}.key: project {project_key} is not declared in projects"
        )
    if key in state.issues:
        raise ValueError(f"{where}: {key} is declared twice")
    status_name = str(item.get("status", project.initial_status.name))
    status = project.status(status_name)
    if status is None:
        raise ValueError(
            f"{where}.status: {status_name!r} is not one of {project.key}'s statuses"
        )
    issue_type = str(item.get("type", project.issue_types[0]))
    if issue_type not in project.issue_types:
        raise ValueError(
            f"{where}.type: {issue_type!r} is not one of {list(project.issue_types)}"
        )
    priority = str(item.get("priority", DEFAULT_PRIORITY))
    if priority not in PRIORITIES:
        raise ValueError(
            f"{where}.priority: {priority!r} must be one of {list(PRIORITIES)}"
        )
    created = _timestamp(item.get("created", DEFAULT_TIMESTAMP), f"{where}.created")
    reporter = state.ensure_user(str(item.get("reporter", "admin"))).name
    assignee = item.get("assignee")
    if assignee is not None:
        assignee = state.ensure_user(str(assignee)).name
    comments = []
    for i, entry in enumerate(_list(item.get("comments"), f"{where}.comments")):
        at = f"{where}.comments[{i}]"
        entry = _mapping(entry, at)
        when = _timestamp(entry.get("created", created), f"{at}.created")
        comments.append(
            Comment(
                id=state.new_id(),
                author=state.ensure_user(str(entry.get("author", reporter))).name,
                body=state.body(entry.get("body", ""), f"{at}.body"),
                created=when,
                updated=when,
            )
        )
    state.issues[key] = Issue(
        id=state.new_id(),
        key=key,
        project=project.key,
        number=int(number_text),
        summary=str(item.get("summary", key)),
        description=state.body(item.get("description"), f"{where}.description"),
        type=issue_type,
        status=status,
        assignee=assignee,
        reporter=reporter,
        priority=priority,
        labels=[str(label) for label in _list(item.get("labels"), f"{where}.labels")],
        created=created,
        updated=_timestamp(item.get("updated", created), f"{where}.updated"),
        comments=comments,
    )


_KNOWN_KEYS = {"users", "projects", "issues"}


def parse_state(data: Mapping[str, Any], mode: Mode) -> JiraState:
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(
            f"unknown seed keys {unknown}; fake-jira reads tokens and "
            f"{sorted(_KNOWN_KEYS)}"
        )
    state = JiraState(mode=mode)
    for i, raw in enumerate(_list(data.get("users"), "users")):
        where = f"users[{i}]"
        item = _mapping(raw, where)
        name = str(_required(item, "name", where))
        overrides = {k: v for k, v in item.items() if k != "name"}
        state.users[name.lower()] = _make_user(name, **overrides)
    for i, raw in enumerate(_list(data.get("projects"), "projects")):
        _parse_project(state, raw, f"projects[{i}]")
    for i, raw in enumerate(_list(data.get("issues"), "issues")):
        _parse_issue(state, raw, f"issues[{i}]")
    return state
