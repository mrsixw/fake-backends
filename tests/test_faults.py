import time

import pytest
import requests

from tests.conftest import IDENTITY
from tests.echo_backend import TEAPOT_HOOK

SHORT_DELAY = 0.3
CLIENT_TIMEOUT = 0.1
# Long enough for a cut to arrive; short enough that a hang fails fast.
STREAM_TIMEOUT = 2


def _get(base_url, auth, path="/echo"):
    return requests.get(f"{base_url}{path}", headers=auth)


def _faults(base_url):
    return {
        rule["id"]: rule for rule in requests.get(f"{base_url}/_fake/faults").json()
    }


# --- actions -------------------------------------------------------------


def test_respond_sends_status_headers_and_json_body(base_url, auth, add_fault):
    add_fault(
        {
            "match": {"path": "/echo"},
            "action": {
                "type": "respond",
                "status": 403,
                "headers": {"X-RateLimit-Remaining": "0"},
                "body": {"message": "API rate limit exceeded"},
            },
        }
    )
    response = _get(base_url, auth)
    assert response.status_code == 403
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.json() == {"message": "API rate limit exceeded"}


def test_respond_sends_a_text_body_as_is(base_url, auth, add_fault):
    add_fault(
        {"match": {}, "action": {"type": "respond", "status": 502, "body": "oops"}}
    )
    response = _get(base_url, auth)
    assert response.status_code == 502
    assert response.text == "oops"


def test_delay_then_passes_through_to_the_real_handler(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "delay", "seconds": SHORT_DELAY}})
    started = time.monotonic()
    response = _get(base_url, auth)
    assert time.monotonic() - started >= SHORT_DELAY
    assert response.json()["identity"] == IDENTITY


def test_delay_trips_a_client_timeout(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "delay", "seconds": SHORT_DELAY}})
    with pytest.raises(requests.exceptions.ReadTimeout):
        requests.get(f"{base_url}/echo", headers=auth, timeout=CLIENT_TIMEOUT)


def test_any_action_can_be_delayed(base_url, auth, add_fault):
    add_fault(
        {
            "match": {},
            "action": {"type": "respond", "status": 500, "delay_seconds": SHORT_DELAY},
        }
    )
    started = time.monotonic()
    assert _get(base_url, auth).status_code == 500
    assert time.monotonic() - started >= SHORT_DELAY


def test_drop_closes_the_connection_before_any_response(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "drop"}})
    with pytest.raises(requests.exceptions.ConnectionError):
        _get(base_url, auth)


def test_truncate_cuts_the_real_body_off(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "truncate"}})
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        _get(base_url, auth)


def test_truncate_keeps_the_requested_bytes(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "truncate", "keep_bytes": 5}})
    response = requests.get(f"{base_url}/echo", headers=auth, stream=True)
    assert response.status_code == 200
    received = b""
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        for chunk in response.iter_content(chunk_size=1):
            received += chunk
    assert received == b'{"ide'


def test_truncate_cuts_an_endless_stream_without_buffering_it(
    base_url, auth, add_fault
):
    add_fault(
        {"match": {"path": "/endless"}, "action": {"type": "truncate", "keep_bytes": 7}}
    )
    response = requests.get(
        f"{base_url}/endless", headers=auth, stream=True, timeout=STREAM_TIMEOUT
    )
    received = b""
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        for chunk in response.iter_content(chunk_size=1):
            received += chunk
    assert received == b"tick\nti"


def test_truncate_without_a_length_sends_headers_only(base_url, auth, add_fault):
    add_fault({"match": {"path": "/endless"}, "action": {"type": "truncate"}})
    response = requests.get(
        f"{base_url}/endless", headers=auth, stream=True, timeout=STREAM_TIMEOUT
    )
    assert response.status_code == 200
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        b"".join(response.iter_content(chunk_size=1))


def test_malformed_json_answers_200_with_a_broken_body(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "malformed_json"}})
    response = _get(base_url, auth)
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/json")
    with pytest.raises(requests.exceptions.JSONDecodeError):
        response.json()


def test_hook_action_is_answered_by_the_backend(base_url, auth, add_fault):
    add_fault(
        {
            "match": {},
            "action": {"type": "hook", "name": TEAPOT_HOOK, "payload": "short"},
        }
    )
    response = _get(base_url, auth)
    assert response.status_code == 418
    assert response.json() == {"hooked": "short", "path": "/echo"}


# --- matching ------------------------------------------------------------


def test_method_and_path_glob_must_both_match(base_url, auth, add_fault):
    add_fault(
        {
            "match": {"method": "post", "path": "/echo/*"},
            "action": {"type": "respond", "status": 500},
        }
    )
    assert _get(base_url, auth, "/echo/a").status_code == 200
    assert requests.post(f"{base_url}/echo", headers=auth).status_code == 200
    assert requests.post(f"{base_url}/echo/a", headers=auth).status_code == 500


def test_identity_match(base_url, auth, add_fault):
    add_fault(
        {"match": {"identity": "bob"}, "action": {"type": "respond", "status": 500}}
    )
    assert _get(base_url, auth).status_code == 200


def test_backend_detail_match_by_value_and_by_membership(base_url, auth, add_fault):
    add_fault(
        {
            "match": {"word": "boom"},
            "action": {"type": "respond", "status": 500},
        }
    )
    add_fault({"match": {"fields": "b"}, "action": {"type": "respond", "status": 502}})
    assert _get(base_url, auth, "/echo?word=calm").status_code == 200
    assert _get(base_url, auth, "/echo?word=boom").status_code == 500
    assert _get(base_url, auth, "/echo?field=a").status_code == 200
    assert _get(base_url, auth, "/echo?field=a&field=b").status_code == 502


# --- firing and counting -------------------------------------------------


def test_fires_on_every_match_by_default(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "respond", "status": 500}})
    assert [_get(base_url, auth).status_code for _ in range(3)] == [500] * 3


def test_fires_on_the_nth_matching_request_only(base_url, auth, add_fault):
    add_fault(
        {
            "match": {"path": "/echo"},
            "fire": {"on": "nth", "n": 2},
            "action": {"type": "respond", "status": 500},
        }
    )
    codes = [_get(base_url, auth).status_code for _ in range(3)]
    assert codes == [200, 500, 200]


def test_fires_after_the_first_n_matching_requests(base_url, auth, add_fault):
    add_fault(
        {
            "match": {},
            "fire": {"on": "after", "n": 2},
            "action": {"type": "respond", "status": 500},
        }
    )
    codes = [_get(base_url, auth).status_code for _ in range(4)]
    assert codes == [200, 200, 500, 500]


def test_only_matching_requests_advance_the_count(base_url, auth, add_fault):
    add_fault(
        {
            "match": {"path": "/echo"},
            "fire": {"on": "nth", "n": 2},
            "action": {"type": "respond", "status": 500},
        }
    )
    assert _get(base_url, auth).status_code == 200
    assert _get(base_url, auth, "/public").status_code == 200
    assert _get(base_url, auth, "/echo/x").status_code == 200
    assert _get(base_url, auth).status_code == 500


def test_cap_spends_the_rule_but_keeps_it_registered(base_url, auth, add_fault):
    rule = add_fault(
        {"match": {}, "fire": {"cap": 2}, "action": {"type": "respond", "status": 500}}
    )
    codes = [_get(base_url, auth).status_code for _ in range(3)]
    assert codes == [500, 500, 200]
    state = _faults(base_url)[rule]
    assert state["matched"] == 3
    assert state["fired"] == 2
    assert state["spent"] is True


def test_earliest_rule_wins_but_every_matching_rule_counts(base_url, auth, add_fault):
    first = add_fault(
        {"match": {}, "fire": {"cap": 1}, "action": {"type": "respond", "status": 500}}
    )
    second = add_fault(
        {
            "match": {},
            "fire": {"on": "nth", "n": 2},
            "action": {"type": "respond", "status": 502},
        }
    )
    assert _get(base_url, auth).status_code == 500
    assert _get(base_url, auth).status_code == 502
    state = _faults(base_url)
    assert (state[first]["matched"], state[first]["fired"]) == (2, 1)
    assert (state[second]["matched"], state[second]["fired"]) == (2, 1)


def test_a_rule_that_loses_on_precedence_has_not_fired(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "respond", "status": 500}})
    loser = add_fault(
        {"match": {}, "fire": {"cap": 1}, "action": {"type": "respond", "status": 502}}
    )
    _get(base_url, auth)
    assert _faults(base_url)[loser]["fired"] == 0


def test_deleting_a_rule_removes_it(base_url, auth, add_fault):
    rule = add_fault({"match": {}, "action": {"type": "respond", "status": 500}})
    assert requests.delete(f"{base_url}/_fake/faults/{rule}").status_code == 204
    assert _get(base_url, auth).status_code == 200
    assert requests.delete(f"{base_url}/_fake/faults/{rule}").status_code == 404


def test_clearing_all_rules(base_url, auth, add_fault):
    add_fault({"match": {}, "action": {"type": "respond", "status": 500}})
    assert requests.delete(f"{base_url}/_fake/faults").status_code == 204
    assert _get(base_url, auth).status_code == 200


def test_faults_fire_before_authentication(base_url, add_fault):
    add_fault({"match": {}, "action": {"type": "respond", "status": 503}})
    assert requests.get(f"{base_url}/echo").status_code == 503


# --- validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        {"match": {}, "action": {"type": "explode"}},
        {"match": {}, "action": {}},
        {"match": {}},
        {"match": {}, "fire": {"on": "sometimes"}, "action": {"type": "drop"}},
        {"match": {}, "fire": {"on": "nth"}, "action": {"type": "drop"}},
        {"match": {}, "fire": {"on": "nth", "n": 0}, "action": {"type": "drop"}},
        {"match": {}, "fire": {"cap": 0}, "action": {"type": "drop"}},
        {"match": {}, "action": {"type": "respond"}},
        {"match": {}, "action": {"type": "respond", "status": 99}},
        {"match": {}, "action": {"type": "delay"}},
        {"match": {}, "action": {"type": "delay", "seconds": -1}},
        {"match": {}, "action": {"type": "truncate", "keep_bytes": -1}},
        {"match": {}, "action": {"type": "hook", "name": "unknown"}},
        {"match": "everything", "action": {"type": "drop"}},
    ],
)
def test_invalid_rules_are_rejected(base_url, rule):
    response = requests.post(f"{base_url}/_fake/faults", json=rule)
    assert response.status_code == 400
    assert response.json()["error"]
