# Agent Instructions

## Project Overview
- **fake-backends** publishes container images that fake the GitHub, JIRA and Jenkins APIs for end-to-end tests. Each fake can be seeded, can have faults injected, and records the requests it receives.
- Consumers: [breakfast](https://github.com/mrsixw/breakfast), [gh-snitch](https://github.com/mrsixw/gh-snitch), [jeeves](https://github.com/mrsixw/jeeves) and [grind](https://github.com/mrsixw/grind).
- **Design docs:** [docs/design/architecture.md](docs/design/architecture.md) explains the server core, and [docs/design/adr/](docs/design/adr/) records why its less obvious choices were made. Read the ADRs before changing the connection abort, fault ordering or counting, or the package layout. Record a new decision of that weight as a new ADR in the same PR.
- Work not yet built is designed in this repo's issues: [issue 1](https://github.com/mrsixw/fake-backends/issues/1) (core), [issue 2](https://github.com/mrsixw/fake-backends/issues/2) (fake-github), [issue 3](https://github.com/mrsixw/fake-backends/issues/3) (fake-jira), [issue 4](https://github.com/mrsixw/fake-backends/issues/4) (fake-jenkins) and [issue 5](https://github.com/mrsixw/fake-backends/issues/5) (the conformance check).

## Environment
- **The servers:** Python >= 3.14, using **uv** (not pip). They run only inside their containers.
- **The `fake_backends.testing` helper:** Python >= 3.11, because it installs into the consumers' test environments.
  - It must never import server code.
  - It lives in `helper/`, its own uv project and lock file. Lint and format it with `make lint` and `make format`, which run its own 3.11 config; never run the root's black or ruff on it.
  - CI tests it under 3.11 and 3.14 (`make helper-test`).

## Fidelity is the product
- A fake is only worth having while it answers the way the real service does. Model every response on the real API's documented shape, and on observed behaviour where the two differ.
- When the fake and a real service disagree, the real service is right. Fix the fake, and add the case to the conformance check.
- Never add a behaviour to a fake that no consumer needs. Each endpoint should trace back to a call a consumer actually makes.

## Work Items
- **A GitHub issue must exist before any work begins.** Every branch, commit and PR references one.
- One issue = one branch = one PR. Branches are named `issue-<N>_<short_description>`.
- PR titles start with `Issue N:`. PR bodies include `Closes #N`.
- **Agents never merge PRs** and never force-push.
- `main` is protected. Changes arrive through PRs.

## Commit Messages
- Use Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `ci:`).
- **A `!` after the type, or the breaking-change footer anywhere in a body, releases a new MAJOR version.** mkver scans every line for these markers as substrings, so a body that merely mentions one counts as using it. Break the string when you need to write about them.

## Code Quality
- No magic numbers: status codes, timeouts and limits become named constants.
- Never use bare `except Exception`. Catch specific types.
- Every module declares `__all__`, and a leading `_` means module-private.

## Agent Instruction Files
`AGENTS.md` is the single source of truth. `CLAUDE.md`, `GEMINI.md` and `.github/copilot-instructions.md` are symlinks to it.
