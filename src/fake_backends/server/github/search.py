"""The subset of GitHub's issue-search syntax the consumers send.

Shared by GraphQL ``search(type: ISSUE)`` and REST ``/search/issues``:

- scope: ``org:``, ``user:`` and ``repo:``, all OR'd together;
- ``is:pr``/``type:pr``, ``is:issue``/``type:issue``;
- ``is:open``, ``is:closed``, ``state:open|closed``, ``is:merged``,
  ``is:unmerged`` (open, or closed without merging, as GitHub documents it),
  ``draft:true|false``;
- ``archived:true|false``;
- ``created:`` and ``updated:`` with ``A..B``, ``>=A``, ``>A``, ``<=A``,
  ``<A`` or a single date, each side a date or an ISO time, ``*`` for open;
- ``author:``, ``assignee:`` (``@me`` is the caller), ``label:``;
- ``sort:created-asc|created-desc|updated-asc|updated-desc|updated|created``;
- any other word must appear in the title or body.

Results default to newest first. Only the first 1000 are retrievable, as on
GitHub, although the count reports them all.
"""

from __future__ import annotations

import datetime as dt
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

from fake_backends.server.github.model import GitHubState, Issue, Pull, Repo

__all__ = ["RESULT_CAP", "Hit", "SearchError", "parse_sort", "search"]

# GitHub returns at most this many results for one search.
RESULT_CAP = 1000
ME = "@me"


class SearchError(ValueError):
    """A search the fake cannot run, as GitHub would refuse it (422)."""


@dataclass(frozen=True)
class Hit:
    repo: Repo
    item: Pull | Issue

    @property
    def is_pr(self) -> bool:
        return isinstance(self.item, Pull)


@dataclass
class _Query:
    owners: list[str] = field(default_factory=list)
    repos: list[tuple[str, str]] = field(default_factory=list)
    predicates: list[Callable[[Hit], bool]] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    sort: tuple[str, bool] | None = None  # (field, descending)


def _bound(text: str, *, upper: bool) -> dt.datetime:
    """A date or time; a bare date as an upper bound means the end of that day."""
    try:
        if "T" in text:
            value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
        day = dt.date.fromisoformat(text)
    except ValueError:
        raise SearchError(f"invalid date {text!r} in search") from None
    start = dt.datetime.combine(day, dt.time(), tzinfo=dt.UTC)
    return start + dt.timedelta(days=1, microseconds=-1) if upper else start


def _range(text: str) -> Callable[[dt.datetime], bool]:
    if ".." in text:
        low, high = text.split("..", 1)
        lo = None if low in {"", "*"} else _bound(low, upper=False)
        hi = None if high in {"", "*"} else _bound(high, upper=True)
        return lambda t: (lo is None or t >= lo) and (hi is None or t <= hi)
    for prefix, test in (
        (">=", lambda t, b: t >= b),
        ("<=", lambda t, b: t <= b),
        (">", lambda t, b: t > b),
        ("<", lambda t, b: t < b),
    ):
        if text.startswith(prefix):
            rest = text[len(prefix) :]
            upper = prefix in {">", "<="}
            bound = _bound(rest, upper=upper)
            return lambda t, b=bound, f=test: f(t, b)
    lo, hi = _bound(text, upper=False), _bound(text, upper=True)
    return lambda t: lo <= t <= hi


_SORTS = {
    "created": ("created", True),
    "created-desc": ("created", True),
    "created-asc": ("created", False),
    "updated": ("updated", True),
    "updated-desc": ("updated", True),
    "updated-asc": ("updated", False),
    "comments": ("comments", True),
    "comments-desc": ("comments", True),
    "comments-asc": ("comments", False),
}


def parse_sort(field_name: str | None, order: str | None) -> tuple[str, bool] | None:
    """REST's ``sort`` and ``order`` parameters, which override ``sort:``."""
    if not field_name:
        return None
    if field_name not in {"created", "updated", "comments"}:
        raise SearchError(f"invalid sort {field_name!r}")
    return field_name, (order or "desc") != "asc"


def _is_merged(hit: Hit) -> bool:
    return hit.is_pr and hit.item.merged


def _qualifier(query: _Query, key: str, value: str, identity: str | None) -> bool:
    """Apply one ``key:value``. False if the key is not a known qualifier."""
    lowered = value.lower()
    match key:
        case "org" | "user":
            query.owners.append(lowered)
        case "repo":
            owner, _, name = lowered.partition("/")
            query.repos.append((owner, name))
        case "is" | "type" if lowered in {"pr", "pull-request"}:
            query.predicates.append(lambda h: h.is_pr)
        case "is" | "type" if lowered == "issue":
            query.predicates.append(lambda h: not h.is_pr)
        case "is" | "state" if lowered in {"open", "closed"}:
            query.predicates.append(lambda h, s=lowered: h.item.state == s)
        case "is" if lowered == "merged":
            query.predicates.append(_is_merged)
        case "is" if lowered == "unmerged":
            query.predicates.append(lambda h: h.is_pr and not h.item.merged)
        case "draft":
            wanted = lowered == "true"
            query.predicates.append(lambda h: h.is_pr and h.item.draft == wanted)
        case "archived":
            wanted = lowered == "true"
            query.predicates.append(lambda h: h.repo.archived == wanted)
        case "created":
            test = _range(value)
            query.predicates.append(lambda h: test(h.item.created_at))
        case "updated":
            test = _range(value)
            query.predicates.append(lambda h: test(h.item.updated_at))
        case "author":
            query.predicates.append(lambda h: h.item.author.lower() == lowered)
        case "assignee":
            who = (identity or "").lower() if lowered == ME else lowered
            query.predicates.append(
                lambda h: who in {a.lower() for a in h.item.assignees}
            )
        case "label":
            query.predicates.append(
                lambda h: lowered in {label.lower() for label in h.item.labels}
            )
        case "sort":
            if lowered not in _SORTS:
                raise SearchError(f"invalid sort qualifier {value!r}")
            query.sort = _SORTS[lowered]
        case _:
            return False
    return True


def _parse(text: str, identity: str | None) -> _Query:
    query = _Query()
    try:
        terms = shlex.split(text)
    except ValueError:
        terms = text.split()
    for term in terms:
        key, sep, value = term.partition(":")
        if sep and value and _qualifier(query, key.lower(), value, identity):
            continue
        query.words.append(term.lower())
    return query


def _sort_key(sort: tuple[str, bool]) -> Callable[[Hit], object]:
    field_name, _ = sort
    if field_name == "comments":
        return lambda h: (
            h.item.comments if h.is_pr else len(h.item.comments),
            h.item.number,
        )
    return lambda h: (getattr(h.item, f"{field_name}_at"), h.item.number)


def search(
    state: GitHubState,
    text: str,
    identity: str | None,
    sort: tuple[str, bool] | None = None,
) -> list[Hit]:
    """Every match, sorted. The caller applies RESULT_CAP when paging."""
    query = _parse(text, identity)
    hits = []
    for repo in state.repos.values():
        if query.owners or query.repos:
            in_owner = repo.owner.lower() in query.owners
            in_repo = (repo.owner.lower(), repo.name.lower()) in query.repos
            if not (in_owner or in_repo):
                continue
        for item in [*repo.pulls.values(), *repo.issues.values()]:
            hit = Hit(repo, item)
            if not all(test(hit) for test in query.predicates):
                continue
            haystack = f"{item.title}\n{item.body or ''}".lower()
            if all(word in haystack for word in query.words):
                hits.append(hit)
    chosen = sort or query.sort or ("created", True)
    hits.sort(key=_sort_key(chosen), reverse=chosen[1])
    return hits
