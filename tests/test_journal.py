import time

import requests

from tests.conftest import IDENTITY


def test_journal_records_each_request_in_order(base_url, auth, journal):
    before = time.time()
    requests.get(f"{base_url}/echo?word=one&field=a&field=b", headers=auth)
    requests.post(f"{base_url}/echo/two", headers=auth, data="payload")

    first, second = journal()

    assert first["method"] == "GET"
    assert first["path"] == "/echo"
    assert first["query"] == {"word": ["one"], "field": ["a", "b"]}
    assert first["identity"] == IDENTITY
    assert first["details"] == {"word": "one", "fields": ["a", "b"]}
    assert first["fault"] is None
    assert first["timestamp"] >= before
    assert second["method"] == "POST"
    assert second["path"] == "/echo/two"
    assert second["timestamp"] >= first["timestamp"]


def test_journal_records_rejected_and_faulted_requests(base_url, auth, add_fault):
    rule = add_fault(
        {"match": {"path": "/echo"}, "action": {"type": "respond", "status": 503}}
    )
    requests.get(f"{base_url}/public")
    requests.get(f"{base_url}/echo/x")
    requests.get(f"{base_url}/echo", headers=auth)

    entries = requests.get(f"{base_url}/_fake/requests").json()

    assert [e["identity"] for e in entries] == [None, None, IDENTITY]
    assert [e["fault"] for e in entries] == [None, None, rule]


def test_handler_still_receives_the_body_after_journaling(base_url, auth):
    response = requests.post(f"{base_url}/echo", headers=auth, data="kept")
    assert response.json()["body"] == "kept"
