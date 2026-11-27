"""The Jenkins API jeeves calls, request by request (src/jeeves/jenkins.py)."""

import re

import pytest
import requests

from tests.conftest import IDENTITY, TOKEN
from tests.jenkins.conftest import Jenkins, basic, job_path

# jeeves jenkins.py builds() and changes()
BUILDS_TREE = (
    "builds[number,result,timestamp,duration,url,building,"
    "actions[parameters[name,value],"
    "causes[shortDescription,userId,upstreamProject,upstreamBuild]]]"
    "{{0,{limit}}}"
)
CHANGE_ITEMS = "items[commitId,msg,timestamp,authorEmail,author[fullName,absoluteUrl]]"
CHANGES_TREE = (
    "number,url,culprits[fullName,absoluteUrl],"
    f"changeSets[{CHANGE_ITEMS}],changeSet[{CHANGE_ITEMS}]"
)
LOGIN_MARKER = "securityRealm/commenceLogin"

SEED = {
    "jenkins": {"description": "fake controller", "executors": 3},
    "users": [
        {"id": IDENTITY, "full_name": "Alice Example"},
        {"id": "bob", "full_name": "Bob"},
    ],
    "nodes": [
        {"name": "agent-1", "host": "10.0.0.5", "labels": ["linux"], "monitor": {}},
        {"name": "agent-2", "offline": True, "offline_reason": "maintenance"},
    ],
    "jobs": [
        {
            "name": "app",
            "type": "pipeline",
            "parameters": [
                {"name": "GREETING", "default": "hello"},
                {"name": "FAST", "type": "boolean", "default": False},
                {"name": "ENV", "type": "choice", "choices": ["dev", "prod"]},
            ],
            "queue_polls": 2,
            "run_log": ["Started\n", "greeting=${GREETING}\n", "Finished: SUCCESS\n"],
            "builds": [
                {
                    "number": 1,
                    "result": "SUCCESS",
                    "timestamp": "2026-03-01T10:00:00Z",
                    "log": "one\n",
                },
                {
                    "number": 2,
                    "result": "FAILURE",
                    "timestamp": "2026-03-02T10:00:00Z",
                    "started_by": "bob",
                    "parameters": {"GREETING": "hi"},
                    "changes": [
                        {"author": "bob", "msg": "Break it", "commit": "a" * 40}
                    ],
                },
            ],
        },
        {"name": "lint", "type": "freestyle", "run_result": "UNSTABLE"},
        {
            "name": "team",
            "type": "folder",
            "jobs": [
                {
                    "name": "nested",
                    "builds": [
                        {
                            "number": 7,
                            "result": "SUCCESS",
                            "changes": [{"author": "alice", "msg": "Fix"}],
                        }
                    ],
                }
            ],
        },
    ],
}


@pytest.fixture(autouse=True)
def seeded(seed):
    seed(SEED)


# --- reading -------------------------------------------------------------


def test_status(jenkins):
    root = jenkins.json("")
    assert (root["_class"], root["mode"], root["numExecutors"]) == (
        "hudson.model.Hudson",
        "NORMAL",
        3,
    )
    assert [j["name"] for j in root["jobs"]] == ["app", "lint", "team"]
    assert root["jobs"][0] == {
        "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob",
        "name": "app",
        "url": f"{jenkins.base}/job/app/",
        "color": "red",
    }


def test_jobs_in_a_folder_and_depth(jenkins):
    nested = jenkins.json(job_path("team"))["jobs"]
    assert nested == [
        {
            "_class": "hudson.model.FreeStyleProject",
            "name": "nested",
            "url": f"{jenkins.base}/job/team/job/nested/",
            "color": "blue",
        }
    ]
    deep = jenkins.json("", {"depth": 1})["jobs"]
    assert "builds" in deep[0] and "jobs" in deep[2]


def test_job_detail_carries_parameter_definitions(jenkins):
    job = jenkins.json(job_path("app"))
    [prop] = job["property"]
    assert "ParametersDefinitionProperty" in prop["_class"]
    kinds = {
        d["name"]: (d["_class"].rsplit(".", 1)[-1], d["defaultParameterValue"]["value"])
        for d in prop["parameterDefinitions"]
    }
    assert kinds == {
        "GREETING": ("StringParameterDefinition", "hello"),
        "FAST": ("BooleanParameterDefinition", False),
        "ENV": ("ChoiceParameterDefinition", "dev"),
    }
    assert prop["parameterDefinitions"][2]["choices"] == ["dev", "prod"]
    assert job["healthReport"][0]["score"] == 50


def test_builds_tree_with_range(jenkins):
    builds = jenkins.json(job_path("app"), {"tree": BUILDS_TREE.format(limit=1)})[
        "builds"
    ]
    assert len(builds) == 1
    [newest] = builds
    assert (newest["number"], newest["result"], newest["building"]) == (
        2,
        "FAILURE",
        False,
    )
    params = next(a for a in newest["actions"] if "parameters" in a)["parameters"]
    assert {"name": "GREETING", "value": "hi"} in [
        {k: p[k] for k in ("name", "value")} for p in params
    ]
    causes = next(a for a in newest["actions"] if "causes" in a)["causes"]
    assert causes[0]["userId"] == "bob"
    assert set(newest) == {
        "_class",
        "number",
        "result",
        "timestamp",
        "duration",
        "url",
        "building",
        "actions",
    }


@pytest.mark.parametrize(
    ("ref", "number"),
    [("lastBuild", 2), ("lastSuccessfulBuild", 1), ("lastFailedBuild", 2), ("1", 1)],
)
def test_build_info_by_number_or_permalink(jenkins, ref, number):
    assert jenkins.json(f"{job_path('app')}/{ref}")["number"] == number


def test_a_missing_permalink_is_a_404(jenkins):
    """A job that never failed has no lastFailedBuild."""
    assert jenkins.get(f"{job_path('team/nested')}/lastFailedBuild").status_code == 404


def test_pipeline_changes_and_culprits(jenkins):
    body = jenkins.json(f"{job_path('app')}/2", {"tree": CHANGES_TREE})
    assert body["culprits"] == [
        {"fullName": "Bob", "absoluteUrl": f"{jenkins.base}/user/bob"}
    ]
    [change_set] = body["changeSets"]
    [item] = change_set["items"]
    assert (item["commitId"], item["msg"], item["author"]["fullName"]) == (
        "a" * 40,
        "Break it",
        "Bob",
    )
    assert "changeSet" not in body


def test_freestyle_changes_use_change_set(jenkins):
    body = jenkins.json(f"{job_path('team/nested')}/7", {"tree": CHANGES_TREE})
    assert body["changeSet"]["items"][0]["msg"] == "Fix"
    assert "changeSets" not in body


def test_console_text(jenkins):
    response = jenkins.text(f"{job_path('app')}/1/consoleText")
    assert response.text == "one\n"


def test_nodes_and_monitor_data(jenkins):
    shallow = jenkins.json("computer")["computer"]
    assert [c["displayName"] for c in shallow] == [
        "Built-In Node",
        "agent-1",
        "agent-2",
    ]
    assert shallow[1]["monitorData"] == {}
    deep = jenkins.json("computer", {"depth": 1})["computer"]
    assert deep[1]["monitorData"]["hudson.node_monitors.DiskSpaceMonitor"]["size"] > 0
    assert deep[2]["offline"] is True and deep[2]["offlineCauseReason"] == "maintenance"


def test_node_config_names_its_host(jenkins):
    xml = jenkins.text("computer/agent-1/config.xml").text
    assert "<host>10.0.0.5</host>" in xml
    assert jenkins.text("computer/(built-in)/config.xml").status_code == 404


def test_whoami(jenkins):
    me = jenkins.json("me")
    assert (me["id"], me["fullName"]) == (IDENTITY, "Alice Example")


# --- triggering and following a build ----------------------------------


def _queue_id(response):
    assert response.status_code == 201, response.text
    match = re.search(r"/queue/item/(\d+)", response.headers["Location"])
    return int(match.group(1))


def test_the_queue_to_build_lifecycle(jenkins):
    """What jeeves' --wait does: queue item, then executable, then the log."""
    item_id = _queue_id(
        jenkins.post(f"{job_path('app')}/buildWithParameters", {"GREETING": "bonjour"})
    )
    assert [i["id"] for i in jenkins.json("queue")["items"]] == [item_id]

    polls = []
    while True:
        item = jenkins.json(f"queue/item/{item_id}")
        polls.append(item.get("why"))
        if (item.get("executable") or {}).get("number"):
            break
    assert polls[0] == "Waiting for next available executor"
    assert item["_class"] == "hudson.model.Queue$LeftItem"
    number = item["executable"]["number"]
    assert number == 3
    assert jenkins.json("queue")["items"] == []

    text, start, more = "", 0, True
    chunks = 0
    while more:
        response = jenkins.text(
            f"{job_path('app')}/{number}/logText/progressiveText", {"start": start}
        )
        text += response.text
        start = int(response.headers["X-Text-Size"])
        more = response.headers.get("X-More-Data", "").lower() == "true"
        chunks += 1
    assert text == "Started\ngreeting=bonjour\nFinished: SUCCESS\n"
    assert chunks == 3
    build = jenkins.json(f"{job_path('app')}/{number}")
    assert (build["result"], build["building"]) == ("SUCCESS", False)


def test_a_build_without_parameters(jenkins):
    item_id = _queue_id(jenkins.post(f"{job_path('lint')}/build"))
    item = jenkins.json(f"queue/item/{item_id}")
    number = item["executable"]["number"]  # queue_polls 0: started at once
    for _ in range(3):
        jenkins.json(f"{job_path('lint')}/{number}")
    assert jenkins.json(f"{job_path('lint')}/{number}")["result"] == "UNSTABLE"


def test_defaults_fill_missing_parameters(jenkins):
    item_id = _queue_id(
        jenkins.post(f"{job_path('app')}/buildWithParameters", {"ENV": "prod"})
    )
    assert (
        jenkins.json(f"queue/item/{item_id}")["params"]
        == "\nGREETING=hello\nFAST=False\nENV=prod"
    )


def test_build_with_parameters_on_a_plain_job_is_refused(jenkins):
    assert (
        jenkins.post(f"{job_path('lint')}/buildWithParameters", {"X": "1"}).status_code
        == 400
    )


def test_stop_aborts_a_running_build(jenkins):
    item_id = _queue_id(
        jenkins.post(f"{job_path('app')}/buildWithParameters", {"GREETING": "x"})
    )
    while not (jenkins.json(f"queue/item/{item_id}").get("executable") or {}):
        pass
    number = jenkins.json(f"queue/item/{item_id}")["executable"]["number"]
    response = jenkins.post(f"{job_path('app')}/{number}/stop")
    assert response.status_code == 200  # the redirect to the build page
    build = jenkins.json(f"{job_path('app')}/{number}")
    assert (build["result"], build["building"]) == ("ABORTED", False)
    assert (
        "Finished: ABORTED"
        in jenkins.text(f"{job_path('app')}/{number}/consoleText").text
    )


def test_running_builds_show_on_the_job(jenkins):
    _queue_id(jenkins.post(f"{job_path('lint')}/build"))
    job = jenkins.json(job_path("lint"))
    assert job["color"] == "notbuilt_anime"
    assert jenkins.json("computer")["busyExecutors"] == 1


def test_an_unknown_job_is_a_404(jenkins):
    assert jenkins.get(job_path("nope")).status_code == 404
    assert jenkins.post(f"{job_path('nope')}/build").status_code == 404


# --- security ------------------------------------------------------------


def test_api_tokens_need_no_crumb_by_default(base_url, auth):
    """Jenkins exempts API-token logins from CSRF, as jeeves' tokens are."""
    response = requests.post(f"{base_url}/{job_path('lint')}/build", headers=auth)
    assert response.status_code == 201


def test_crumbs_can_be_enforced(base_url, auth, seed):
    seed({**SEED, "security": {"api_tokens_exempt": False}})
    without = requests.post(f"{base_url}/{job_path('lint')}/build", headers=auth)
    assert without.status_code == 403
    assert "crumb" in without.text
    jenkins = Jenkins(base_url, auth)
    assert jenkins.post(f"{job_path('lint')}/build").status_code == 201


def test_with_crumbs_off_the_issuer_is_absent(jenkins, seed):
    seed({**SEED, "security": {"crumbs": False}})
    assert (
        jenkins.session.get(f"{jenkins.base}/crumbIssuer/api/json").status_code == 404
    )
    assert jenkins.post(f"{job_path('lint')}/build").status_code == 201


def test_anonymous_is_forbidden(base_url):
    response = requests.get(f"{base_url}/api/json")
    assert response.status_code == 403


def test_a_wrong_token_is_a_401(base_url):
    assert (
        requests.get(
            f"{base_url}/api/json", headers=basic(IDENTITY, "wrong")
        ).status_code
        == 401
    )


def test_the_login_redirect(base_url, seed):
    """SSO-style: anonymous API calls bounce to commenceLogin, as jeeves detects."""
    seed({**SEED, "security": {"login_redirect": True}})
    response = requests.get(f"{base_url}/api/json")
    assert response.history and LOGIN_MARKER in response.history[0].headers["Location"]
    assert LOGIN_MARKER in response.url
    assert response.status_code == 200


def test_the_token_decides_the_identity(base_url):
    me = requests.get(f"{base_url}/me/api/json", headers=basic("anyone", TOKEN)).json()
    assert me["id"] == IDENTITY


def test_the_jeeves_fixture(base_url):
    assert (
        requests.post(f"{base_url}/_fake/fixtures/jeeves-fixtures").status_code == 204
    )
    jenkins = Jenkins(base_url, basic("jeeves-e2e", "jeeves-e2e-token"))
    names = [j["name"] for j in jenkins.json("")["jobs"]]
    assert names == ["e2e-freestyle", "e2e-pipeline", "e2e-lock-holder", "e2e-folder"]
    assert [j["name"] for j in jenkins.json(job_path("e2e-folder"))["jobs"]] == [
        "nested"
    ]
    item_id = _queue_id(
        jenkins.post(
            f"{job_path('e2e-folder/nested')}/buildWithParameters", {"GREETING": "hey"}
        )
    )
    number = jenkins.json(f"queue/item/{item_id}")["executable"]["number"]
    for _ in range(2):
        jenkins.json(f"{job_path('e2e-folder/nested')}/{number}")
    assert (
        "greeting=hey"
        in jenkins.text(f"{job_path('e2e-folder/nested')}/{number}/consoleText").text
    )
