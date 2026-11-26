"""The pytest plugin, run the way a consumer runs it: in its own pytest session."""

import textwrap

from tests.conftest import REPO_ROOT

CONFTEST = textwrap.dedent(f"""
    from fake_backends.testing import fake_fixture

    fake_core = fake_fixture(
        "core",
        mode="process",
        source={str(REPO_ROOT)!r},
        seed={{"tokens": {{"t-alice": "alice"}}}},
    )
    renamed = fake_fixture(
        "core", mode="process", source={str(REPO_ROOT)!r}, name="other_core"
    )
    """)


def test_each_test_gets_a_reset_seeded_fake(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile("""
        import urllib.request

        def _call(fake):
            request = urllib.request.Request(
                fake.url("/x"), headers={"Authorization": "Bearer t-alice"}
            )
            try:
                urllib.request.urlopen(request)
            except OSError:  # a 401, or the dropped connection
                pass

        def test_first(fake_core):
            fake_core.add_fault({"match": {}, "action": {"type": "drop"}})
            _call(fake_core)
            assert fake_core.requests()[0]["identity"] == "alice"

        def test_second_sees_no_leftovers(fake_core):
            assert fake_core.faults() == []
            assert fake_core.requests() == []

        def test_seed_reapplied_after_reset(fake_core):
            _call(fake_core)
            assert fake_core.requests()[0]["identity"] == "alice"

        def test_one_server_per_session(fake_core):
            assert fake_core.base_url == fake_core.base_url

        def test_named_fixture(other_core):
            assert other_core.healthy()
        """)
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=5)
    result.stdout.no_fnmatch_line("*fake-backends: core logs*")
    # Loading the plugin before conftest.py imports it keeps this away.
    result.stdout.no_fnmatch_line("*PytestAssertRewriteWarning*")


def test_logs_are_shown_when_a_test_fails(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile("""
        def test_fails(fake_core):
            assert False
        """)
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*fake-backends: core logs*"])


def test_a_fake_that_cannot_start_errors_the_test(pytester):
    pytester.makeconftest(textwrap.dedent(f"""
            from fake_backends.testing import fake_fixture
            broken = fake_fixture("nope", mode="process", source={str(REPO_ROOT)!r})
            """))
    pytester.makepyfile("def test_x(broken):\n    pass\n")
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(
        [
            "*FakeStartError: fake 'nope' exited during startup",
            "*unknown backend 'nope'*",
        ]
    )


def test_a_fixture_loads_a_fakes_ready_made_seed(pytester):
    pytester.makeconftest(textwrap.dedent(f"""
            from fake_backends.testing import fake_fixture
            fake_github = fake_fixture(
                "github",
                mode="process",
                source={str(REPO_ROOT)!r},
                fixture="breakfast-fixtures",
            )
            """))
    pytester.makepyfile("""
        import json
        import urllib.request

        def test_fixture_prs(fake_github):
            assert fake_github.fixtures() == ["breakfast-fixtures"]
            request = urllib.request.Request(
                fake_github.url("/repos/mrsixw/breakfast-fixtures/pulls/8"),
                headers={"Authorization": "Bearer token-mrsixw"},
            )
            with urllib.request.urlopen(request) as response:
                assert json.load(response)["merged"] is True
        """)
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_seed_and_fixture_together_are_refused():
    import pytest

    from fake_backends.testing import fake_fixture

    with pytest.raises(ValueError, match="not both"):
        fake_fixture("github", seed={}, fixture="breakfast-fixtures")
