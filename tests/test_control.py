import requests

from tests.conftest import IDENTITY, TOKEN
from tests.echo_backend import FIXTURE


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


# --- backend state and fixtures ------------------------------------------


def test_a_seed_the_backend_rejects_changes_nothing(base_url, auth):
    requests.put(f"{base_url}/_fake/state", json={"greeting": "hi"})
    response = requests.put(f"{base_url}/_fake/state", json={"greeting": 7})
    assert response.status_code == 400
    assert "greeting must be a string" in response.json()["error"]
    # Neither the tokens nor the data moved: the first seed had no tokens.
    assert requests.get(f"{base_url}/echo", headers=auth).status_code == 401


def test_fixtures_are_listed(base_url):
    assert requests.get(f"{base_url}/_fake/fixtures").json() == [FIXTURE]


def test_loading_a_fixture_replaces_the_state(base_url):
    response = requests.post(f"{base_url}/_fake/fixtures/{FIXTURE}")
    assert response.status_code == 204
    echoed = requests.get(
        f"{base_url}/echo", headers={"Authorization": "Bearer token-fixture"}
    ).json()
    assert echoed["identity"] == "fixture-user"
    assert echoed["greeting"] == "hello from a fixture"


def test_an_unknown_fixture_names_the_known_ones(base_url):
    response = requests.post(f"{base_url}/_fake/fixtures/nope")
    assert response.status_code == 404
    assert FIXTURE in response.json()["error"]


def test_reset_returns_the_backend_to_its_empty_state(base_url):
    requests.post(f"{base_url}/_fake/fixtures/{FIXTURE}")
    requests.post(f"{base_url}/_fake/reset")
    requests.put(f"{base_url}/_fake/state", json={"tokens": {"t": "u"}})
    echoed = requests.get(f"{base_url}/echo", headers={"Authorization": "Bearer t"})
    assert echoed.json()["greeting"] is None
