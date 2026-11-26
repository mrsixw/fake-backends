import requests

from tests.conftest import IDENTITY, TOKEN


def test_healthz_answers(base_url):
    response = requests.get(f"{base_url}/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_json_seed_is_served(base_url, auth):
    requests.put(
        f"{base_url}/_fake/state",
        json={"tokens": {TOKEN: IDENTITY}, "greeting": "hello"},
    ).raise_for_status()
    assert requests.get(f"{base_url}/echo", headers=auth).json()["greeting"] == "hello"


def test_yaml_seed_is_served(base_url, auth):
    body = f"tokens:\n  {TOKEN}: {IDENTITY}\ngreeting: bonjour\n"
    response = requests.put(
        f"{base_url}/_fake/state",
        data=body,
        headers={"Content-Type": "application/yaml"},
    )
    assert response.status_code == 204
    assert requests.get(f"{base_url}/echo", headers=auth).json()["greeting"] == (
        "bonjour"
    )


def test_seed_replaces_previous_state(base_url, auth):
    requests.put(
        f"{base_url}/_fake/state",
        json={"tokens": {TOKEN: IDENTITY}, "greeting": "hello"},
    )
    requests.put(f"{base_url}/_fake/state", json={"tokens": {TOKEN: IDENTITY}})
    assert requests.get(f"{base_url}/echo", headers=auth).json()["greeting"] is None


def test_unparseable_seed_is_rejected(base_url):
    response = requests.put(
        f"{base_url}/_fake/state",
        data="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert "seed" in response.json()["error"]


def test_seed_must_be_a_mapping(base_url):
    response = requests.put(f"{base_url}/_fake/state", json=["a", "list"])
    assert response.status_code == 400


def test_tokens_must_map_strings_to_strings(base_url):
    response = requests.put(f"{base_url}/_fake/state", json={"tokens": {"t": 1}})
    assert response.status_code == 400


def test_reset_clears_state_faults_and_journal(base_url, auth, add_fault, journal):
    add_fault({"match": {"path": "/never"}, "action": {"type": "drop"}})
    requests.get(f"{base_url}/echo", headers=auth)

    requests.post(f"{base_url}/_fake/reset").raise_for_status()

    assert requests.get(f"{base_url}/_fake/faults").json() == []
    assert journal() == []
    assert requests.get(f"{base_url}/echo", headers=auth).status_code == 401


def test_control_paths_are_never_faulted_journaled_or_authenticated(
    base_url, add_fault, journal
):
    add_fault({"match": {"path": "*"}, "action": {"type": "drop"}})

    assert requests.get(f"{base_url}/healthz").status_code == 200
    assert requests.get(f"{base_url}/_fake/faults").status_code == 200
    assert journal() == []
