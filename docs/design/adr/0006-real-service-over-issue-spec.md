# ADR 0006: Where an issue and the real service disagree, the fake follows the service

- **Status:** Accepted
- **Date:** 2026-11-27
- **Issues:** [issue 2](https://github.com/mrsixw/fake-backends/issues/2), [issue 3](https://github.com/mrsixw/fake-backends/issues/3), [issue 4](https://github.com/mrsixw/fake-backends/issues/4)

## Context

Each fake was specified by an issue, written before the fake was built and mostly from memory of the real API. While building them, three places came up where the issue's wording and the real service differ:

- **GitHub rate limits.** [Issue 2](https://github.com/mrsixw/fake-backends/issues/2) asked for "separate buckets per token". GitHub limits the user a token authenticates as: a user's personal tokens share one allowance.
- **Jenkins crumbs.** [Issue 4](https://github.com/mrsixw/fake-backends/issues/4) said "every POST requires a valid crumb when crumbs are on". Since Jenkins 2.96, requests authenticated with an API token are exempt from CSRF crumbs, and API tokens are how jeeves authenticates.
- **The Jira Server spec.** [Issue 3](https://github.com/mrsixw/fake-backends/issues/3) asked for validation against Atlassian's published OpenAPI specs. Atlassian publishes no platform-only Server or Data Center spec; the nearest is the Jira Software Data Center 11.3 spec, which includes the platform REST API. That spec also declares every issue field an object, which its own example contradicts.

A fake exists so that a consumer's tests mean something about the real service. A fake that matches its issue but not the service makes those tests pass for code that would fail in production. In the crumb case, a fake that demands a crumb would push jeeves into fetching crumbs it does not need, and could hide a real bug in its crumb handling, which the real Jenkins never exercises for token logins.

## Decision

When sources disagree about how a fake should behave, the fake follows them in this order:

1. **The real service, observed.** A response from the service itself, such as a probe of the nightly conformance check ([issue 5](https://github.com/mrsixw/fake-backends/issues/5)) or a request made by hand.
2. **The vendor's reference documentation** for that behaviour.
3. **The vendor's published API spec.** Where a spec contradicts its own examples or the service, the trimmer records the correction in code, with its reason, rather than editing the spec by hand.
4. **The issue that specified the fake.**

So:

- fake-github keeps rate-limit buckets per account. Two tokens mapped to one identity share them; tokens mapped to different identities get separate limits ([docs/github.md](../../github.md)).
- fake-jenkins exempts API-token logins from crumbs by default. The seed's `security.api_tokens_exempt: false` makes every POST need one, for testing a client against a Jenkins configured that way ([docs/jenkins.md](../../jenkins.md)).
- fake-jira's Server mode is validated against the Jira Software Data Center 11.3 spec, with the issue-field correction recorded in `utils/trim_openapi.py` ([docs/jira.md](../../jira.md)).

Every departure from an issue is stated in that fake's PR and in its doc under `docs/`, with the real behaviour it follows. An issue's acceptance criteria are met in substance: the criterion is what the issue was trying to make true of the fake, read against the real service.

## Consequences

- A consumer test that passes against a fake is evidence about the real service, for the behaviour that test exercises and the fake models. That is the reason these fakes exist.
- Issues are no longer the final word on behaviour. A reviewer checking a PR against its issue needs the PR's list of departures, so each PR carries one.
- Where the real behaviour is configurable, as with crumbs, the fake defaults to the real default and offers the other setting in its seed. Tests of a non-default deployment stay possible.
- The order makes a disagreement between the service and its own documentation a question with an answer: observed behaviour wins, and the conformance check keeps checking it. That check's rules file is the one place a known difference between a fake and the service is excused, each with a reason.
