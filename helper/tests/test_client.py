"""The control-API client, against a real core fake run from this checkout."""

import urllib.error
import urllib.request
from http import HTTPStatus

import pytest

from fake_backends.testing import FakeClient, FakeControlError

TOKEN = "t-alice"
IDENTITY = "alice"


def _get(client: FakeClient, path: str, token: str | None = None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(client.url(path), headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_healthy(core):
    assert core.healthy()


def test_an_unreachable_fake_is_not_healthy():
    assert not FakeClient("http://127.0.0.1:9").healthy()


def test_url_joins_paths(core):
    assert core.url("/a/b") == f"{core.base_url}/a/b"
    assert core.url("a") == f"{core.base_url}/a"


def test_seeded_tokens_identify_the_caller(core):
    core.seed({"tokens": {TOKEN: IDENTITY}})
    _get(core, "/anything", TOKEN)
    assert core.requests()[-1]["identity"] == IDENTITY


def test_faults_are_added_listed_and_removed(core):
    rule = core.add_fault(
        {"match": {"path": "/x"}, "action": {"type": "respond", "status": 503}}
    )
    assert _get(core, "/x") == HTTPStatus.SERVICE_UNAVAILABLE
    [listed] = core.faults()
    assert (listed["id"], listed["fired"]) == (rule, 1)

    core.remove_fault(rule)
    assert core.faults() == []


def test_clear_faults(core):
    core.add_fault({"match": {}, "action": {"type": "respond", "status": 503}})
    core.clear_faults()
    assert core.faults() == []


def test_the_journal_records_requests_in_order(core):
    _get(core, "/first")
    _get(core, "/second")
    assert [entry["path"] for entry in core.requests()] == ["/first", "/second"]


def test_reset_clears_state_faults_and_journal(core):
    core.seed({"tokens": {TOKEN: IDENTITY}})
    core.add_fault({"match": {}, "action": {"type": "respond", "status": 503}})
    _get(core, "/x", TOKEN)
    core.reset()
    assert core.faults() == []
    assert core.requests() == []
    _get(core, "/x", TOKEN)
    assert core.requests()[-1]["identity"] is None


def test_a_refused_call_raises_with_the_reason(core):
    with pytest.raises(FakeControlError) as caught:
        core.add_fault({"match": {}, "action": {"type": "explode"}})
    assert caught.value.status == HTTPStatus.BAD_REQUEST
    assert "must be one of" in caught.value.body
