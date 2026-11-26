# fake-jira

The JIRA REST calls [grind](https://github.com/mrsixw/grind) makes, served from a seed, in either of JIRA's two API modes. Built in [issue 3](https://github.com/mrsixw/fake-backends/issues/3).

## Modes

`FAKE_JIRA_MODE` picks the mode when the fake starts. One image serves either.

| | `cloud` (the default) | `server` (Server and Data Center) |
|---|---|---|
| API | `/rest/api/3` | `/rest/api/2` |
| Search | `GET /search/jql`, paged with `nextPageToken` and `isLast` | `GET /search`, paged with `startAt`, `maxResults` and `total` |
| Bodies (descriptions, comments) | Atlassian Document Format, stored and returned as sent | Plain strings |
| Users | `accountId` | `name` and `key` |
| User search | `GET /user/search?query=` | Not served (404) |
| Auth | HTTP Basic, `email-or-name:api-token` | Basic, or a Bearer personal access token |

From the helper, set the mode with `env`:

```python
jira = fake_fixture(
    "jira", image="ghcr.io/mrsixw/fake-jira:...", env={"FAKE_JIRA_MODE": "server"}
)
```

The token decides who the caller is; the username part of Basic is not checked. A missing or unknown credential gets `401` in JIRA's error shape.

## Endpoints

Both modes, under the mode's prefix:

| Endpoint | Notes |
|---|---|
| `GET /myself` | |
| `GET /issue/{key}?fields=...` | `fields` is a comma list. `*all`, `*navigable` and an absent `fields` mean every field the fake serves. `comment` includes the comments. |
| `POST /issue` | `project.key`, `summary` and `issuetype.name` are required. `description`, `labels` and `assignee` are optional. Each bad field is named in `errors`. |
| `PUT /issue/{key}` | `fields.summary`, `fields.description`, `fields.labels`, and `update.labels` with `add`, `remove` or `set` |
| `POST /issue/{key}/comment` | |
| `PUT /issue/{key}/assignee` | `{"accountId": ...}` in Cloud, `{"name": ...}` in Server. A null value unassigns. |
| `GET` and `POST /issue/{key}/transitions` | Only the transitions the issue's current status allows |

- **Body format:** a body of the wrong kind for the mode (text in Cloud, a document in Server) is refused with `400`, as JIRA refuses it.
- **Search:** with no `fields`, Cloud's `search/jql` returns only issue ids, as JIRA Cloud does. Server returns every field.
- **Unbounded searches:** Cloud refuses one with JIRA's message.

## JQL

Only what grind sends, plus a few neighbours:

- `project = X`, `project != X`, `project in (X, Y)`
- `assignee` and `reporter` with `= currentUser()`, `= name`, `is EMPTY` or `is not EMPTY`
- `statusCategory` and `status` with `=`, `!=` or `in (...)`. Categories match by name (`Done`) or key (`done`).
- `issuetype` (or `type`), `labels` and `priority` with `=` or `in (...)`, and `key = X`
- `text ~ "words"` (summary, description and comments) and `summary ~ "words"`
- `AND` and parentheses
- `ORDER BY updated|created|key|priority|status [ASC|DESC], ...`

Anything else is a `400` in JIRA's shape: `OR`, `NOT`, functions other than `currentUser()`, relative dates, or an unknown field (`Field 'x' does not exist or you do not have permission to view it.`). A query that a new version of grind sends then fails loudly instead of matching nothing.

## Seeds

```yaml
tokens: {token-alice: alice}
users:
  - name: alice                 # the identity tokens map to; the Server username
    display_name: Alice Example
    email: alice@example.com
    # account_id: defaults to a stable id derived from the name
projects:
  - key: PROJ
    name: Project
    # The defaults:
    # statuses: [{name: To Do, category: new},
    #            {name: In Progress, category: indeterminate},
    #            {name: Done, category: done}]
    # transitions: one global transition into each status
    # issue_types: [Task, Bug, Story, Epic]
  - key: OPS
    statuses:
      - {name: Open, category: new}
      - {name: Closed, category: done}
    transitions:
      - {id: "5", name: Close, to: Closed, from: [Open]}   # omit from for a global one
issues:
  - key: PROJ-1
    summary: Coffee machine leaks
    description: "It drips.\n\nEvery morning."  # text, or an ADF document
    type: Bug
    status: To Do
    assignee: alice
    reporter: bob             # people not listed in users are created
    priority: High            # Highest, High, Medium, Low or Lowest
    labels: [kitchen]
    created: 2026-03-01T09:00:00Z
    updated: 2026-03-04T09:00:00Z
    comments:
      - {author: bob, body: Still leaking.}
```

- **Bodies:** a seed may write text or ADF in either mode. Each body is converted once to the mode's own form.
- **Errors:** a bad seed is refused with a `400` naming the field.

## Fidelity

There is no free, stable real JIRA to compare against, so **Atlassian's published OpenAPI specs are fake-jira's fidelity check**. Every response in the tests is validated against them. That covers each issue field too, against its own component, since both specs leave an issue's `fields` free-form.

- **Cloud:** [the Jira Cloud platform v3 spec](https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json).
- **Server:** [the Jira Software Data Center 11.3 spec](https://dac-static.atlassian.com/server/jira/platform/jira_software_dc_11003_swagger.v3.json), which includes the platform REST API.

The specs are trimmed into `tests/jira/openapi_*.json` by `utils/trim_openapi.py`, which records one correction. The Data Center spec declares every issue field an object, which contradicts its own example. Neither spec marks properties as required, so they check types and, in Cloud, unknown properties, but not completeness.

## Known differences from JIRA

- **Workflows:** a project's workflow is its statuses and transitions only. There are no screens, conditions or validators.
- **Fields:** custom fields, components, versions, sprints and attachments are not modelled.
- **`text ~`:** matches every word as a substring. JIRA's stemming and Lucene syntax are not reproduced.
- **Ids:** ids, account ids and timestamps are stable fakes: every run of a seed produces the same ones.
