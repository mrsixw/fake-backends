"""REST response bodies, shaped as GitHub's OpenAPI description requires.

Every builder fills each field the schema marks required, so a response
validates against tests/github/openapi_subset.json. URLs point back at the
fake: ``api`` is where the request arrived (``http://host`` or
``http://host/api/v3``) and ``web`` is the host root, where GitHub's web pages
live, so ``html_url`` paths read ``/{owner}/{repo}/pull/{n}`` as on github.com.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Any

from fake_backends.server.github.model import (
    CheckRun,
    Comment,
    GitHubState,
    Issue,
    Pull,
    Release,
    Repo,
    Review,
    Status,
)

__all__ = [
    "Urls",
    "check_runs",
    "combined_status",
    "comment",
    "issue",
    "private_user",
    "protection",
    "pull",
    "release",
    "review",
    "search_item",
    "simple_user",
    "timestamp",
]

# Label ids are derived from the repository's, leaving room for this many.
_LABEL_IDS_PER_REPO = 100
_NO_ASSOCIATION = "NONE"
_REACTIONS = ("+1", "-1", "laugh", "hooray", "confused", "heart", "rocket", "eyes")
_OWNER_ASSOCIATION = "OWNER"


@dataclass(frozen=True)
class Urls:
    api: str
    web: str


def timestamp(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _node_id(kind: str, number: int) -> str:
    return f"{kind}_{number}"


def simple_user(state: GitHubState, urls: Urls, login: str) -> dict[str, Any]:
    user = state.ensure_user(login)
    api = f"{urls.api}/users/{user.login}"
    return {
        "login": user.login,
        "id": user.id,
        "node_id": _node_id("U", user.id),
        "avatar_url": f"{urls.web}/avatars/{user.login}",
        "gravatar_id": "",
        "url": api,
        "html_url": f"{urls.web}/{user.login}",
        "followers_url": f"{api}/followers",
        "following_url": f"{api}/following{{/other_user}}",
        "gists_url": f"{api}/gists{{/gist_id}}",
        "starred_url": f"{api}/starred{{/owner}}{{/repo}}",
        "subscriptions_url": f"{api}/subscriptions",
        "organizations_url": f"{api}/orgs",
        "repos_url": f"{api}/repos",
        "events_url": f"{api}/events{{/privacy}}",
        "received_events_url": f"{api}/received_events",
        "type": "Organization" if user.is_org else "User",
        "site_admin": False,
        "user_view_type": "public",
    }


def private_user(state: GitHubState, urls: Urls, login: str) -> dict[str, Any]:
    """``GET /user``: the authenticated account."""
    user = state.ensure_user(login)
    repos = state.repos_of(user.login)
    public = [r for r in repos if not r.private]
    stamp = (
        timestamp(min((r.created_at for r in repos), default=None))
        or "2026-01-01T00:00:00Z"
    )
    return {
        **simple_user(state, urls, login),
        "name": user.name,
        "company": None,
        "blog": "",
        "location": None,
        "email": None,
        "hireable": None,
        "bio": None,
        "public_repos": len(public),
        "public_gists": 0,
        "followers": 0,
        "following": 0,
        "created_at": stamp,
        "updated_at": stamp,
        "private_gists": 0,
        "total_private_repos": len(repos) - len(public),
        "owned_private_repos": len(repos) - len(public),
        "disk_usage": 0,
        "collaborators": 0,
        "two_factor_authentication": True,
        "twitter_username": None,
    }


def repository(state: GitHubState, urls: Urls, repo: Repo) -> dict[str, Any]:
    api = f"{urls.api}/repos/{repo.full_name}"
    open_issues = sum(1 for i in repo.issues.values() if i.state == "open") + sum(
        1 for p in repo.pulls.values() if p.state == "open"
    )
    templated = {
        "archive_url": "/{archive_format}{/ref}",
        "assignees_url": "/assignees{/user}",
        "blobs_url": "/git/blobs{/sha}",
        "branches_url": "/branches{/branch}",
        "collaborators_url": "/collaborators{/collaborator}",
        "comments_url": "/comments{/number}",
        "commits_url": "/commits{/sha}",
        "compare_url": "/compare/{base}...{head}",
        "contents_url": "/contents/{+path}",
        "git_commits_url": "/git/commits{/sha}",
        "git_refs_url": "/git/refs{/sha}",
        "git_tags_url": "/git/tags{/sha}",
        "issue_comment_url": "/issues/comments{/number}",
        "issue_events_url": "/issues/events{/number}",
        "issues_url": "/issues{/number}",
        "keys_url": "/keys{/key_id}",
        "labels_url": "/labels{/name}",
        "milestones_url": "/milestones{/number}",
        "notifications_url": "/notifications{?since,all,participating}",
        "pulls_url": "/pulls{/number}",
        "releases_url": "/releases{/id}",
        "statuses_url": "/statuses/{sha}",
        "trees_url": "/git/trees{/sha}",
    }
    plain = {
        "contributors_url": "/contributors",
        "deployments_url": "/deployments",
        "downloads_url": "/downloads",
        "events_url": "/events",
        "forks_url": "/forks",
        "hooks_url": "/hooks",
        "languages_url": "/languages",
        "merges_url": "/merges",
        "stargazers_url": "/stargazers",
        "subscribers_url": "/subscribers",
        "subscription_url": "/subscription",
        "tags_url": "/tags",
        "teams_url": "/teams",
    }
    host = urls.web.split("://", 1)[-1]
    return {
        "id": repo.id,
        "node_id": _node_id("R", repo.id),
        "name": repo.name,
        "full_name": repo.full_name,
        "owner": simple_user(state, urls, repo.owner),
        "private": repo.private,
        "html_url": f"{urls.web}/{repo.full_name}",
        "description": repo.description,
        "fork": repo.fork,
        "url": api,
        **{key: api + suffix for key, suffix in {**templated, **plain}.items()},
        "git_url": f"git://{host}/{repo.full_name}.git",
        "ssh_url": f"git@{host}:{repo.full_name}.git",
        "clone_url": f"{urls.web}/{repo.full_name}.git",
        "svn_url": f"{urls.web}/{repo.full_name}",
        "mirror_url": None,
        "homepage": None,
        "language": None,
        "forks": 0,
        "forks_count": 0,
        "stargazers_count": 0,
        "watchers": 0,
        "watchers_count": 0,
        "size": 0,
        "default_branch": repo.default_branch,
        "open_issues": open_issues,
        "open_issues_count": open_issues,
        "has_issues": True,
        "has_projects": True,
        "has_wiki": True,
        "has_pages": False,
        "has_downloads": True,
        "archived": repo.archived,
        "disabled": False,
        "license": None,
        "allow_forking": True,
        "is_template": False,
        "web_commit_signoff_required": False,
        "has_discussions": False,
        "has_pull_requests": True,
        "pull_request_creation_policy": "all",
        "topics": [],
        "visibility": "private" if repo.private else "public",
        "pushed_at": timestamp(repo.pushed_at),
        "created_at": timestamp(repo.created_at),
        "updated_at": timestamp(repo.pushed_at),
    }


def _label(urls: Urls, repo: Repo, name: str, index: int) -> dict[str, Any]:
    label = repo.label(name)
    return {
        "id": index,
        "node_id": _node_id("LA", index),
        "url": f"{urls.api}/repos/{repo.full_name}/labels/{name}",
        "name": label.name,
        "description": label.description,
        "color": label.color,
        "default": label.default,
        "archived_at": None,
        "archived_by": None,
    }


def _labels(urls: Urls, repo: Repo, names: list[str]) -> list[dict[str, Any]]:
    return [
        _label(urls, repo, name, repo.id * _LABEL_IDS_PER_REPO + i)
        for i, name in enumerate(names)
    ]


def _branch(
    state: GitHubState, urls: Urls, repo: Repo, ref: str, sha: str
) -> dict[str, Any]:
    return {
        "label": f"{repo.owner}:{ref}",
        "ref": ref,
        "sha": sha,
        "user": simple_user(state, urls, repo.owner),
        "repo": repository(state, urls, repo),
    }


def _association(repo: Repo, login: str) -> str:
    return (
        _OWNER_ASSOCIATION if login.lower() == repo.owner.lower() else _NO_ASSOCIATION
    )


def pull(state: GitHubState, urls: Urls, repo: Repo, pr: Pull) -> dict[str, Any]:
    api = f"{urls.api}/repos/{repo.full_name}"
    html = f"{urls.web}/{repo.full_name}/pull/{pr.number}"
    self_url = f"{api}/pulls/{pr.number}"
    users = [simple_user(state, urls, a) for a in pr.assignees]
    return {
        "url": self_url,
        "id": pr.id,
        "node_id": _node_id("PR", pr.id),
        "html_url": html,
        "diff_url": f"{html}.diff",
        "patch_url": f"{html}.patch",
        "issue_url": f"{api}/issues/{pr.number}",
        "commits_url": f"{self_url}/commits",
        "review_comments_url": f"{self_url}/comments",
        "review_comment_url": f"{api}/pulls/comments{{/number}}",
        "comments_url": f"{api}/issues/{pr.number}/comments",
        "statuses_url": f"{api}/statuses/{pr.head_sha}",
        "number": pr.number,
        "state": pr.state,
        "locked": False,
        "title": pr.title,
        "user": simple_user(state, urls, pr.author),
        "body": pr.body,
        "labels": _labels(urls, repo, pr.labels),
        "milestone": None,
        "active_lock_reason": None,
        "created_at": timestamp(pr.created_at),
        "updated_at": timestamp(pr.updated_at),
        "closed_at": timestamp(pr.closed_at),
        "merged_at": timestamp(pr.merged_at),
        # GitHub keeps a test merge commit for every pull request.
        "merge_commit_sha": hashlib.sha1(f"merge:{pr.head_sha}".encode()).hexdigest(),
        "assignee": users[0] if users else None,
        "assignees": users,
        "requested_reviewers": [
            simple_user(state, urls, r) for r in pr.requested_reviewers
        ],
        "requested_teams": [],
        "head": _branch(state, urls, repo, pr.head, pr.head_sha),
        "base": _branch(state, urls, repo, pr.base, pr.head_sha),
        "_links": {
            "self": {"href": self_url},
            "html": {"href": html},
            "issue": {"href": f"{api}/issues/{pr.number}"},
            "comments": {"href": f"{api}/issues/{pr.number}/comments"},
            "review_comments": {"href": f"{self_url}/comments"},
            "review_comment": {"href": f"{api}/pulls/comments{{/number}}"},
            "commits": {"href": f"{self_url}/commits"},
            "statuses": {"href": f"{api}/statuses/{pr.head_sha}"},
        },
        "author_association": _association(repo, pr.author),
        "auto_merge": None,
        "draft": pr.draft,
        "merged": pr.merged,
        "mergeable": None if pr.state == "closed" else True,
        "rebaseable": None if pr.state == "closed" else True,
        "mergeable_state": "unknown" if pr.state == "closed" else "clean",
        "merged_by": simple_user(state, urls, repo.owner) if pr.merged else None,
        "comments": pr.comments,
        "review_comments": pr.review_comments,
        "maintainer_can_modify": False,
        "commits": pr.commits,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "changed_files": pr.changed_files,
    }


def review(
    state: GitHubState, urls: Urls, repo: Repo, pr: Pull, item: Review
) -> dict[str, Any]:
    html = f"{urls.web}/{repo.full_name}/pull/{pr.number}"
    return {
        "id": item.id,
        "node_id": _node_id("PRR", item.id),
        "user": simple_user(state, urls, item.author),
        "body": item.body,
        "state": item.state,
        "html_url": f"{html}#pullrequestreview-{item.id}",
        "pull_request_url": f"{urls.api}/repos/{repo.full_name}/pulls/{pr.number}",
        "_links": {
            "html": {"href": f"{html}#pullrequestreview-{item.id}"},
            "pull_request": {
                "href": f"{urls.api}/repos/{repo.full_name}/pulls/{pr.number}"
            },
        },
        "submitted_at": timestamp(item.submitted_at),
        "commit_id": item.commit_id,
        "author_association": _association(repo, item.author),
    }


def protection(
    urls: Urls, repo: Repo, branch: str, rules: dict[str, Any]
) -> dict[str, Any]:
    base = f"{urls.api}/repos/{repo.full_name}/branches/{branch}/protection"
    return {
        "url": f"{base}/required_pull_request_reviews",
        "dismiss_stale_reviews": bool(rules.get("dismiss_stale_reviews", False)),
        "require_code_owner_reviews": bool(
            rules.get("require_code_owner_reviews", False)
        ),
        "required_approving_review_count": int(
            rules.get("required_approving_review_count", 1)
        ),
        "require_last_push_approval": bool(
            rules.get("require_last_push_approval", False)
        ),
    }


_APP_ID = 1
_APP_SLUG = "fake-ci"


def _app(state: GitHubState, urls: Urls) -> dict[str, Any]:
    """The GitHub App every seeded check run belongs to."""
    return {
        "id": _APP_ID,
        "slug": _APP_SLUG,
        "node_id": _node_id("A", _APP_ID),
        "client_id": _APP_SLUG,
        "owner": simple_user(state, urls, _APP_SLUG),
        "name": "Fake CI",
        "description": None,
        "external_url": "https://example.invalid/fake-ci",
        "html_url": "https://example.invalid/apps/fake-ci",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "permissions": {},
        "events": [],
    }


def check_runs(
    state: GitHubState, urls: Urls, repo: Repo, sha: str, runs: list[CheckRun]
) -> dict[str, Any]:
    api = f"{urls.api}/repos/{repo.full_name}"
    return {
        "total_count": len(runs),
        "check_runs": [
            {
                "id": run.id,
                "head_sha": sha,
                "node_id": _node_id("CR", run.id),
                "external_id": str(run.id),
                "url": f"{api}/check-runs/{run.id}",
                "html_url": f"{urls.web}/{repo.full_name}/runs/{run.id}",
                "details_url": f"{urls.web}/{repo.full_name}/runs/{run.id}",
                "status": run.status,
                "conclusion": run.conclusion,
                "started_at": timestamp(run.started_at),
                "completed_at": timestamp(run.completed_at),
                "output": {
                    "title": None,
                    "summary": None,
                    "text": None,
                    "annotations_count": 0,
                    "annotations_url": f"{api}/check-runs/{run.id}/annotations",
                },
                "name": run.name,
                "check_suite": {"id": run.id},
                "app": _app(state, urls),
                "pull_requests": [],
            }
            for run in runs
        ],
    }


_FAILING_STATES = frozenset({"error", "failure"})


def combined_status(
    state: GitHubState, urls: Urls, repo: Repo, sha: str, statuses: list[Status]
) -> dict[str, Any]:
    """The combined state is the worst of the latest status per context."""
    latest: dict[str, Status] = {}
    for item in sorted(statuses, key=lambda s: s.created_at):
        latest[item.context] = item
    # GitHub's documented rule: failure if any context errored or failed,
    # pending if there are none or any is pending, otherwise success.
    states = {s.state for s in latest.values()}
    if states & _FAILING_STATES:
        combined = "failure"
    elif not states or "pending" in states:
        combined = "pending"
    else:
        combined = "success"
    api = f"{urls.api}/repos/{repo.full_name}"
    return {
        "state": combined,
        "statuses": [
            {
                "url": f"{api}/statuses/{sha}",
                "avatar_url": None,
                "id": s.id,
                "node_id": _node_id("SC", s.id),
                "state": s.state,
                "description": s.description,
                "target_url": None,
                "context": s.context,
                "created_at": timestamp(s.created_at),
                "updated_at": timestamp(s.created_at),
            }
            for s in latest.values()
        ],
        "sha": sha,
        "total_count": len(latest),
        "repository": {
            key: value
            for key, value in repository(state, urls, repo).items()
            if key in _MINIMAL_REPOSITORY_KEYS
        },
        "commit_url": f"{api}/commits/{sha}",
        "url": f"{api}/commits/{sha}/status",
    }


# The fields of GitHub's minimal-repository the combined status carries.
_MINIMAL_REPOSITORY_KEYS = {
    "id",
    "node_id",
    "name",
    "full_name",
    "owner",
    "private",
    "html_url",
    "description",
    "fork",
    "url",
    "archive_url",
    "assignees_url",
    "blobs_url",
    "branches_url",
    "collaborators_url",
    "comments_url",
    "commits_url",
    "compare_url",
    "contents_url",
    "contributors_url",
    "deployments_url",
    "downloads_url",
    "events_url",
    "forks_url",
    "git_commits_url",
    "git_refs_url",
    "git_tags_url",
    "git_url",
    "issue_comment_url",
    "issue_events_url",
    "issues_url",
    "keys_url",
    "labels_url",
    "languages_url",
    "merges_url",
    "milestones_url",
    "notifications_url",
    "pulls_url",
    "releases_url",
    "ssh_url",
    "stargazers_url",
    "statuses_url",
    "subscribers_url",
    "subscription_url",
    "tags_url",
    "teams_url",
    "trees_url",
    "hooks_url",
}


def _issue_core(
    state: GitHubState, urls: Urls, repo: Repo, item: Pull | Issue
) -> dict[str, Any]:
    api = f"{urls.api}/repos/{repo.full_name}"
    is_pr = isinstance(item, Pull)
    kind = "pull" if is_pr else "issues"
    url = f"{api}/issues/{item.number}"
    users = [simple_user(state, urls, a) for a in item.assignees]
    comment_count = item.comments if is_pr else len(item.comments)
    body: dict[str, Any] = {
        "id": item.id,
        "node_id": _node_id("PR" if is_pr else "I", item.id),
        "url": url,
        "repository_url": api,
        "labels_url": f"{url}/labels{{/name}}",
        "comments_url": f"{url}/comments",
        "events_url": f"{url}/events",
        "html_url": f"{urls.web}/{repo.full_name}/{kind}/{item.number}",
        "number": item.number,
        "state": item.state,
        "state_reason": None if is_pr else item.state_reason,
        "title": item.title,
        "body": item.body,
        "user": simple_user(state, urls, item.author),
        "labels": _labels(urls, repo, item.labels),
        "assignee": users[0] if users else None,
        "assignees": users,
        "milestone": None,
        "locked": False,
        "active_lock_reason": None,
        "comments": comment_count,
        "closed_at": timestamp(item.closed_at),
        "created_at": timestamp(item.created_at),
        "updated_at": timestamp(item.updated_at),
        "author_association": _association(repo, item.author),
        "timeline_url": f"{url}/timeline",
        "performed_via_github_app": None,
        "reactions": {
            "url": f"{url}/reactions",
            "total_count": 0,
            **dict.fromkeys(_REACTIONS, 0),
        },
        "type": None,
        "issue_field_values": [],
    }
    if is_pr:
        html = f"{urls.web}/{repo.full_name}/pull/{item.number}"
        body["draft"] = item.draft
        body["pull_request"] = {
            "url": f"{api}/pulls/{item.number}",
            "html_url": html,
            "diff_url": f"{html}.diff",
            "patch_url": f"{html}.patch",
            "merged_at": timestamp(item.merged_at),
        }
    return body


def issue(
    state: GitHubState, urls: Urls, repo: Repo, item: Pull | Issue
) -> dict[str, Any]:
    """An issue as ``/repos/{o}/{r}/issues`` returns it.

    A pull request read this way carries a ``pull_request`` key, as on GitHub.
    Unlike a search result, it says who closed it.
    """
    closer = simple_user(state, urls, repo.owner) if item.state == "closed" else None
    return {**_issue_core(state, urls, repo, item), "closed_by": closer}


def search_item(
    state: GitHubState, urls: Urls, repo: Repo, item: Pull | Issue
) -> dict[str, Any]:
    # GitHub's schema makes a search item's body a string, never null.
    core = _issue_core(state, urls, repo, item)
    return {**core, "body": core["body"] or "", "score": 1.0}


def comment(
    state: GitHubState, urls: Urls, repo: Repo, number: int, item: Comment
) -> dict[str, Any]:
    api = f"{urls.api}/repos/{repo.full_name}"
    return {
        "id": item.id,
        "node_id": _node_id("IC", item.id),
        "url": f"{api}/issues/comments/{item.id}",
        "html_url": (
            f"{urls.web}/{repo.full_name}/issues/{number}#issuecomment-{item.id}"
        ),
        "body": item.body,
        "user": simple_user(state, urls, item.author),
        "created_at": timestamp(item.created_at),
        "updated_at": timestamp(item.updated_at),
        "issue_url": f"{api}/issues/{number}",
        "author_association": _association(repo, item.author),
    }


def release(
    state: GitHubState, urls: Urls, repo: Repo, item: Release
) -> dict[str, Any]:
    api = f"{urls.api}/repos/{repo.full_name}"
    html = f"{urls.web}/{repo.full_name}/releases/tag/{item.tag}"
    return {
        "url": f"{api}/releases/{item.id}",
        "html_url": html,
        "assets_url": f"{api}/releases/{item.id}/assets",
        "upload_url": (
            f"{urls.api}/uploads/repos/{repo.full_name}/releases/{item.id}"
            "/assets{?name,label}"
        ),
        "tarball_url": f"{api}/tarball/{item.tag}",
        "zipball_url": f"{api}/zipball/{item.tag}",
        "id": item.id,
        "node_id": _node_id("RE", item.id),
        "tag_name": item.tag,
        "target_commitish": item.target,
        "name": item.name,
        "body": item.body,
        "draft": item.draft,
        "prerelease": item.prerelease,
        "created_at": timestamp(item.created_at),
        "published_at": timestamp(item.published_at),
        "author": simple_user(state, urls, item.author),
        "assets": [
            {
                "url": f"{api}/releases/assets/{asset.id}",
                "browser_download_url": (
                    f"{urls.web}/{repo.full_name}/releases/download/"
                    f"{item.tag}/{asset.name}"
                ),
                "id": asset.id,
                "node_id": _node_id("RA", asset.id),
                "name": asset.name,
                "label": None,
                "state": "uploaded",
                "content_type": asset.content_type,
                "size": len(asset.content),
                "digest": None,
                "download_count": 0,
                "created_at": timestamp(item.created_at),
                "updated_at": timestamp(item.created_at),
                "uploader": None,
            }
            for asset in item.assets
        ],
    }
