"""The conformance comparator, which decides what counts as the fake drifting."""

import pytest

from conformance.compare import Rules, Sample, compare, shape
from conformance.probes import GITHUB_PROBES, JENKINS_PROBES
from conformance.run import RULES_FILE, rules_for


def _sample(body, status=200, headers=()):
    return Sample(status, frozenset(h.lower() for h in headers), body)


def _problems(real, fake, rules=None, headers=()):
    return [
        str(d)
        for d in compare(_sample(**real), _sample(**fake), rules or Rules(), headers)
    ]


def test_shape_lists_every_path_and_type():
    assert shape({"a": 1, "b": [{"c": None}, {"c": "x"}], "d": {"e": True}}) == {
        "(body)": {"object"},
        "a": {"number"},
        "b": {"array"},
        "b[].c": {"null", "string"},
        "b[]": {"object"},
        "d": {"object"},
        "d.e": {"boolean"},
    }


def test_identical_bodies_match():
    body = {"a": [1, 2], "b": {"c": "x"}}
    assert _problems({"body": body}, {"body": body}) == []


def test_a_field_missing_from_the_fake_is_reported_once():
    problems = _problems({"body": {"a": {"b": 1, "c": 2}}}, {"body": {}})
    assert problems == ["`a`: missing from the fake"]


def test_a_field_only_the_fake_has_is_reported():
    assert _problems({"body": {}}, {"body": {"x": 1}}) == [
        "`x`: the fake has it; the real service does not"
    ]


def test_a_type_change_is_reported_null_included():
    assert _problems({"body": {"a": "x"}}, {"body": {"a": None}}) == [
        "`a`: real string, fake null"
    ]


def test_status_and_header_presence_are_compared():
    problems = _problems(
        {"body": {}, "status": 200, "headers": ["X-RateLimit-Used"]},
        {"body": {}, "status": 404},
        headers=["X-RateLimit-Used"],
    )
    assert problems == [
        "`(status)`: real 200, fake 404",
        "`(header) X-RateLimit-Used`: real only",
    ]


def test_not_modelled_fields_may_be_missing():
    rules = Rules(not_modelled=["**.topics"])
    assert (
        _problems({"body": {"repo": {"topics": ["x"]}}}, {"body": {"repo": {}}}, rules)
        == []
    )


def test_volatile_fields_skip_the_type_check():
    rules = Rules(volatile=["name"])
    assert _problems({"body": {"name": "Steve"}}, {"body": {"name": None}}, rules) == []


def test_values_are_compared_only_where_named():
    rules = Rules(values=["items[].title"])
    real = {"body": {"items": [{"title": "A", "id": 1}]}}
    fake = {"body": {"items": [{"title": "B", "id": 2}]}}
    assert _problems(real, fake, rules) == ["`items[].title`: real ['A'], fake ['B']"]


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("**.created_at", "created_at", True),
        ("**.created_at", "items[].user.created_at", True),
        ("*.url", "user.url", True),
        ("*.url", "a.user.url", False),
        ("head.repo", "head.repo.topics[]", True),
        ("head.repo", "head.repository", False),
    ],
)
def test_patterns(pattern, path, matches):
    assert Rules(not_modelled=[pattern]).ignored_absence(path) is matches


def test_the_rules_file_names_only_real_probes():
    import yaml

    document = yaml.safe_load(RULES_FILE.read_text())
    for service, probes in (("github", GITHUB_PROBES), ("jenkins", JENKINS_PROBES)):
        names = {p.name for p in probes}
        assert set(document[service]["probes"] or {}) <= names
        for probe in probes:
            rules_for(service, probe)


def test_a_text_body_turning_into_json_is_caught():
    assert _problems({"body": "log text"}, {"body": {"log": "text"}}) == [
        "`log`: the fake has it; the real service does not",
        "`(body)`: real string, fake object",
    ]


def test_value_lists_are_compared_in_order():
    rules = Rules(values=["jobs[].name"])
    real = {"body": {"jobs": [{"name": "a"}, {"name": "b"}]}}
    fake = {"body": {"jobs": [{"name": "b"}, {"name": "a"}]}}
    assert _problems(real, fake, rules) == [
        "`jobs[].name`: real ['a', 'b'], fake ['b', 'a']"
    ]
