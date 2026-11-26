"""JIRA response bodies, in the shape each mode's published OpenAPI spec gives.

Cloud users carry an ``accountId``; Server users a ``name`` and ``key``.
Bodies are ADF in Cloud and strings in Server, held that way in the model.
Timestamps use JIRA's own format, ``2026-03-01T10:00:00.000+0000``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Any

from fake_backends.server.jira.model import (
    CATEGORY_COLOURS,
    CATEGORY_IDS,
    CATEGORY_NAMES,
    PRIORITIES,
    Comment,
    Issue,
    JiraState,
    Mode,
    Project,
    Status,
    Transition,
    User,
)

__all__ = [
    "ALL_FIELDS",
    "comment",
    "comments_page",
    "error",
    "issue",
    "timestamp",
    "transitions",
    "user",
]

# Every field the fake serves, for "*all" and "*navigable".
ALL_FIELDS = (
    "summary",
    "status",
    "issuetype",
    "assignee",
    "reporter",
    "priority",
    "labels",
    "created",
    "updated",
    "description",
    "project",
    "comment",
)
_AVATAR_SIZES = ("16x16", "24x24", "32x32", "48x48")
# What JIRA says an issue can be expanded with.
_ISSUE_EXPAND = (
    "renderedFields,names,schema,operations,editmeta,changelog,"
    "versionedRepresentations"
)
_TIME_ZONE = "UTC"
_LOCALE = "en_US"


def timestamp(value: dt.datetime) -> str:
    utc = value.astimezone(dt.UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}+0000"


def error(
    messages: Iterable[str] = (), errors: dict[str, str] | None = None
) -> dict[str, Any]:
    return {"errorMessages": list(messages), "errors": errors or {}}


def _avatars(base: str, name: str) -> dict[str, str]:
    return {
        size: f"{base}/secure/useravatar?size={size}&owner={name}"
        for size in _AVATAR_SIZES
    }


def user(state: JiraState, base: str, api: str, who: User) -> dict[str, Any]:
    if state.mode is Mode.CLOUD:
        return {
            "self": f"{api}/user?accountId={who.account_id}",
            "accountId": who.account_id,
            "accountType": "atlassian",
            "emailAddress": who.email,
            "avatarUrls": _avatars(base, who.account_id),
            "displayName": who.display_name,
            "active": who.active,
            "timeZone": _TIME_ZONE,
            "locale": _LOCALE,
        }
    return {
        "self": f"{api}/user?username={who.name}",
        "key": who.name.lower(),
        "name": who.name,
        "emailAddress": who.email,
        "avatarUrls": _avatars(base, who.name),
        "displayName": who.display_name,
        "active": who.active,
        "timeZone": _TIME_ZONE,
        "locale": _LOCALE,
    }


def _user_ref(
    state: JiraState, base: str, api: str, name: str | None
) -> dict[str, Any] | None:
    """A user as an issue field carries it: no locale, as JIRA sends it."""
    if name is None:
        return None
    full = user(state, base, api, state.ensure_user(name))
    full.pop("locale")
    return full


def _status(base: str, api: str, status: Status) -> dict[str, Any]:
    category = status.category
    return {
        "self": f"{api}/status/{status.id}",
        "description": "",
        "iconUrl": f"{base}/images/icons/statuses/generic.png",
        "name": status.name,
        "id": status.id,
        "statusCategory": {
            "self": f"{api}/statuscategory/{CATEGORY_IDS[category]}",
            "id": CATEGORY_IDS[category],
            "key": category.value,
            "colorName": CATEGORY_COLOURS[category],
            "name": CATEGORY_NAMES[category],
        },
    }


def _issue_type(base: str, api: str, project: Project, name: str) -> dict[str, Any]:
    type_id = str(project.issue_types.index(name) + 1)
    return {
        "self": f"{api}/issuetype/{type_id}",
        "id": type_id,
        "description": "",
        "iconUrl": f"{base}/images/icons/issuetypes/{name.lower()}.svg",
        "name": name,
        "subtask": False,
    }


def _priority(base: str, api: str, name: str) -> dict[str, Any]:
    priority_id = str(PRIORITIES.index(name) + 1)
    return {
        "self": f"{api}/priority/{priority_id}",
        "iconUrl": f"{base}/images/icons/priorities/{name.lower()}.svg",
        "name": name,
        "id": priority_id,
    }


def _project(base: str, api: str, project: Project) -> dict[str, Any]:
    return {
        "self": f"{api}/project/{project.id}",
        "id": project.id,
        "key": project.key,
        "name": project.name,
        "projectTypeKey": "software",
        "avatarUrls": _avatars(base, project.key),
    }


def comment(
    state: JiraState, base: str, api: str, issue: Issue, item: Comment
) -> dict[str, Any]:
    author = _user_ref(state, base, api, item.author)
    body: dict[str, Any] = {
        "self": f"{api}/issue/{issue.id}/comment/{item.id}",
        "id": item.id,
        "author": author,
        "body": item.body,
        "updateAuthor": author,
        "created": timestamp(item.created),
        "updated": timestamp(item.updated),
    }
    if state.mode is Mode.CLOUD:
        body["jsdPublic"] = True
    return body


def comments_page(
    state: JiraState, base: str, api: str, issue: Issue
) -> dict[str, Any]:
    return {
        "comments": [comment(state, base, api, issue, c) for c in issue.comments],
        "maxResults": len(issue.comments),
        "total": len(issue.comments),
        "startAt": 0,
    }


def issue(
    state: JiraState, base: str, api: str, item: Issue, fields: Iterable[str]
) -> dict[str, Any]:
    project = state.projects[item.project]
    makers = {
        "summary": lambda: item.summary,
        "status": lambda: _status(base, api, item.status),
        "issuetype": lambda: _issue_type(base, api, project, item.type),
        "assignee": lambda: _user_ref(state, base, api, item.assignee),
        "reporter": lambda: _user_ref(state, base, api, item.reporter),
        "priority": lambda: _priority(base, api, item.priority),
        "labels": lambda: list(item.labels),
        "created": lambda: timestamp(item.created),
        "updated": lambda: timestamp(item.updated),
        "description": lambda: item.description,
        "project": lambda: _project(base, api, project),
        "comment": lambda: comments_page(state, base, api, item),
    }
    return {
        "expand": _ISSUE_EXPAND,
        "id": item.id,
        "self": f"{api}/issue/{item.id}",
        "key": item.key,
        "fields": {name: makers[name]() for name in fields if name in makers},
    }


def transitions(
    base: str, api: str, project: Project, available: list[Transition]
) -> dict[str, Any]:
    return {
        "expand": "transitions",
        "transitions": [
            {
                "id": t.id,
                "name": t.name,
                "to": _status(base, api, project.statuses[t.to.lower()]),
                "hasScreen": False,
                "isGlobal": t.sources is None,
                "isInitial": False,
                "isAvailable": True,
                "isConditional": False,
                "isLooped": False,
            }
            for t in available
        ],
    }
