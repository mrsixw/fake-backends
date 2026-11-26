# ADR 0002: Faults fire before auth, and every matching rule counts

- **Status:** Accepted
- **Date:** 2026-11-26
- **Issue:** [issue 6](https://github.com/mrsixw/fake-backends/issues/6)

## Context

A fault rule says which requests it matches, when it fires (`always`, the `nth` match, or `after` the first `n`), and optionally a `cap`. Two questions needed a definite answer, because a scenario's expected outcome depends on them and an ambiguous rule makes tests flaky or misleading:

1. Where faults sit relative to authentication.
2. How counting works when several rules match the same request.

## Decision

**Faults fire before authentication.** A rule is decided for every request, whatever credential it carries, and the auth check runs only if the request reaches the real handler. A canned 503 is a 503 even for a missing or bad token.

**Counting:**

- A rule counts only the requests it matches, from the moment it is registered. Requests it does not match never advance it.
- Every matching rule counts the request (`matched`), even when another rule answers it.
- Of the rules that would fire, the earliest registered one answers. Only that rule's `fired` count goes up.
- A rule that reaches its `cap` is spent: it keeps counting matches, never fires again, and stays listed in `GET /_fake/faults` so a test can inspect it.
- Counts reset only when the rule is deleted or the fake is reset.

## Why

- **Before auth:** outages on real services hit every caller alike. A 503 or a dropped connection arrives before any credential is checked, and tools must handle it the same way whether or not their token is good. Putting faults after auth would also make it impossible to fault an unauthenticated call, such as a public release lookup.
- **Per-rule counting:** "fail the 2nd call to `/search`" must mean the 2nd call to `/search`, not the 2nd request of any kind. Counting every matching rule, rather than only the winner, keeps each rule's `nth` independent of which other rules happen to be registered, so rules can be layered without renumbering them.
- **Earliest wins:** registration order is visible in the test that sets the rules up, so precedence is readable at the call site.
- **Spent rules stay:** a test can assert that a fault actually fired the expected number of times.

## Consequences

- A test that wants a 401 must not also register a matching `respond`, `drop`, `malformed_json` or `hook` rule, because those answer (or close) without reaching the auth check.
- `delay` and `truncate` pass through to the auth check, so an unauthenticated request with a `delay` rule still gets its 401, late, and a `truncate` rule cuts the 401 body.
- Covered by `test_faults_fire_before_authentication`, `test_only_matching_requests_advance_the_count`, `test_earliest_rule_wins_but_every_matching_rule_counts`, `test_a_rule_that_loses_on_precedence_has_not_fired` and `test_cap_spends_the_rule_but_keeps_it_registered`. Counting only the winner was checked to fail the precedence test.
