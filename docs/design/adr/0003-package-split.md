# ADR 0003: Server and test helper are separate packages on different Pythons

- **Status:** Accepted
- **Date:** 2026-11-26
- **Issues:** [issue 6](https://github.com/mrsixw/fake-backends/issues/6), [issue 8](https://github.com/mrsixw/fake-backends/issues/8)

## Context

This repo ships two kinds of code that run in very different places:

- **The servers** run only inside the fake images. Their Python version is ours to choose.
- **The `fake_backends.testing` helper** (pytest fixtures, an HTTP client for the control API, the container runtime) installs into each consumer's test environment: breakfast, gh-snitch, jeeves and grind. Its Python version is set by the oldest of them.

One package would force the servers down to the consumers' oldest Python, or force every consumer up to the servers' newest.

## Decision

- `fake_backends` is a **namespace package**: there is no `src/fake_backends/__init__.py`. `fake_backends.server` and `fake_backends.testing` are distributed separately, the helper as `fake-backends-testing`. The helper lives in `helper/`, a separate uv project with its own lock file, because one lock cannot resolve for both Python floors.
- **The servers require Python 3.14** (`requires-python = ">=3.14"`), moving to 3.15 once it is final.
- **The helper requires Python 3.11** until the consumers move up.
- **The helper never imports server code.** It talks to a fake only over HTTP, the same way a consumer's binary does. CI runs its tests under 3.11 and 3.14, and `helper/tests/test_no_server_imports.py` fails on any import of `fake_backends.server`, naming the line.

## Consequences

- Adding an `__init__.py` under `src/fake_backends/` would break the split: the two distributions would collide. Don't.
- Anything both sides need, such as the control API's paths, is defined on each side or published as data, never shared as an import from `server`.
- Server code may use 3.14 features freely; helper code may not use anything newer than 3.11.
- The root project's ruff and black exclude `helper/`, which is linted and formatted with its own 3.11 config. Formatting it with the root config rewrote `except (A, B):` into 3.14's unparenthesised form, which 3.11 cannot parse.
- The helper takes its version from the repo's `v*` tags (hatch-vcs), so it and the images share one release number.
