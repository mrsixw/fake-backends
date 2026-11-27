# fake-jenkins

The Jenkins API [jeeves](https://github.com/mrsixw/jeeves) calls, and the Lockable Resources plugin, served from a seed. Built in [issue 4](https://github.com/mrsixw/fake-backends/issues/4).

## Endpoints

| Endpoint | Notes |
|---|---|
| `GET /api/json` | The root: `mode`, `nodeDescription`, `numExecutors`, `jobs` |
| `GET /job/a/job/b/api/json` | A job or folder, at any nesting depth |
| `GET /job/.../{n or permalink}/api/json` | A build. The permalinks are `lastBuild`, `lastSuccessfulBuild`, `lastFailedBuild`, `lastCompletedBuild`, `lastStableBuild`, `lastUnstableBuild` and `lastUnsuccessfulBuild`. A permalink with no build is a 404. |
| `GET /job/.../{n}/consoleText` | The log so far |
| `GET /job/.../{n}/logText/progressiveText?start=` | The log from `start`, with `X-Text-Size`, and `X-More-Data: true` while the build runs |
| `POST /job/.../build` and `/buildWithParameters` | A form body (or query string). Answers `201` with `Location: /queue/item/N/`. Missing parameters take their defaults. |
| `POST /job/.../{n}/stop` | Aborts a running build, then redirects to the build page |
| `GET /queue/api/json`, `GET /queue/item/{id}/api/json` | A waiting item has `why`. Once it starts it becomes a `LeftItem` with `executable.number`. |
| `GET /computer/api/json` | Nodes. `depth=1` adds `monitorData` (disk, temp, swap, response time, clock, architecture). |
| `GET /computer/{name}/config.xml` | An agent's config, with the SSH launcher's `<host>`. The built-in node has none (404). |
| `GET /me/api/json` | The token's user |
| `GET /crumbIssuer/api/json` | `crumb` and `crumbRequestField` (`Jenkins-Crumb`); a 404 when crumbs are off |

**`tree` and `depth`** behave as on Jenkins.
- **`tree`:** keeps only the named fields, nested with `[]`, and slices arrays with `{M,N}`, `{N}`, `{M,}` or `{,N}`. `_class` is always kept.
- **`depth`:** shows nested objects in full only while depth remains. Below that they are summaries (`_class`, `name` or `number`, `url`).

**Builds:** they carry `actions` (with `ParametersAction` and `CauseAction`), `culprits`, and their changes. Changes are in `changeSets[]` on pipelines and a single `changeSet` on freestyle jobs, as on Jenkins.

## The build lifecycle

Builds move on when they are looked at, not as time passes, so a test sees the same sequence every run.

1. A trigger queues an item. It waits for the job's `queue_polls` reads of `queue/item/{id}` (0 means it starts at once).
2. The build then runs. Each read of its progressive log or its JSON emits the next chunk of the job's `run_log`. In `run_log`, `${NAME}` is replaced with the build's parameter, as a shell `echo` would print it.
3. After the last chunk the build finishes with the job's `run_result`.
4. A job's `locks` are held while its build runs. A locked resource shows `locked`, `buildName` and `lockCause`. If any of the job's resources is reserved or locked by something else, the build starts but waits inside the lock step, logging `Trying to acquire lock on [...]`. It emits nothing more until every resource is free; it then takes them all and runs on.

## Security

- **Logins:** callers log in with HTTP Basic `user:api-token`. The token decides who the caller is; the username part is not checked.
- **A wrong token:** `401`.
- **No credential:** `403` (`X-You-Are-Authenticated-As: anonymous`). With `security.login_redirect`, it is instead a redirect to `securityRealm/commenceLogin`, as SSO and reverse-proxy deployments do. That page answers `200`.
- **Crumbs:** on by default. API-token logins are exempt from them, as they are on Jenkins since 2.96. Set `security.api_tokens_exempt: false` to make every POST need a valid crumb (`403` without one), or `security.crumbs: false` to turn crumbs off and remove the issuer.

## Lockable Resources

`/lockable-resources/` follows [jeeves' AGENTS.md](https://github.com/mrsixw/jeeves/blob/main/AGENTS.md), and what plugin `1576.v217259b_f9976` was observed to do.

| Endpoint | Answer |
|---|---|
| `GET api/json` | `resources[]`: `name`, `description`, `labels` (space-separated) and `labelsAsList`, `note`, `reserved`, `reservedBy`, `reservedByEmail`, `reservedTimestamp`, `locked`, `lockCause`, `buildName`, `free`, `ephemeral`, `stolen` |
| `POST reserve` (`resource`, optional `reason`) | `423` if the resource is not free, even for its own holder |
| `POST unreserve` | `200` whether or not anyone held it. `403` only for someone else's reservation, unless the caller is an administrator. |
| `POST unlock`, `POST reset` | `200` whether or not there was anything to do |
| `POST createResource` (`name`, `description`, `labels`) | `409` if it exists |
| `POST deleteResource` | `423` if the resource is not free |

- **Responses:** every mutator is POST-only (`405` otherwise). A successful one redirects to the previous page (`Referer`, or the plugin's page), so only its status code means anything.
- **Unknown resources:** a 404 from every mutator.
- **`lockable_resources.plugin`** switches the plugin:
  - `absent`: every `/lockable-resources` path is a 404. This is the ambiguity jeeves resolves by reading `api/json`.
  - `old`: `createResource` and `deleteResource` are 404, while everything else works, like an older plugin release.

## Seeds

```yaml
tokens: {my-api-token: alice}
jenkins: {description: "My Jenkins", executors: 2}
security: {crumbs: true, api_tokens_exempt: true, login_redirect: false}
users:
  - {id: alice, full_name: Alice Example, email: alice@example.com, admin: true}  # admin defaults to false
nodes:
  - {name: agent-1, host: 10.0.0.5, labels: [linux], executors: 2}
  - {name: agent-2, offline: true, offline_reason: maintenance}
jobs:
  - name: app
    type: pipeline              # freestyle (the default), pipeline, folder or matrix
    description: The app
    parameters:
      - {name: GREETING, default: hello}
      - {name: FAST, type: boolean, default: false}
      - {name: ENV, type: choice, choices: [dev, prod]}
    queue_polls: 2              # reads of the queue item before it starts
    run_log: ["Started\n", "greeting=${GREETING}\n", "Finished: SUCCESS\n"]
    run_result: SUCCESS         # SUCCESS, UNSTABLE, FAILURE, ABORTED or NOT_BUILT
    locks: [shared-db]          # held while a build runs
    builds:                     # history
      - number: 1
        result: FAILURE
        timestamp: 2026-03-01T10:00:00Z
        started_by: bob
        parameters: {GREETING: hi}
        changes: [{author: bob, msg: Break it, commit: <40 hex digits>}]
        log: "the console text\n"
  - name: team
    type: folder
    jobs: [{name: nested}]
lockable_resources:
  plugin: current               # current, old or absent
  resources:
    - {name: shared-db, description: The test database, labels: "db linux"}
    - {name: device, reserved_by: bob}
```

- **Defaults:** anything left out gets a fixed default, so every run of a seed behaves the same. A bad seed is refused with a `400` that names the field.
- **Ready-made seed:** `jeeves-fixtures` copies the Jenkins jeeves' end-to-end suite builds in a container, with the same user, jobs, folder and lockable resources. Load it with `POST /_fake/fixtures/jeeves-fixtures` or `fake_fixture("jenkins", fixture="jeeves-fixtures", ...)`. It accepts the API token `jeeves-e2e-token`.

## Known differences from Jenkins

- **No executors:** builds run when polled rather than on an executor. A queue item waits for its poll count, not for a free executor.
- **Permissions:** only `admin`, which lets a user release someone else's reservation, is modelled. It is false unless the seed sets it. Every authenticated user may do everything else.
- **Pages:** HTML pages are placeholders, there only so that redirects land somewhere.
- **Not modelled:** artifacts, test reports, views, pipeline stages and the Blue Ocean API.

The nightly conformance check ([issue 5](https://github.com/mrsixw/fake-backends/issues/5)) compares responses with the real Jenkins image jeeves pins.
