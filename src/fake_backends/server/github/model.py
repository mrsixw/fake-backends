"""fake-github's state: what a seed declares, parsed once and then served.

A seed is YAML or JSON with these top-level keys besides ``tokens`` (see
docs/github.md for every field):

- ``users`` and ``orgs``: accounts. Anyone named elsewhere (an author, a
  reviewer, a repository owner not listed as an org) becomes a user.
- ``repos``: repositories, each with its ``pulls``, ``issues``, ``releases``
  and branch ``protection``.
- ``generate``: bulk pull requests, for searches too big to write by hand.
- ``rate_limits``: per-bucket limits, to make exhaustion reachable.

Logins and repository names are matched case-insensitively, as on GitHub.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "DEFAULT_TIMESTAMP",
    "Asset",
    "Bucket",
    "CheckRun",
    "Comment",
    "Contribution",
    "ContributionKind",
    "GitHubState",
    "Issue",
    "Pull",
    "Release",
    "Repo",
    "Review",
    "Status",
    "User",
    "parse_state",
]

# Every timestamp a seed leaves out. Fixed, so two runs answer identically.
DEFAULT_TIMESTAMP = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
# IDs are assigned from here, in seed order, so they are stable too.
_FIRST_ID = 1000
_STEP_MINUTES_DEFAULT = 60


class Bucket(StrEnum):
    """GitHub's rate-limit buckets: REST, REST search, and GraphQL."""

    CORE = "core"
    SEARCH = "search"
    GRAPHQL = "graphql"


class ContributionKind(StrEnum):
    COMMIT = "commit"
    PULL_REQUEST = "pull_request"
    PULL_REQUEST_REVIEW = "pull_request_review"
    ISSUE = "issue"
    REPOSITORY = "repository"
    RESTRICTED = "restricted"


# GitHub's defaults for an authenticated user: 5000 an hour for REST and for
# GraphQL, and 30 a minute for search.
HOURLY_LIMIT = 5000
SEARCH_LIMIT = 30
HOUR_SECONDS = 3600
MINUTE_SECONDS = 60
DEFAULT_LIMITS: dict[Bucket, tuple[int, int]] = {
    Bucket.CORE: (HOURLY_LIMIT, HOUR_SECONDS),
    Bucket.SEARCH: (SEARCH_LIMIT, MINUTE_SECONDS),
    Bucket.GRAPHQL: (HOURLY_LIMIT, HOUR_SECONDS),
}


@dataclass
class Contribution:
    date: dt.date
    kind: ContributionKind
    count: int


@dataclass
class User:
    login: str
    id: int
    name: str | None = None
    is_org: bool = False
    contributions: list[Contribution] = field(default_factory=list)


@dataclass
class Review:
    id: int
    author: str
    state: str
    body: str
    submitted_at: dt.datetime
    commit_id: str


@dataclass
class CheckRun:
    id: int
    name: str
    status: str
    conclusion: str | None
    started_at: dt.datetime
    completed_at: dt.datetime | None


@dataclass
class Status:
    id: int
    context: str
    state: str
    description: str | None
    created_at: dt.datetime


@dataclass
class Comment:
    id: int
    author: str
    body: str
    created_at: dt.datetime
    updated_at: dt.datetime


@dataclass
class Pull:
    id: int
    number: int
    title: str
    body: str | None
    author: str
    state: str  # "open" or "closed"
    merged: bool
    draft: bool
    labels: list[str]
    assignees: list[str]
    requested_reviewers: list[str]
    head: str
    head_sha: str
    base: str
    created_at: dt.datetime
    updated_at: dt.datetime
    closed_at: dt.datetime | None
    merged_at: dt.datetime | None
    additions: int
    deletions: int
    changed_files: int
    commits: int
    comments: int
    review_comments: int
    review_decision: str | None
    reviews: list[Review]
    check_runs: list[CheckRun]
    statuses: list[Status]


@dataclass
class Issue:
    id: int
    number: int
    title: str
    body: str | None
    author: str
    state: str
    state_reason: str | None
    labels: list[str]
    assignees: list[str]
    created_at: dt.datetime
    updated_at: dt.datetime
    closed_at: dt.datetime | None
    comments: list[Comment]


@dataclass
class Asset:
    id: int
    name: str
    content: bytes
    content_type: str


@dataclass
class Release:
    id: int
    tag: str
    name: str | None
    body: str | None
    draft: bool
    prerelease: bool
    target: str
    author: str
    created_at: dt.datetime
    published_at: dt.datetime | None
    assets: list[Asset]


@dataclass
class Label:
    name: str
    description: str
    color: str
    default: bool


# The labels GitHub gives every new repository, as it describes them.
GITHUB_DEFAULT_LABELS = (
    Label("bug", "Something isn't working", "d73a4a", True),
    Label(
        "documentation", "Improvements or additions to documentation", "0075ca", True
    ),
    Label("duplicate", "This issue or pull request already exists", "cfd3d7", True),
    Label("enhancement", "New feature or request", "a2eeef", True),
    Label("good first issue", "Good for newcomers", "7057ff", True),
    Label("help wanted", "Extra attention is needed", "008672", True),
    Label("invalid", "This doesn't seem right", "e4e669", True),
    Label("question", "Further information is requested", "d876e3", True),
    Label("wontfix", "This will not be worked on", "ffffff", True),
)
# A label used without being declared, as GitHub creates one.
UNDECLARED_LABEL_COLOR = "ededed"


@dataclass
class Repo:
    id: int
    owner: str
    name: str
    archived: bool
    fork: bool
    private: bool
    description: str | None
    default_branch: str
    created_at: dt.datetime
    pushed_at: dt.datetime
    protection: dict[str, dict[str, Any]]
    pulls: dict[int, Pull]
    issues: dict[int, Issue]
    releases: list[Release]
    labels: dict[str, Label] = field(default_factory=dict)

    def label(self, name: str) -> Label:
        return self.labels.get(name.lower()) or Label(
            name, "", UNDECLARED_LABEL_COLOR, False
        )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def next_number(self) -> int:
        """Pull requests and issues share one sequence, as on GitHub."""
        return max(itertools.chain(self.pulls, self.issues, [0])) + 1


@dataclass
class GitHubState:
    users: dict[str, User] = field(default_factory=dict)
    repos: dict[tuple[str, str], Repo] = field(default_factory=dict)
    limits: dict[Bucket, tuple[int, int]] = field(
        default_factory=lambda: dict(DEFAULT_LIMITS)
    )
    # (identity, bucket) -> (used, window reset epoch seconds)
    usage: dict[tuple[str, Bucket], tuple[int, int]] = field(default_factory=dict)
    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(_FIRST_ID))

    def new_id(self) -> int:
        return next(self._ids)

    def user(self, login: str) -> User | None:
        return self.users.get(login.lower())

    def ensure_user(self, login: str) -> User:
        found = self.user(login)
        if found is None:
            found = User(login=login, id=self.new_id())
            self.users[login.lower()] = found
        return found

    def repo(self, owner: str, name: str) -> Repo | None:
        return self.repos.get((owner.lower(), name.lower()))

    def repos_of(self, owner: str) -> list[Repo]:
        return sorted(
            (r for r in self.repos.values() if r.owner.lower() == owner.lower()),
            key=lambda r: r.name.lower(),
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


def _opt_timestamp(item: Mapping[str, Any], key: str, where: str) -> dt.datetime | None:
    value = item.get(key)
    return None if value is None else _timestamp(value, f"{where}.{key}")


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


def _choice(value: str, allowed: set[str], where: str) -> str:
    if value not in allowed:
        raise ValueError(f"{where}: {value!r} must be one of {sorted(allowed)}")
    return value


_PULL_STATES = {"open", "closed", "merged"}
_ISSUE_STATES = {"open", "closed"}
_STATE_REASONS = {"completed", "not_planned", "reopened"}
_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"}
_REVIEW_DECISIONS = {"APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"}
_CHECK_STATUSES = {"queued", "in_progress", "completed"}
_CHECK_CONCLUSIONS = {
    "success",
    "failure",
    "neutral",
    "cancelled",
    "skipped",
    "timed_out",
    "action_required",
}
_STATUS_STATES = {"error", "failure", "pending", "success"}


def _fake_sha(*parts: object) -> str:
    """A stable 40-hex-digit SHA for a seed that does not give one."""
    return hashlib.sha1("/".join(map(str, parts)).encode()).hexdigest()


def _parse_user(state: GitHubState, raw: Any, where: str, *, is_org: bool) -> None:
    item = _mapping(raw, where)
    login = str(_required(item, "login", where))
    user = state.ensure_user(login)
    user.is_org = is_org
    user.name = item.get("name")
    for i, entry in enumerate(
        _list(item.get("contributions"), f"{where}.contributions")
    ):
        at = f"{where}.contributions[{i}]"
        entry = _mapping(entry, at)
        kind = str(entry.get("kind", ContributionKind.COMMIT))
        user.contributions.append(
            Contribution(
                date=_timestamp(_required(entry, "date", at), f"{at}.date").date(),
                kind=ContributionKind(
                    _choice(kind, {k.value for k in ContributionKind}, f"{at}.kind")
                ),
                count=int(entry.get("count", 1)),
            )
        )


def _parse_pull(
    state: GitHubState, repo: Repo, item: Mapping[str, Any], where: str
) -> Pull:
    number = int(_required(item, "number", where))
    raw_state = _choice(str(item.get("state", "open")), _PULL_STATES, f"{where}.state")
    created = _timestamp(
        item.get("created_at", DEFAULT_TIMESTAMP), f"{where}.created_at"
    )
    updated = _timestamp(item.get("updated_at", created), f"{where}.updated_at")
    closed = _opt_timestamp(item, "closed_at", where)
    merged_at = _opt_timestamp(item, "merged_at", where)
    if raw_state != "open" and closed is None:
        closed = updated
    if raw_state == "merged" and merged_at is None:
        merged_at = closed
    author = str(item.get("author", repo.owner))
    state.ensure_user(author)
    head = str(item.get("head", f"branch-{number}"))
    head_sha = str(item.get("head_sha", _fake_sha(repo.full_name, number, head)))

    reviews = []
    for i, raw in enumerate(_list(item.get("reviews"), f"{where}.reviews")):
        at = f"{where}.reviews[{i}]"
        review = _mapping(raw, at)
        reviewer = str(_required(review, "author", at))
        state.ensure_user(reviewer)
        reviews.append(
            Review(
                id=state.new_id(),
                author=reviewer,
                state=_choice(
                    str(review.get("state", "COMMENTED")), _REVIEW_STATES, f"{at}.state"
                ),
                body=str(review.get("body", "")),
                submitted_at=_timestamp(
                    review.get("submitted_at", updated), f"{at}.submitted_at"
                ),
                commit_id=head_sha,
            )
        )

    checks = []
    for i, raw in enumerate(_list(item.get("check_runs"), f"{where}.check_runs")):
        at = f"{where}.check_runs[{i}]"
        check = _mapping(raw, at)
        status = _choice(
            str(check.get("status", "completed")), _CHECK_STATUSES, f"{at}.status"
        )
        conclusion = check.get(
            "conclusion", "success" if status == "completed" else None
        )
        if conclusion is not None:
            _choice(str(conclusion), _CHECK_CONCLUSIONS, f"{at}.conclusion")
        started = _timestamp(check.get("started_at", created), f"{at}.started_at")
        checks.append(
            CheckRun(
                id=state.new_id(),
                name=str(_required(check, "name", at)),
                status=status,
                conclusion=conclusion,
                started_at=started,
                completed_at=started if status == "completed" else None,
            )
        )

    statuses = []
    for i, raw in enumerate(_list(item.get("statuses"), f"{where}.statuses")):
        at = f"{where}.statuses[{i}]"
        entry = _mapping(raw, at)
        statuses.append(
            Status(
                id=state.new_id(),
                context=str(_required(entry, "context", at)),
                state=_choice(
                    str(entry.get("state", "success")), _STATUS_STATES, f"{at}.state"
                ),
                description=entry.get("description"),
                created_at=_timestamp(
                    entry.get("created_at", created), f"{at}.created_at"
                ),
            )
        )

    decision = item.get("review_decision")
    if decision is not None:
        _choice(str(decision), _REVIEW_DECISIONS, f"{where}.review_decision")
    for login in [*item.get("assignees", []), *item.get("requested_reviewers", [])]:
        state.ensure_user(str(login))

    return Pull(
        id=state.new_id(),
        number=number,
        title=str(item.get("title", f"Pull request {number}")),
        body=item.get("body"),
        author=author,
        state="open" if raw_state == "open" else "closed",
        merged=raw_state == "merged",
        draft=bool(item.get("draft", False)),
        labels=[str(label) for label in _list(item.get("labels"), f"{where}.labels")],
        assignees=[str(a) for a in _list(item.get("assignees"), f"{where}.assignees")],
        requested_reviewers=[
            str(r)
            for r in _list(
                item.get("requested_reviewers"), f"{where}.requested_reviewers"
            )
        ],
        head=head,
        head_sha=head_sha,
        base=str(item.get("base", repo.default_branch)),
        created_at=created,
        updated_at=updated,
        closed_at=closed if raw_state != "open" else None,
        merged_at=merged_at if raw_state == "merged" else None,
        additions=int(item.get("additions", 1)),
        deletions=int(item.get("deletions", 0)),
        changed_files=int(item.get("changed_files", 1)),
        commits=int(item.get("commits", 1)),
        comments=int(item.get("comments", 0)),
        review_comments=int(item.get("review_comments", 0)),
        review_decision=decision,
        reviews=reviews,
        check_runs=checks,
        statuses=statuses,
    )


def _parse_issue(
    state: GitHubState, repo: Repo, item: Mapping[str, Any], where: str
) -> Issue:
    number = int(_required(item, "number", where))
    issue_state = _choice(
        str(item.get("state", "open")), _ISSUE_STATES, f"{where}.state"
    )
    reason = item.get("state_reason")
    if reason is not None:
        _choice(str(reason), _STATE_REASONS, f"{where}.state_reason")
    created = _timestamp(
        item.get("created_at", DEFAULT_TIMESTAMP), f"{where}.created_at"
    )
    updated = _timestamp(item.get("updated_at", created), f"{where}.updated_at")
    closed = _opt_timestamp(item, "closed_at", where)
    author = str(item.get("author", repo.owner))
    state.ensure_user(author)
    comments = []
    for i, raw in enumerate(_list(item.get("comments"), f"{where}.comments")):
        at = f"{where}.comments[{i}]"
        entry = _mapping(raw, at)
        commenter = str(entry.get("author", author))
        state.ensure_user(commenter)
        when = _timestamp(entry.get("created_at", created), f"{at}.created_at")
        comments.append(
            Comment(
                id=state.new_id(),
                author=commenter,
                body=str(entry.get("body", "")),
                created_at=when,
                updated_at=when,
            )
        )
    assignees = [str(a) for a in _list(item.get("assignees"), f"{where}.assignees")]
    for login in assignees:
        state.ensure_user(login)
    return Issue(
        id=state.new_id(),
        number=number,
        title=str(item.get("title", f"Issue {number}")),
        body=item.get("body"),
        author=author,
        state=issue_state,
        state_reason=reason,
        labels=[str(label) for label in _list(item.get("labels"), f"{where}.labels")],
        assignees=assignees,
        created_at=created,
        updated_at=updated,
        closed_at=(closed or updated) if issue_state == "closed" else None,
        comments=comments,
    )


def _parse_release(
    state: GitHubState, repo: Repo, item: Mapping[str, Any], where: str
) -> Release:
    created = _timestamp(
        item.get("created_at", DEFAULT_TIMESTAMP), f"{where}.created_at"
    )
    draft = bool(item.get("draft", False))
    assets = []
    for i, raw in enumerate(_list(item.get("assets"), f"{where}.assets")):
        at = f"{where}.assets[{i}]"
        asset = _mapping(raw, at)
        content = asset.get("content", "")
        assets.append(
            Asset(
                id=state.new_id(),
                name=str(_required(asset, "name", at)),
                content=(
                    content.encode() if isinstance(content, str) else bytes(content)
                ),
                content_type=str(asset.get("content_type", "application/octet-stream")),
            )
        )
    return Release(
        id=state.new_id(),
        tag=str(_required(item, "tag", where)),
        name=item.get("name"),
        body=item.get("body"),
        draft=draft,
        prerelease=bool(item.get("prerelease", False)),
        target=str(item.get("target", repo.default_branch)),
        author=repo.owner,
        created_at=created,
        published_at=(
            None
            if draft
            else _timestamp(item.get("published_at", created), f"{where}.published_at")
        ),
        assets=assets,
    )


def _parse_repo(state: GitHubState, raw: Any, where: str) -> None:
    item = _mapping(raw, where)
    owner = str(_required(item, "owner", where))
    name = str(_required(item, "name", where))
    if state.repo(owner, name) is not None:
        raise ValueError(f"{where}: {owner}/{name} is declared twice")
    owner_user = state.ensure_user(owner)
    created = _timestamp(
        item.get("created_at", DEFAULT_TIMESTAMP), f"{where}.created_at"
    )
    repo = Repo(
        id=state.new_id(),
        owner=owner_user.login,
        name=name,
        archived=bool(item.get("archived", False)),
        fork=bool(item.get("fork", False)),
        private=bool(item.get("private", False)),
        description=item.get("description"),
        default_branch=str(item.get("default_branch", "main")),
        created_at=created,
        pushed_at=_timestamp(item.get("pushed_at", created), f"{where}.pushed_at"),
        protection={
            str(branch): dict(_mapping(rules, f"{where}.protection.{branch}"))
            for branch, rules in _mapping(
                item.get("protection", {}), f"{where}.protection"
            ).items()
        },
        pulls={},
        issues={},
        releases=[],
        labels=_parse_labels(item.get("labels"), f"{where}.labels"),
    )
    state.repos[(owner.lower(), name.lower())] = repo
    for i, pull in enumerate(_list(item.get("pulls"), f"{where}.pulls")):
        _add(
            repo,
            _parse_pull(
                state,
                repo,
                _mapping(pull, f"{where}.pulls[{i}]"),
                f"{where}.pulls[{i}]",
            ),
            where,
        )
    for i, issue in enumerate(_list(item.get("issues"), f"{where}.issues")):
        _add(
            repo,
            _parse_issue(
                state,
                repo,
                _mapping(issue, f"{where}.issues[{i}]"),
                f"{where}.issues[{i}]",
            ),
            where,
        )
    for i, release in enumerate(_list(item.get("releases"), f"{where}.releases")):
        repo.releases.append(
            _parse_release(
                state,
                repo,
                _mapping(release, f"{where}.releases[{i}]"),
                f"{where}.releases[{i}]",
            )
        )


def _parse_labels(raw: Any, where: str) -> dict[str, Label]:
    """A repository's labels; GitHub's defaults unless the seed lists its own."""
    if raw is None:
        return {label.name: label for label in GITHUB_DEFAULT_LABELS}
    labels = {}
    for i, entry in enumerate(_list(raw, where)):
        at = f"{where}[{i}]"
        entry = _mapping(entry, at)
        name = str(_required(entry, "name", at))
        labels[name.lower()] = Label(
            name=name,
            description=str(entry.get("description", "")),
            color=str(entry.get("color", UNDECLARED_LABEL_COLOR)),
            default=bool(entry.get("default", False)),
        )
    return labels


def _add(repo: Repo, item: Pull | Issue, where: str) -> None:
    if item.number in repo.pulls or item.number in repo.issues:
        raise ValueError(
            f"{where}: number {item.number} is used twice in {repo.full_name}; "
            "pull requests and issues share one sequence"
        )
    target = repo.pulls if isinstance(item, Pull) else repo.issues
    target[item.number] = item


def _parse_generate(state: GitHubState, raw: Any, where: str) -> None:
    """Add ``count`` pull requests to a repository, one every ``step_minutes``."""
    item = _mapping(raw, where)
    full_name = str(_required(item, "repo", where))
    owner, _, name = full_name.partition("/")
    repo = state.repo(owner, name)
    if repo is None:
        raise ValueError(f"{where}: repo {full_name!r} is not declared in repos")
    count = int(_required(item, "pulls", where))
    start = _timestamp(item.get("start", DEFAULT_TIMESTAMP), f"{where}.start")
    step = dt.timedelta(minutes=int(item.get("step_minutes", _STEP_MINUTES_DEFAULT)))
    template = {
        k: v
        for k, v in item.items()
        if k not in {"repo", "pulls", "start", "step_minutes"}
    }
    for offset in range(count):
        number = repo.next_number()
        created = start + step * offset
        pull = {
            "title": f"Generated pull request {number}",
            **template,
            "number": number,
            "created_at": created,
            "updated_at": created,
        }
        _add(repo, _parse_pull(state, repo, pull, f"{where}[{offset}]"), where)


def _parse_limits(state: GitHubState, raw: Any) -> None:
    for bucket_name, settings in _mapping(raw, "rate_limits").items():
        where = f"rate_limits.{bucket_name}"
        bucket = Bucket(_choice(str(bucket_name), {b.value for b in Bucket}, where))
        settings = _mapping(settings, where)
        default_limit, default_window = DEFAULT_LIMITS[bucket]
        state.limits[bucket] = (
            int(settings.get("limit", default_limit)),
            int(settings.get("window_seconds", default_window)),
        )


_KNOWN_KEYS = {"users", "orgs", "repos", "generate", "rate_limits"}


def parse_state(data: Mapping[str, Any]) -> GitHubState:
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(
            f"unknown seed keys {unknown}; fake-github reads tokens and "
            f"{sorted(_KNOWN_KEYS)}"
        )
    state = GitHubState()
    for i, raw in enumerate(_list(data.get("orgs"), "orgs")):
        _parse_user(state, raw, f"orgs[{i}]", is_org=True)
    for i, raw in enumerate(_list(data.get("users"), "users")):
        _parse_user(state, raw, f"users[{i}]", is_org=False)
    for i, raw in enumerate(_list(data.get("repos"), "repos")):
        _parse_repo(state, raw, f"repos[{i}]")
    for i, raw in enumerate(_list(data.get("generate"), "generate")):
        _parse_generate(state, raw, f"generate[{i}]")
    if "rate_limits" in data:
        _parse_limits(state, data["rate_limits"])
    return state
