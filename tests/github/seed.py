"""A small GitHub for the tests: one org, a user, live and archived repos."""

__all__ = ["ORG", "REPO", "SEED", "OLD_REPO", "OTHER_USER"]

ORG = "acme"
REPO = "widgets"
OLD_REPO = "museum"
OTHER_USER = "bob"

SEED = {
    "orgs": [{"login": ORG}],
    "users": [
        {
            "login": "alice",
            "name": "Alice",
            "contributions": [
                {"date": "2026-02-01", "kind": "commit", "count": 5},
                {"date": "2026-02-02", "kind": "pull_request", "count": 2},
                {"date": "2026-02-03", "kind": "pull_request_review", "count": 3},
                {"date": "2026-02-04", "kind": "issue", "count": 1},
                {"date": "2025-01-01", "kind": "commit", "count": 100},
            ],
        },
        {"login": OTHER_USER, "contributions": [{"date": "2026-03-01", "count": 4}]},
    ],
    "repos": [
        {
            "owner": ORG,
            "name": REPO,
            "protection": {"main": {"required_approving_review_count": 2}},
            "pulls": [
                {
                    "number": 1,
                    "title": "Add sprockets",
                    "author": "alice",
                    "labels": ["enhancement"],
                    "assignees": ["alice"],
                    "requested_reviewers": [OTHER_USER],
                    "created_at": "2026-03-01T10:00:00Z",
                    "updated_at": "2026-03-05T10:00:00Z",
                    "review_decision": "APPROVED",
                    "reviews": [
                        {"author": OTHER_USER, "state": "APPROVED"},
                        {"author": "carol", "state": "COMMENTED"},
                    ],
                    "check_runs": [
                        {"name": "test", "conclusion": "success"},
                        {"name": "lint", "status": "in_progress"},
                    ],
                    "statuses": [{"context": "ci/legacy", "state": "success"}],
                },
                {
                    "number": 2,
                    "title": "Draft: rework cogs",
                    "author": OTHER_USER,
                    "draft": True,
                    "labels": ["wip"],
                    "created_at": "2026-03-02T10:00:00Z",
                },
                {
                    "number": 3,
                    "title": "Merged fix",
                    "author": "alice",
                    "state": "merged",
                    "created_at": "2026-02-01T10:00:00Z",
                    "merged_at": "2026-02-02T10:00:00Z",
                },
                {
                    "number": 4,
                    "title": "Abandoned idea",
                    "author": OTHER_USER,
                    "state": "closed",
                    "created_at": "2026-01-15T10:00:00Z",
                },
            ],
            "issues": [
                {
                    "number": 5,
                    "title": "Sprockets squeak",
                    "body": "They squeak when wet.",
                    "author": OTHER_USER,
                    "labels": ["bug"],
                    "assignees": ["alice"],
                    "created_at": "2026-03-03T10:00:00Z",
                    "comments": [{"author": "alice", "body": "Confirmed."}],
                },
                {
                    "number": 6,
                    "title": "Old question",
                    "state": "closed",
                    "state_reason": "completed",
                    "created_at": "2026-01-01T10:00:00Z",
                },
            ],
            "releases": [
                {
                    "tag": "v1.0.0",
                    "published_at": "2026-02-01T00:00:00Z",
                    "assets": [{"name": "widgets", "content": "#!/bin/sh\necho old\n"}],
                },
                {
                    "tag": "v1.1.0",
                    "published_at": "2026-03-01T00:00:00Z",
                    "assets": [{"name": "widgets", "content": "#!/bin/sh\necho new\n"}],
                },
                {
                    "tag": "v2.0.0-rc1",
                    "prerelease": True,
                    "published_at": "2026-04-01T00:00:00Z",
                },
            ],
        },
        {
            "owner": ORG,
            "name": OLD_REPO,
            "archived": True,
            "pulls": [
                {
                    "number": 1,
                    "title": "Archived PR",
                    "created_at": "2025-06-01T00:00:00Z",
                }
            ],
        },
    ],
}
