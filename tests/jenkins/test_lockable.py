"""Lockable Resources, as jeeves' lockable.py calls it and as the real plugin
(1576.v217259b_f9976) was observed to answer, per jeeves' AGENTS.md.
"""

import pytest

from tests.conftest import IDENTITY
from tests.jenkins.conftest import Jenkins, basic, job_path

ROOT = "lockable-resources"
SEED = {
    "users": [
        {"id": IDENTITY, "admin": False},
        {"id": "bob", "admin": False},
        {"id": "root", "admin": True},
    ],
    "jobs": [
        {
            "name": "holder",
            "type": "pipeline",
            "locks": ["held"],
            "run_log": ["a\n", "b\n"],
        }
    ],
    "lockable_resources": {
        "resources": [
            {"name": "free", "description": "Reserve me.", "labels": "pool linux"},
            {"name": "spare", "labels": ["pool"]},
            {"name": "theirs", "reserved_by": "bob"},
            {"name": "held"},
        ]
    },
}


@pytest.fixture(autouse=True)
def seeded(seed):
    seed(SEED)


def _resources(jenkins):
    return {r["name"]: r for r in jenkins.json(ROOT)["resources"]}


def _status(jenkins, endpoint, **data):
    """The status jeeves sees: the first response, before following the redirect."""
    response = jenkins.session.post(
        f"{jenkins.base}/{ROOT}/{endpoint}", data=data, allow_redirects=False
    )
    return response.status_code


FOUND = 302


def test_the_register(jenkins):
    free = _resources(jenkins)["free"]
    assert free["labels"] == "pool linux"
    assert free["labelsAsList"] == ["pool", "linux"]
    assert (free["free"], free["reserved"], free["locked"]) == (True, False, False)
    assert _resources(jenkins)["theirs"]["reservedBy"] == "bob"


def test_reserve_and_unreserve(jenkins):
    assert _status(jenkins, "reserve", resource="free", reason="testing") == FOUND
    mine = _resources(jenkins)["free"]
    assert (mine["reservedBy"], mine["note"], mine["free"]) == (
        IDENTITY,
        "testing",
        False,
    )
    assert mine["reservedTimestamp"]
    assert _status(jenkins, "unreserve", resource="free") == FOUND
    assert _resources(jenkins)["free"]["reservedBy"] is None


def test_mutators_forward_to_the_previous_page(jenkins):
    response = jenkins.session.post(
        f"{jenkins.base}/{ROOT}/reserve",
        data={"resource": "free"},
        headers={"Referer": f"{jenkins.base}/{ROOT}/"},
        allow_redirects=True,
    )
    assert response.status_code == 200
    assert response.history[0].headers["Location"] == f"{jenkins.base}/{ROOT}/"


def test_reserving_a_resource_that_is_not_free_is_423(jenkins):
    assert _status(jenkins, "reserve", resource="theirs") == 423
    _status(jenkins, "reserve", resource="free")
    assert _status(jenkins, "reserve", resource="free") == 423  # even our own


def test_unreserve_answers_200_when_nobody_holds_it(jenkins):
    """Observed on 1576.v217259b_f9976: no 403, just the usual redirect."""
    assert _status(jenkins, "unreserve", resource="spare") == FOUND


def test_unreserving_someone_elses_reservation_is_403(jenkins):
    assert _status(jenkins, "unreserve", resource="theirs") == 403


def test_an_administrator_may_unreserve_anyones(base_url, seed):
    seed({**SEED, "tokens": {"root-token": "root"}})
    admin = Jenkins(base_url, basic("root", "root-token"))
    assert _status(admin, "unreserve", resource="theirs") == FOUND
    assert _resources(admin)["theirs"]["reservedBy"] is None


@pytest.mark.parametrize("endpoint", ["unlock", "reset"])
def test_unlock_and_reset_answer_200_with_nothing_to_do(jenkins, endpoint):
    assert _status(jenkins, endpoint, resource="spare") == FOUND


def test_reset_clears_a_reservation(jenkins):
    assert _status(jenkins, "reset", resource="theirs") == FOUND
    assert _resources(jenkins)["theirs"]["free"] is True


@pytest.mark.parametrize(
    "endpoint", ["reserve", "unreserve", "unlock", "reset", "deleteResource"]
)
def test_an_unknown_resource_is_404(jenkins, endpoint):
    assert _status(jenkins, endpoint, resource="ghost") == 404


def test_create_and_delete(jenkins):
    assert (
        _status(jenkins, "createResource", name="new", description="d", labels="a b")
        == FOUND
    )
    assert _resources(jenkins)["new"]["labelsAsList"] == ["a", "b"]
    assert _status(jenkins, "createResource", name="new") == 409
    assert _status(jenkins, "deleteResource", resource="new") == FOUND
    assert "new" not in _resources(jenkins)


def test_deleting_a_resource_that_is_not_free_is_423(jenkins):
    assert _status(jenkins, "deleteResource", resource="theirs") == 423


def test_mutators_need_post(jenkins):
    response = jenkins.session.get(
        f"{jenkins.base}/{ROOT}/reserve", params={"resource": "free"}
    )
    assert response.status_code == 405


def test_a_running_build_holds_its_lock(jenkins):
    response = jenkins.post(f"{job_path('holder')}/build")
    number = jenkins.json(
        "queue/item/" + response.headers["Location"].rstrip("/").rsplit("/", 1)[1]
    )["executable"]["number"]
    held = _resources(jenkins)["held"]
    assert (held["locked"], held["free"], held["reserved"]) == (True, False, False)
    assert held["buildName"] == f"holder #{number}"
    assert "is locking held" in held["lockCause"]
    while jenkins.json(f"{job_path('holder')}/{number}")["building"]:
        pass
    assert _resources(jenkins)["held"]["free"] is True


# --- the plugin switches ------------------------------------------------


@pytest.mark.parametrize(
    "endpoint",
    ["reserve", "unreserve", "unlock", "reset", "createResource", "deleteResource"],
)
def test_with_the_plugin_absent_every_path_is_404(jenkins, seed, endpoint):
    seed({**SEED, "lockable_resources": {"plugin": "absent"}})
    assert jenkins.get(ROOT).status_code == 404
    assert _status(jenkins, endpoint, resource="free", name="x") == 404


def test_with_an_old_plugin_only_create_and_delete_are_missing(jenkins, seed):
    seed(
        {**SEED, "lockable_resources": {**SEED["lockable_resources"], "plugin": "old"}}
    )
    assert jenkins.get(ROOT).status_code == 200
    assert _status(jenkins, "createResource", name="x") == 404
    assert _status(jenkins, "deleteResource", resource="spare") == 404
    assert _status(jenkins, "reserve", resource="free") == FOUND


def test_the_404_ambiguity_jeeves_resolves(jenkins, seed):
    """A 404 from reserve means either an unknown resource or no plugin;
    only the register tells them apart, as jeeves' _interpret_404 does."""
    unknown = _status(jenkins, "reserve", resource="ghost")
    seed({**SEED, "lockable_resources": {"plugin": "absent"}})
    absent = _status(jenkins, "reserve", resource="free")
    assert unknown == absent == 404
    assert jenkins.get(ROOT).status_code == 404


def test_a_build_waits_for_a_lock_someone_else_holds(jenkins):
    """The lock step blocks while the resource is reserved, then takes it."""
    assert _status(jenkins, "reserve", resource="held") == FOUND
    response = jenkins.post(f"{job_path('holder')}/build")
    item = response.headers["Location"].rstrip("/").rsplit("/", 1)[1]
    number = jenkins.json(f"queue/item/{item}")["executable"]["number"]
    path = f"{job_path('holder')}/{number}"
    for _ in range(3):
        assert jenkins.json(path)["building"] is True
    assert (
        "Trying to acquire lock on [held]" in jenkins.text(f"{path}/consoleText").text
    )
    assert _resources(jenkins)["held"]["locked"] is False

    assert _status(jenkins, "unreserve", resource="held") == FOUND
    jenkins.json(path)
    assert _resources(jenkins)["held"]["locked"] is True
    while jenkins.json(path)["building"]:
        pass
    assert _resources(jenkins)["held"]["free"] is True


def test_users_are_not_administrators_unless_the_seed_says_so(base_url, seed):
    seed({**SEED, "tokens": {"stranger-token": "stranger"}})
    stranger = Jenkins(base_url, basic("stranger", "stranger-token"))
    assert _status(stranger, "unreserve", resource="theirs") == 403
