# ADR 0005: Release from tags, without pushing a version commit

- **Status:** Accepted
- **Date:** 2026-11-26
- **Issue:** [issue 7](https://github.com/mrsixw/fake-backends/issues/7)

## Context

The CLIs (breakfast and the others) release on every merge to `main` with git-mkver. Their release job commits a `chore: bump version` to `main`, a protected branch, which only works because it checks out with an admin PAT (`secrets.GH_TOKEN`) that the ruleset's admin bypass lets through. The [terraform-github README](https://github.com/mrsixw/terraform-github#bypass-actors) records the cost: the PAT expires every 90 days, and the way out is an open decision. One route it lists, "removing the need to push at all", is "the only route that gives up no protection anywhere".

fake-backends has no version file to keep current. Its products are images, and an image's version is its tag.

## Decision

- **The tag is the only record of a version.** git-mkver computes the next version from the last `v*` tag and the Conventional Commit messages since it (`utils/next_version.sh`). Nothing is written back to `main`.
- **No PAT.** The workflow's own `GITHUB_TOKEN` pushes images (`packages: write`) and creates the tag and release (`contents: write`). The ruleset protects only the default branch, so creating a tag needs no bypass.
- **Images first, release last.** For a merge to `main`:
  1. `plan` computes the version, and stands down if `HEAD` already carries a `v*` tag (a re-run of a finished release).
  2. `publish` builds and pushes each published image for `linux/amd64` and `linux/arm64`, as `ghcr.io/mrsixw/<image>:<version>`.
  3. `release` checks that each image can be pulled without logging in, then creates the tag and GitHub release, listing each image's digest.

  A release therefore exists only once its images do. If a push fails, there is no tag, and a re-run computes the same version and pushes again.
- **One release at a time.** Pushes to `main` share one concurrency group, so two quick merges cannot compute the same version. A newer queued run replaces an older queued one, and its release includes the older run's commits.
- **Every pull request reports the version its merge would release**, with a warning for a major bump, as the CLIs do.
- **git-mkver is pinned** to one version, checked against a SHA-256, so a commit always runs the same release logic. `make bats` tests the release scripts with that same version.

## Consequences

- **Each new image needs a one-time switch to public.** A new GHCR package can start out private, and GitHub has no API to change that. The release stops before announcing anything and prints the package's settings link; make it public there and re-run the job.
- Releasing needs no `GH_TOKEN`. The conformance check uses it instead, only to read public GitHub data, because `GET /user` refuses a workflow's own token. So fake-backends stays in terraform-github's `gh_token_secret_repos`.
- `pyproject.toml` keeps `version = "0.0.0"`. The server is never published to PyPI. The test helper takes its version from the same tags through hatch-vcs, and consumers install it from a tag.
- A release cut while no backend is marked `published` in the registry would have no images, so the workflow skips the release entirely until the first fake ships.
- A failed release after the images are pushed leaves images without a release. They are tagged with a version that has no git tag yet, and the re-run overwrites them with the same version.
