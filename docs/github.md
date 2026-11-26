# fake-github

The GitHub REST and GraphQL calls that [breakfast](https://github.com/mrsixw/breakfast), [gh-snitch](https://github.com/mrsixw/gh-snitch) and [grind](https://github.com/mrsixw/grind) make, served from a seed. Built in [issue 2](https://github.com/mrsixw/fake-backends/issues/2).

## Where it listens

| Path | Serves |
|---|---|
| `POST /graphql`, `POST /api/graphql` | GraphQL, api.github.com style and Enterprise style |
| `/user`, `/repos/...`, `/search/issues` | REST, api.github.com style |
| `/api/v3/...` | The same REST, Enterprise style |
| `/{owner}/{repo}/releases/latest/download/{name}`, `/{owner}/{repo}/releases/download/{tag}/{name}` | Release assets, github.com's web paths |

Web URLs in responses (`html_url`, a GraphQL `url`) point at the fake's host root, so their paths read `/{owner}/{repo}/pull/{n}` exactly as on github.com.

**Tokens:** `Authorization: Bearer <t>` or `token <t>`. An unknown or missing token gets `401` with `{"message": "Bad credentials", ...}`. Two calls need no token: `GET .../releases/latest` and the asset downloads, which the consumers' update checks make.

## GraphQL

Queries run on graphql-core against [`schema.graphql`](../src/fake_backends/server/github/schema.graphql), a trimmed copy of GitHub's public schema. Aliases, variables, fragments and `@include` work as on github.com.

| Field | Notes |
|---|---|
| `search(type: ISSUE, query, first, after)` | `issueCount`, `nodes` (`... on PullRequest` / `... on Issue`) and `pageInfo`. Cursors look like GitHub's (base64 `cursor:N`). Only the first 1000 results can be paged to; `issueCount` counts them all. |
| `repositoryOwner(login)` | `login` and `repositories(first, after, isArchived, isFork)`. An unknown login is `null` with no error. |
| `repository(owner, name)` | `pullRequest(number)` with `reviewDecision` and `reviews(first, states) { totalCount nodes { author { login } state } }`. An unknown repository or number is `null` plus a `NOT_FOUND` error. |
| `user(login)` | `contributionsCollection(from, to)`: `contributionCalendar.totalContributions` and the six breakdown counters. A span over a year is refused; an unknown login is a `NOT_FOUND` error. |
| `rateLimit` | `cost`, `limit`, `remaining`, `resetAt`, `used`, `nodeCount`. Agrees with the response's headers. |

`first` is required on every connection and may not exceed 100, as on GitHub. Errors carry GitHub's top-level `type`.

## REST

| Endpoint | Notes |
|---|---|
| `GET /user` | The token's identity |
| `GET /repos/{o}/{r}/pulls/{n}` | |
| `GET /repos/{o}/{r}/pulls/{n}/reviews` | Page-number paging with a `Link` header |
| `GET /repos/{o}/{r}/branches/{b}/protection/required_pull_request_reviews` | `404 Branch not protected` unless the seed protects the branch |
| `GET /repos/{o}/{r}/commits/{ref}/check-runs` and `/status` | `ref` is a PR's head SHA or branch; anything else is a `422` |
| `GET /search/issues` | `q`, `sort`, `order`, `per_page`, `page`. Past the first 1000 results is a `422`. |
| `GET` and `POST /repos/{o}/{r}/issues` | The list includes pull requests, as GitHub's does |
| `GET` and `PATCH /repos/{o}/{r}/issues/{n}` | A pull request read this way carries `pull_request`, which grind refuses |
| `GET` and `POST /repos/{o}/{r}/issues/{n}/comments` | |
| `GET /repos/{o}/{r}/releases/latest` | The newest published, non-prerelease release |

Every REST response body is validated in the tests against [GitHub's OpenAPI description](https://github.com/github/rest-api-description) (`tests/github/openapi_subset.json`, made by `utils/trim_github_openapi.py`).

## Search syntax

Shared by GraphQL `search` and REST `/search/issues`:

- `org:`, `user:`, `repo:`. Several are OR'd together, including a mix of kinds. No consumer mixes them, and the conformance check is where GitHub's behaviour for a mix would be confirmed.
- `is:pr` / `type:pr`, `is:issue` / `type:issue`
- `is:open`, `is:closed`, `state:open|closed`, `is:merged`, and `is:unmerged`, which is open or closed without merging, as GitHub documents it
- `draft:true|false`, `archived:true|false`
- `created:` and `updated:` with `A..B`, `>=A`, `>A`, `<=A`, `<A` or a single date. Each side is a date or an ISO time, and `*` leaves it open.
- `author:`, `assignee:` (`@me` is the caller), `label:`
- `sort:created-asc|created-desc|updated-asc|updated-desc|updated|created`
- Any other word, or "quoted phrase", must appear in the title or body.

Results are newest first by default.

## Rate limits

Each account has three buckets, as on GitHub: `core` (REST), `search` (REST `/search/*`) and `graphql`. The account is the identity a token maps to, so two tokens for one user share its buckets, as a user's personal tokens do on GitHub. Map tokens to different identities to give them separate limits. A bucket's window starts with its first request. Every response carries `X-RateLimit-Limit`, `-Remaining`, `-Reset`, `-Used` and `-Resource` for the bucket it spent from.

- **Defaults:** 5000 an hour for `core` and `graphql`, 30 a minute for `search`.
- **Running out:** REST answers `403` with `API rate limit exceeded for user ID N.` GraphQL answers `200` with a `RATE_LIMITED` error.
- **Cost:** every GraphQL query costs one point. GitHub charges more for big connections, but the consumers read `remaining` and `resetAt`, not `cost`.
- **Tests:** set a small `limit` in the seed to exhaust a bucket quickly. For a secondary limit or a `Retry-After`, use a fault rule instead.

## Faults

Every core fault action works. fake-github adds:

- **Matching on GraphQL:** the request's root fields (`graphql_fields`), aliases (`graphql_aliases`) and operation name (`graphql_operation`) are backend details. A rule can match them, and the journal records them:

  ```json
  {"match": {"graphql_fields": "search"},
   "action": {"type": "hook", "name": "graphql_error", "error_type": "RESOURCE_LIMITS_EXCEEDED"}}
  ```
- **The `graphql_error` hook** answers `200` with `{"data": null, "errors": [{"type": ..., "message": ...}]}`. It takes `error_type` (for example `RATE_LIMITED`, `RESOURCE_LIMITS_EXCEEDED` or `NOT_FOUND`) and an optional `message`.

## Seeds

A seed is YAML or JSON. Besides `tokens`, it has these keys:

```yaml
tokens: {token-alice: alice}
orgs:
  - login: acme
users:
  - login: alice
    name: Alice
    contributions:              # for gh-snitch
      - {date: 2026-02-01, kind: commit, count: 5}
      # kind: commit, pull_request, pull_request_review, issue, repository, restricted
repos:
  - owner: acme               # an org, or a user (created if not listed)
    name: widgets
    archived: false
    default_branch: main
    protection:
      main: {required_approving_review_count: 2}
    pulls:
      - number: 1
        title: Add sprockets
        author: alice
        state: open           # open, closed or merged
        draft: false
        labels: [enhancement]
        assignees: [alice]
        requested_reviewers: [bob]
        head: feature         # head_sha defaults to a stable fake SHA
        created_at: 2026-03-01T10:00:00Z
        review_decision: APPROVED
        reviews:
          - {author: bob, state: APPROVED}
        check_runs:
          - {name: test, conclusion: success}
        statuses:
          - {context: ci/legacy, state: success}
    issues:                   # numbers share one sequence with pulls
      - number: 2
        title: Sprockets squeak
        labels: [bug]
        comments:
          - {author: alice, body: Confirmed.}
    releases:
      - tag: v1.0.0
        assets:
          - {name: widgets, content: "#!/bin/sh\n"}
generate:                     # bulk pull requests
  - {repo: acme/widgets, pulls: 301, start: 2026-01-01T00:00:00Z, step_minutes: 60}
rate_limits:
  search: {limit: 5, window_seconds: 60}
```

- **Defaults:** anything a seed leaves out gets a fixed value. Timestamps default to `2026-01-01T00:00:00Z`, and IDs come out in seed order, so two runs answer identically.
- **Errors:** a malformed seed is refused with a `400` that names the field, for example `repos[0].pulls[2].state`.
- **Ready-made seed:** `breakfast-fixtures` copies the 8 PRs of the frozen [`mrsixw/breakfast-fixtures`](https://github.com/mrsixw/breakfast-fixtures) repository. Load it with `POST /_fake/fixtures/breakfast-fixtures`, or `fake_fixture("github", fixture="breakfast-fixtures", ...)`. It accepts `token-mrsixw`.

## Known differences from GitHub

- **Search:** only lexical, the subset above. GitHub's best-match ranking becomes newest first.
- **Pull request comments:** comments on a pull request's conversation are not modelled. Its `comments` count comes from the seed.
- **Unauthenticated calls:** only the two release calls work without a token. GitHub also serves public data anonymously, at 60 requests an hour.
- **GraphQL cost:** a flat one point per query (see Rate limits).

The nightly conformance check ([issue 5](https://github.com/mrsixw/fake-backends/issues/5)) compares responses with real GitHub.
