"""Jenkins' JSON, as ``/api/json`` renders it.

``depth`` works as on Jenkins: the object asked for is shown in full, and a
nested object is shown in full only while depth remains, otherwise as a
summary (``_class``, ``name`` or ``number``, ``url``). With ``tree`` the
object is rendered deep and then cut down to the fields named.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fake_backends.server.jenkins.model import (
    BUILT_IN_NODE,
    PERMALINKS,
    Build,
    JenkinsState,
    Job,
    JobType,
    Node,
    QueueItem,
    Resource,
    Result,
)

__all__ = [
    "TREE_DEPTH",
    "build",
    "computers",
    "job",
    "lockable",
    "me",
    "millis",
    "queue",
    "queue_item",
    "root",
]

# How deep to render before applying a tree; deep enough for any tree jeeves
# sends (root > jobs > builds > actions > parameters).
TREE_DEPTH = 4
_MS_PER_SECOND = 1000
_HEALTH_WINDOW = 5
_PERCENT = 100
_LOCKABLE_ROOT_CLASS = (
    "org.jenkins.plugins.lockableresources.actions.LockableResourcesRootAction"
)
# Jenkins' sunny icon starts above this score.
_HEALTHY_SCORE = 80

JOB_CLASSES = {
    JobType.FREESTYLE: "hudson.model.FreeStyleProject",
    JobType.PIPELINE: "org.jenkinsci.plugins.workflow.job.WorkflowJob",
    JobType.FOLDER: "com.cloudbees.hudson.plugins.folder.Folder",
    JobType.MATRIX: "hudson.matrix.MatrixProject",
}
BUILD_CLASSES = {
    JobType.FREESTYLE: "hudson.model.FreeStyleBuild",
    JobType.PIPELINE: "org.jenkinsci.plugins.workflow.job.WorkflowRun",
    JobType.MATRIX: "hudson.matrix.MatrixBuild",
}
_COLOURS = {
    Result.SUCCESS: "blue",
    Result.UNSTABLE: "yellow",
    Result.FAILURE: "red",
    Result.ABORTED: "aborted",
    Result.NOT_BUILT: "notbuilt",
}
_PARAMETER_CLASSES = {
    "string": "StringParameterDefinition",
    "text": "TextParameterDefinition",
    "boolean": "BooleanParameterDefinition",
    "choice": "ChoiceParameterDefinition",
    "password": "PasswordParameterDefinition",
}
_VALUE_CLASSES = {
    "string": "StringParameterValue",
    "text": "TextParameterValue",
    "boolean": "BooleanParameterValue",
    "choice": "StringParameterValue",
    "password": "PasswordParameterValue",
}


def _value_class(kind: str | None) -> str:
    return _VALUE_CLASSES.get(kind or "string", "StringParameterValue")


def millis(value: dt.datetime) -> int:
    return int(value.timestamp() * _MS_PER_SECOND)


def _url(base: str, path: str = "") -> str:
    return f"{base}/{path}"


def _colour(item: Job) -> str:
    if item.disabled:
        return "disabled"
    builds = item.newest_first()
    if not builds:
        return "notbuilt"
    newest = builds[0]
    if newest.building:
        finished = next((b for b in builds if not b.building), None)
        base = _COLOURS[finished.result] if finished else "notbuilt"
        return f"{base}_anime"
    return _COLOURS[newest.result]


def _job_summary(base: str, item: Job) -> dict[str, Any]:
    summary = {
        "_class": JOB_CLASSES[item.type],
        "name": item.name,
        "url": _url(base, item.url_path),
    }
    if item.type is not JobType.FOLDER:
        summary["color"] = _colour(item)
    return summary


def _build_summary(base: str, item: Job, b: Build) -> dict[str, Any]:
    return {
        "_class": BUILD_CLASSES[item.type],
        "number": b.number,
        "url": _url(base, f"{item.url_path}{b.number}/"),
    }


def _health(item: Job) -> list[dict[str, Any]]:
    recent = [b for b in item.newest_first() if not b.building][:_HEALTH_WINDOW]
    if not recent:
        return []
    good = sum(1 for b in recent if b.result == Result.SUCCESS)
    score = good * _PERCENT // len(recent)
    return [
        {
            "description": f"Build stability: {len(recent) - good} out of the last "
            f"{len(recent)} builds failed.",
            "iconClassName": (
                "icon-health-80plus" if score > _HEALTHY_SCORE else "icon-health-00to19"
            ),
            "iconUrl": "health-80plus.png",
            "score": score,
        }
    ]


def _parameters_property(item: Job) -> list[dict[str, Any]]:
    if not item.parameters:
        return []
    definitions = []
    for p in item.parameters:
        definition = {
            "_class": f"hudson.model.{_PARAMETER_CLASSES[p.kind]}",
            "defaultParameterValue": {
                "_class": f"hudson.model.{_VALUE_CLASSES[p.kind]}",
                "name": p.name,
                "value": None if p.kind == "password" else p.default,
            },
            "description": p.description,
            "name": p.name,
            "type": _PARAMETER_CLASSES[p.kind],
        }
        if p.kind == "choice":
            definition["choices"] = list(p.choices)
        definitions.append(definition)
    return [
        {
            "_class": "hudson.model.ParametersDefinitionProperty",
            "parameterDefinitions": definitions,
        }
    ]


def job(state: JenkinsState, base: str, item: Job, depth: int) -> dict[str, Any]:
    summary = _job_summary(base, item)
    full: dict[str, Any] = {
        **summary,
        "actions": [],
        "description": item.description,
        "displayName": item.name,
        "displayNameOrNull": None,
        "fullDisplayName": " » ".join(item.path),
        "fullName": item.full_name,
        "healthReport": _health(item),
    }
    if item.type is JobType.FOLDER:
        full["jobs"] = [
            (
                job(state, base, child, depth - 1)
                if depth > 0
                else _job_summary(base, child)
            )
            for child in item.children.values()
        ]
        full["primaryView"] = {"_class": "hudson.model.AllView", "name": "All"}
        return full

    def nested(b: Build | None) -> dict[str, Any] | None:
        if b is None:
            return None
        return (
            build(state, base, item, b, depth - 1)
            if depth > 0
            else _build_summary(base, item, b)
        )

    builds = item.newest_first()
    full.update(
        {
            "buildable": not item.disabled,
            "builds": [nested(b) for b in builds],
            "firstBuild": nested(builds[-1] if builds else None),
            "inQueue": any(
                q.job == item.path and not q.left for q in state.queue.values()
            ),
            "keepDependencies": False,
            "nextBuildNumber": item.next_number,
            "property": _parameters_property(item),
            "queueItem": None,
            "concurrentBuild": False,
            "disabled": item.disabled,
            "resumeBlocked": False,
        }
    )
    for link, test in PERMALINKS.items():
        full[link] = nested(next((b for b in builds if test(b)), None))
    return full


def _culprit(state: JenkinsState, base: str, user_id: str) -> dict[str, Any]:
    user = state.user(user_id)
    return {
        "absoluteUrl": _url(base, f"user/{user.id.lower()}"),
        "fullName": user.full_name,
    }


def _change_items(state: JenkinsState, base: str, b: Build) -> list[dict[str, Any]]:
    return [
        {
            "_class": "hudson.plugins.git.GitChangeSet",
            "affectedPaths": [],
            "commitId": c.commit_id,
            "timestamp": millis(c.timestamp),
            "author": _culprit(state, base, c.author),
            "authorEmail": c.author_email,
            "comment": f"{c.msg}\n",
            "date": c.timestamp.strftime("%Y-%m-%d %H:%M:%S +0000"),
            "id": c.commit_id,
            "msg": c.msg,
            "paths": [],
        }
        for c in b.changes
    ]


def build(
    state: JenkinsState, base: str, item: Job, b: Build, depth: int
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if b.parameters:
        kinds = {p.name: p.kind for p in item.parameters}
        actions.append(
            {
                "_class": "hudson.model.ParametersAction",
                "parameters": [
                    {
                        "_class": f"hudson.model.{_value_class(kinds.get(name))}",
                        "name": name,
                        "value": value,
                    }
                    for name, value in b.parameters.items()
                ],
            }
        )
    actions.append({"_class": "hudson.model.CauseAction", "causes": b.causes})
    full = {
        **_build_summary(base, item, b),
        "actions": actions,
        "artifacts": [],
        "building": b.building,
        "description": None,
        "displayName": f"#{b.number}",
        "duration": b.duration_ms,
        "estimatedDuration": b.duration_ms or -1,
        "executor": None,
        "fullDisplayName": f"{' » '.join(item.path)} #{b.number}",
        "id": str(b.number),
        "keepLog": False,
        "queueId": b.queue_id,
        "result": b.result.value if b.result else None,
        "timestamp": millis(b.timestamp),
        "inProgress": b.building,
        "culprits": [_culprit(state, base, c) for c in b.culprits],
    }
    changes = {
        "_class": "hudson.plugins.git.GitChangeSetList",
        "items": _change_items(state, base, b),
        "kind": "git",
    }
    if item.type is JobType.PIPELINE:
        full["changeSets"] = [changes] if b.changes else []
    else:
        full["changeSet"] = changes
    return full


def root(state: JenkinsState, base: str, depth: int) -> dict[str, Any]:
    return {
        "_class": "hudson.model.Hudson",
        "assignedLabels": [{"name": "built-in"}],
        "mode": "NORMAL",
        "nodeDescription": state.nodes[BUILT_IN_NODE].description,
        "nodeName": "",
        "numExecutors": state.executors,
        "description": state.description,
        "jobs": [
            job(state, base, j, depth - 1) if depth > 0 else _job_summary(base, j)
            for j in state.jobs.values()
        ],
        "overallLoad": {},
        "primaryView": {
            "_class": "hudson.model.AllView",
            "name": "all",
            "url": _url(base),
        },
        "quietDownReason": None,
        "quietingDown": False,
        "slaveAgentPort": -1,
        "unlabeledLoad": {"_class": "jenkins.model.UnlabeledLoadStatistics"},
        "url": _url(base),
        "useCrumbs": state.security.crumbs,
        "useSecurity": True,
        "views": [{"_class": "hudson.model.AllView", "name": "all", "url": _url(base)}],
    }


def queue_item(state: JenkinsState, base: str, item: QueueItem) -> dict[str, Any]:
    owner = state.job(item.job)
    task = _job_summary(base, owner)
    shown: dict[str, Any] = {
        "actions": [{"_class": "hudson.model.CauseAction", "causes": item.causes}],
        "blocked": False,
        "buildable": not item.left,
        "id": item.id,
        "inQueueSince": millis(item.since),
        "params": "".join(f"\n{k}={v}" for k, v in item.parameters.items()),
        "stuck": False,
        "task": task,
        "url": f"queue/item/{item.id}/",
        "why": None if item.left else item.why,
    }
    if item.cancelled:
        shown.update(
            {
                "_class": "hudson.model.Queue$LeftItem",
                "cancelled": True,
                "executable": None,
            }
        )
    elif item.build is not None:
        b = owner.builds[item.build]
        shown.update(
            {
                "_class": "hudson.model.Queue$LeftItem",
                "cancelled": False,
                "executable": _build_summary(base, owner, b),
            }
        )
    else:
        shown["_class"] = "hudson.model.Queue$BuildableItem"
        shown["pending"] = False
    return shown


def queue(state: JenkinsState, base: str) -> dict[str, Any]:
    return {
        "_class": "hudson.model.Queue",
        "discoverableItems": [],
        "items": [
            queue_item(state, base, q) for q in state.queue.values() if not q.left
        ],
    }


# What a healthy node reports when the seed gives no monitor data.
_GIB = 1024**3
_DEFAULT_DISK_GIB = 50
_DEFAULT_SWAP_GIB = 2
_DEFAULT_TEMP_GIB = 10
_DEFAULT_RESPONSE_MS = 10
_MONITOR_DEFAULTS = {
    "hudson.node_monitors.ArchitectureMonitor": "Linux (amd64)",
    "hudson.node_monitors.ClockMonitor": {
        "_class": "hudson.util.ClockDifference",
        "diff": 0,
    },
    "hudson.node_monitors.DiskSpaceMonitor": {
        "_class": "hudson.node_monitors.DiskSpaceMonitorDescriptor$DiskSpace",
        "path": "/var/jenkins_home",
        "size": _DEFAULT_DISK_GIB * _GIB,
    },
    "hudson.node_monitors.ResponseTimeMonitor": {
        "_class": "hudson.node_monitors.ResponseTimeMonitor$Data",
        "average": _DEFAULT_RESPONSE_MS,
    },
    "hudson.node_monitors.SwapSpaceMonitor": {
        "_class": "hudson.node_monitors.SwapSpaceMonitor$MemoryUsage2",
        "availableSwapSpace": _DEFAULT_SWAP_GIB * _GIB,
    },
    "hudson.node_monitors.TemporarySpaceMonitor": {
        "_class": "hudson.node_monitors.DiskSpaceMonitorDescriptor$DiskSpace",
        "path": "/tmp",
        "size": _DEFAULT_TEMP_GIB * _GIB,
    },
}


def _computer(state: JenkinsState, node: Node, depth: int) -> dict[str, Any]:
    built_in = node.name == BUILT_IN_NODE
    # Every build runs on the built-in node.
    busy = state.running() if built_in else 0
    shown = {
        "_class": (
            "hudson.model.Hudson$MasterComputer"
            if built_in
            else "hudson.slaves.SlaveComputer"
        ),
        "actions": [],
        "assignedLabels": [{"name": label} for label in node.labels],
        "description": node.description,
        "displayName": "Built-In Node" if built_in else node.name,
        "executors": [{}] * node.executors,
        "icon": "symbol-computer",
        "idle": busy == 0,
        "jnlpAgent": not built_in and node.host is None,
        "launchSupported": not built_in,
        "manualLaunchAllowed": not built_in,
        "numExecutors": node.executors,
        "offline": node.offline,
        "offlineCause": None,
        "offlineCauseReason": node.offline_reason,
        "oneOffExecutors": [],
        "temporarilyOffline": node.offline,
        "monitorData": {},
    }
    if depth > 0 and not node.offline:
        shown["monitorData"] = {**_MONITOR_DEFAULTS, **node.monitor}
    return shown


def computers(state: JenkinsState, depth: int) -> dict[str, Any]:
    shown = [_computer(state, n, depth) for n in state.nodes.values()]
    return {
        "_class": "hudson.model.ComputerSet",
        "busyExecutors": state.running(),
        "computer": shown,
        "displayName": "Nodes",
        "totalExecutors": sum(
            n.executors for n in state.nodes.values() if not n.offline
        ),
    }


def me(state: JenkinsState, base: str, identity: str) -> dict[str, Any]:
    user = state.user(identity)
    return {
        "_class": "hudson.model.User",
        "absoluteUrl": _url(base, f"user/{user.id.lower()}"),
        "description": None,
        "fullName": user.full_name,
        "id": user.id,
        "property": [
            {"_class": "hudson.tasks.Mailer$UserProperty", "address": user.email}
        ],
    }


def _resource(r: Resource) -> dict[str, Any]:
    reserved_at = r.reserved_at.isoformat() if r.reserved_at else None
    build_name = r.locked_by
    return {
        "_class": "org.jenkins.plugins.lockableresources.LockableResource",
        "buildName": build_name,
        "description": r.description,
        "ephemeral": r.ephemeral,
        "free": r.free,
        "labels": " ".join(r.labels),
        "labelsAsList": list(r.labels),
        "lockCause": f"[{build_name}] is locking {r.name}" if build_name else None,
        "locked": r.locked_by is not None,
        "name": r.name,
        "note": r.note,
        "reserved": r.reserved_by is not None,
        "reservedBy": r.reserved_by,
        "reservedByEmail": r.reserved_by_email,
        "reservedTimestamp": reserved_at,
        "stolen": r.stolen,
    }


def lockable(state: JenkinsState) -> dict[str, Any]:
    return {
        "_class": _LOCKABLE_ROOT_CLASS,
        "resources": [_resource(r) for r in state.resources.values()],
    }
