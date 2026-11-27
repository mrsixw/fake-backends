# fake-backends 🎭

Container-hosted fake **GitHub**, **JIRA** and **Jenkins** APIs with fault injection, for end-to-end tests.

Each fake serves the API calls a real tool makes. It can be seeded with data, told to fail on purpose, and asked which requests it received. End-to-end suites can then drive a real built binary through both its happy paths and its failure paths: rate limits, 5xx responses, timeouts, dropped connections and malformed bodies.

> **Status:** the shared server core, the image pipeline, the test helper and all three fakes are built. The nightly conformance check is tracked in [issue 5](https://github.com/mrsixw/fake-backends/issues/5).

## Images

| Image | Serves | Issue |
|---|---|---|
| `fake-github` | REST and GraphQL: [docs/github.md](docs/github.md) | [issue 2](https://github.com/mrsixw/fake-backends/issues/2) |
| `fake-jira` | Cloud v3 and Server v2 modes: [docs/jira.md](docs/jira.md) | [issue 3](https://github.com/mrsixw/fake-backends/issues/3) |
| `fake-jenkins` | Jenkins core, plus Lockable Resources: [docs/jenkins.md](docs/jenkins.md) | [issue 4](https://github.com/mrsixw/fake-backends/issues/4) |

All three are built on a shared core ([issue 1](https://github.com/mrsixw/fake-backends/issues/1)):
- a control API under `/_fake/` for seed state, fault rules and the request journal;
- multi-arch images published to GHCR;
- the `fake_backends.testing` pytest helper, documented below.

A nightly conformance check ([issue 5](https://github.com/mrsixw/fake-backends/issues/5)) compares the fakes with real GitHub and Jenkins. JIRA has no free, stable real instance to compare against, so fake-jira's fidelity check is Atlassian's published OpenAPI specs instead: every response its tests see is validated against them ([docs/jira.md](docs/jira.md#fidelity)).

## Control API

Every fake serves these endpoints. Requests to them are never faulted, journaled or authenticated.

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Returns `{"status": "ok"}` once the fake is up |
| `PUT /_fake/state` | Load seed data as JSON, or as YAML with `Content-Type: application/yaml`. `tokens` maps each accepted token to an identity; every other key belongs to the backend. Replaces the previous state, or changes nothing if the backend refuses the seed (`400`, naming the problem). |
| `POST /_fake/reset` | Clear the state, the fault rules and the journal |
| `GET /_fake/fixtures` | The ready-made seeds this fake ships |
| `POST /_fake/fixtures/{name}` | Load one, replacing the state |
| `POST /_fake/faults` | Register a fault rule. Returns `201` with its `id`. |
| `GET /_fake/faults` | List the rules with their `matched` and `fired` counts and whether each is `spent` |
| `DELETE /_fake/faults` | Remove every rule |
| `DELETE /_fake/faults/{id}` | Remove one rule |
| `GET /_fake/requests` | The journal: method, path, query, identity, backend details, the rule that fired, and a timestamp, in arrival order |

### Fault rules

```json
{
  "match": {"method": "GET", "path": "/repos/*", "identity": "alice"},
  "fire": {"on": "nth", "n": 2, "cap": 1},
  "action": {"type": "respond", "status": 403,
             "headers": {"X-RateLimit-Remaining": "0"},
             "body": {"message": "API rate limit exceeded"}}
}
```

- **`match`**
  - `method`: case-insensitive.
  - `path`: an `fnmatch` glob.
  - `identity`: the identity the caller's token maps to.
  - Any other key is a backend detail. It must equal the detail's value, or be one of its members when the detail is a list, such as GraphQL root fields.
  - An empty `match` matches everything.
- **`fire`**
  - `on`: `always` (the default), `nth` (only the nth matching request) or `after` (every matching request after the first `n`).
  - `cap`: limits how many times the rule fires. A spent rule stays listed.
- **Counting**
  - A rule counts only the requests it matches, from when it is registered.
  - When several rules match, every one of them counts the request, but only the earliest registered rule that fires answers it.
  - Counts reset only when the rule is removed or the fake is reset.
- **Faults fire before authentication.** A canned 503 is a 503 whatever credential was sent.

| Action | Effect |
|---|---|
| `respond` | Answer with `status`, plus optional `headers` and `body`. A string body is sent as text; anything else as JSON. |
| `delay` | Wait `seconds`, then answer normally |
| `drop` | Close the connection before sending anything |
| `truncate` | Send the real response's headers and only `keep_bytes` of its body (half by default), then close |
| `malformed_json` | Answer `200` with a body that is not valid JSON |
| `hook` | A backend-specific action, such as a GraphQL `errors[]` entry |

Any action also takes `delay_seconds`, which waits before acting.

## Testing helper

`fake-backends-testing` gives a consumer's test suite pytest fixtures that start a fake, reset it before every test, and drive it through the control API. It needs Python 3.11 or later, and imports nothing from the servers.

### Install

Pin it to the same release as the images, as a test dependency:

```toml
[dependency-groups]
e2e = [
    "fake-backends-testing @ git+https://github.com/mrsixw/fake-backends@v0.2.0#subdirectory=helper",
]
```

pytest loads the plugin automatically once it is installed.

### Use

Declare each fake in `conftest.py`. The name you assign is the fixture's name:

```python
from fake_backends.testing import fake_fixture

fake_github = fake_fixture(
    "github",
    image="ghcr.io/mrsixw/fake-github:0.2.0@sha256:...",
    seed={"tokens": {"token-alice": "alice"}},
)
```

```python
def test_rate_limited(fake_github, run_cli):
    fake_github.add_fault({
        "match": {"path": "/user"},
        "action": {"type": "respond", "status": 403,
                   "headers": {"X-RateLimit-Remaining": "0"}},
    })
    result = run_cli("--github-url", fake_github.url())
    assert [r["path"] for r in fake_github.requests()] == ["/user"]
```

- **Lifetime:** each fake starts once per session and is reset before every test, then reseeded if `seed` was given, or loaded with one of its ready-made seeds if `fixture` was (for example `fixture="breakfast-fixtures"`). At the end of the session it is stopped and removed. If any test failed, its logs are printed first.
- **Configuring a fake:** `env=` sets its environment, for example `fake_fixture("jira", ..., env={"FAKE_JIRA_MODE": "server"})`.
- **The client** (`FakeClient`) has `seed()`, `load_fixture()`, `fixtures()`, `reset()`, `add_fault()`, `faults()`, `remove_fault()`, `clear_faults()`, `requests()`, `healthy()` and `url()`. A refused call raises `FakeControlError` with the fake's reason.
- **Without pytest:** `start_fake(backend, image=...)` returns a `RunningFake` with a `client`, `logs()` and `stop()`.

### Container runtimes

Container mode, the default, uses docker if it answers, otherwise Apple's `container`. Docker comes first so that local runs match CI. A runtime that is installed but not running gives a `RuntimeUnavailable` naming the fix (`is Docker running?`, `run container system start`), rather than a timeout. On Macs where Apple's published ports don't forward, the helper reaches the container on its own address instead.

### Process mode

To work on a fake and a consumer side by side, run the server from a fake-backends checkout instead of an image:

```bash
FAKE_BACKENDS_MODE=process FAKE_BACKENDS_SOURCE=~/git_repos/fake-backends make e2e
```

This needs `uv`. The fixture runs `uv run python -m fake_backends.server` from that checkout.

## Development

```bash
make format   # black, then ruff --fix
make lint     # ruff, black --check, shellcheck and actionlint
make test     # pytest against a real uvicorn server
make bats     # the release scripts, with the pinned git-mkver
make helper-test       # the test helper on Python 3.11 (HELPER_PYTHON=3.14 for the newest)
make helper-container  # the helper against a real core image
```

### Building and running an image

One `Dockerfile` builds every fake. `BACKEND` picks which one, from the registry in `src/fake_backends/server/registry.py`:

```bash
make image BACKEND=core                    # docker build, tagged fake-core:dev
make smoke BACKEND=core                    # build, then utils/smoke_test_image.sh
make smoke BACKEND=core RUNTIME=container  # Apple's container instead of docker
```

The image listens on port 8080, runs as an unprivileged user and has a `HEALTHCHECK` on `/healthz`. `core` serves only the control API: it exists so the image can be built and tested before any fake does, and is never published.

## Releases

Every merge to `main` releases. git-mkver computes the version from the tags and the Conventional Commit messages since the last one, and every pull request's `version` check shows what its merge would release.

A release pushes each published image to `ghcr.io/mrsixw/<image>:<version>` for `linux/amd64` and `linux/arm64`, then creates the tag and a GitHub release listing each image's digest. Pin images by that digest. The first release of a new image stops until its GHCR package is made public, a one-time switch in the package's settings. Nothing is pushed outside a release, and no version commit is pushed to `main`: see [ADR 0005](docs/design/adr/0005-release-from-tags.md).

## Consumers

- [breakfast](https://github.com/mrsixw/breakfast): [issue 536](https://github.com/mrsixw/breakfast/issues/536)
- [gh-snitch](https://github.com/mrsixw/gh-snitch): [issue 211](https://github.com/mrsixw/gh-snitch/issues/211)
- [jeeves](https://github.com/mrsixw/jeeves): [issue 167](https://github.com/mrsixw/jeeves/issues/167)
- [grind](https://github.com/mrsixw/grind): [issue 27](https://github.com/mrsixw/grind/issues/27)

## Licence

[MIT](LICENSE)
