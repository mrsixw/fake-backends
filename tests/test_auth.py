import pytest
import requests

from tests.conftest import IDENTITY, TOKEN


def test_missing_credential_gets_401(base_url):
    response = requests.get(f"{base_url}/echo")
    assert response.status_code == 401
    assert response.json() == {"message": "Bad credentials"}


def test_unknown_token_gets_401(base_url):
    response = requests.get(
        f"{base_url}/echo", headers={"Authorization": "Bearer not-a-token"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "token"])
def test_known_token_is_identified(base_url, scheme):
    response = requests.get(
        f"{base_url}/echo", headers={"Authorization": f"{scheme} {TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json()["identity"] == IDENTITY


def test_public_route_needs_no_credential(base_url):
    response = requests.get(f"{base_url}/public")
    assert response.status_code == 200
    assert response.json() == {"identity": None}


def test_public_route_still_identifies_a_known_token(base_url, auth):
    assert requests.get(f"{base_url}/public", headers=auth).json() == {
        "identity": IDENTITY
    }
