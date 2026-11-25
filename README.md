# fake-backends 🎭

Container-hosted fake **GitHub**, **JIRA** and **Jenkins** APIs with fault injection, for end-to-end tests.

Each fake serves the API calls a real tool makes. It can be seeded with data, told to fail on purpose, and asked which requests it received. End-to-end suites can then drive a real built binary through both its happy paths and its failure paths: rate limits, 5xx responses, timeouts, dropped connections and malformed bodies.

> **Status:** nothing is built yet. The work is tracked in this repo's issues.

## Images (planned)

| Image | Serves | Issue |
|---|---|---|
| `fake-github` | REST and GraphQL | [issue 2](https://github.com/mrsixw/fake-backends/issues/2) |
| `fake-jira` | Cloud v3 and Server v2 modes | [issue 3](https://github.com/mrsixw/fake-backends/issues/3) |
| `fake-jenkins` | Jenkins core, plus Lockable Resources | [issue 4](https://github.com/mrsixw/fake-backends/issues/4) |

All three are built on a shared core ([issue 1](https://github.com/mrsixw/fake-backends/issues/1)):
- a control API under `/_fake/` for seed state, fault rules and the request journal;
- multi-arch images published to GHCR;
- the `fake_backends.testing` pytest helper.

A nightly conformance check ([issue 5](https://github.com/mrsixw/fake-backends/issues/5)) compares the fakes with real GitHub and Jenkins.

## Consumers

- [breakfast](https://github.com/mrsixw/breakfast): [issue 536](https://github.com/mrsixw/breakfast/issues/536)
- [gh-snitch](https://github.com/mrsixw/gh-snitch): [issue 211](https://github.com/mrsixw/gh-snitch/issues/211)
- [jeeves](https://github.com/mrsixw/jeeves): [issue 167](https://github.com/mrsixw/jeeves/issues/167)
- [grind](https://github.com/mrsixw/grind): [issue 27](https://github.com/mrsixw/grind/issues/27)

## Licence

[MIT](LICENSE)
