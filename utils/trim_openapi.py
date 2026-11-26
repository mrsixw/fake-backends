"""Cut a published OpenAPI description down to the endpoints a fake serves.

The full descriptions run to megabytes. The tests need only the response
schemas of the endpoints listed below and every component they reference, so
this writes that subset, which is committed:

    uv run python utils/trim_openapi.py github /tmp/github.json <source-ref>
    uv run python utils/trim_openapi.py jira-cloud /tmp/cloud-v3.json <source-ref>
    uv run python utils/trim_openapi.py jira-server /tmp/dc.json <source-ref>

``source-ref`` records where the file came from (a commit, or a version), and
each target's docstring below names the URL to download. The result is plain
JSON Schema 2020-12: OpenAPI 3.0's ``nullable`` is rewritten, and a known
defect in GitHub's 3.1 description is repaired (see ``_repair``). Re-run with
a newer description to pick up upstream changes.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["TARGETS", "Target", "main", "trim"]

REPO_ROOT = Path(__file__).resolve().parent.parent
REF_PREFIX = "#/components/schemas/"
NULL = {"type": "null"}
_DOCUMENTATION_ONLY = frozenset({"example", "examples"})


@dataclass(frozen=True)
class Target:
    """One published description, and the operations a fake serves from it."""

    url: str
    output: Path
    # Prepended to each path below to find it in the description.
    prefix: str
    # (method, path, status) for every response with a body.
    operations: tuple[tuple[str, str, str], ...]
    # Further components the tests validate against, such as error bodies.
    extra: tuple[str, ...]
    # Defects in the description, corrected: (component, path into it, the
    # corrected value, why). Each is a contradiction the description makes
    # with itself, so the correction is not a guess.
    fixes: tuple[tuple[str, tuple[str, ...], Any, str], ...] = ()


_JIRA_COMMON = (
    ("get", "/myself", "200"),
    ("get", "/issue/{issueIdOrKey}", "200"),
    ("post", "/issue", "201"),
    ("post", "/issue/{issueIdOrKey}/comment", "201"),
    ("get", "/issue/{issueIdOrKey}/transitions", "200"),
)

TARGETS = {
    "github": Target(
        url=(
            "https://raw.githubusercontent.com/github/rest-api-description/<sha>/"
            "descriptions-next/api.github.com/api.github.com.2022-11-28.json"
        ),
        output=REPO_ROOT / "tests" / "github" / "openapi_subset.json",
        prefix="",
        operations=(
            ("get", "/user", "200"),
            ("get", "/repos/{owner}/{repo}/pulls/{pull_number}", "200"),
            ("get", "/repos/{owner}/{repo}/pulls/{pull_number}/reviews", "200"),
            (
                "get",
                "/repos/{owner}/{repo}/branches/{branch}/protection/"
                "required_pull_request_reviews",
                "200",
            ),
            ("get", "/repos/{owner}/{repo}/commits/{ref}/check-runs", "200"),
            ("get", "/repos/{owner}/{repo}/commits/{ref}/status", "200"),
            ("get", "/search/issues", "200"),
            ("get", "/repos/{owner}/{repo}/issues", "200"),
            ("post", "/repos/{owner}/{repo}/issues", "201"),
            ("get", "/repos/{owner}/{repo}/issues/{issue_number}", "200"),
            ("patch", "/repos/{owner}/{repo}/issues/{issue_number}", "200"),
            ("get", "/repos/{owner}/{repo}/issues/{issue_number}/comments", "200"),
            ("post", "/repos/{owner}/{repo}/issues/{issue_number}/comments", "201"),
            ("get", "/repos/{owner}/{repo}/releases/latest", "200"),
        ),
        extra=("basic-error", "validation-error"),
    ),
    "jira-cloud": Target(
        url="https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json",
        output=REPO_ROOT / "tests" / "jira" / "openapi_cloud.json",
        prefix="/rest/api/3",
        operations=(
            *_JIRA_COMMON,
            ("get", "/search/jql", "200"),
            ("get", "/user/search", "200"),
        ),
        # An issue's fields are free-form in IssueBean, so the tests also
        # check each field against its own component.
        extra=(
            "ErrorCollection",
            "StatusDetails",
            "IssueTypeDetails",
            "UserDetails",
            "Priority",
            "PageOfComments",
            "ProjectDetails",
        ),
    ),
    "jira-server": Target(
        # Jira Software Data Center 11.3, which includes the platform REST API.
        url=(
            "https://dac-static.atlassian.com/server/jira/platform/"
            "jira_software_dc_11003_swagger.v3.json"
        ),
        output=REPO_ROOT / "tests" / "jira" / "openapi_server.json",
        # The description's server URL already ends in /rest.
        prefix="/api/2",
        operations=(*_JIRA_COMMON, ("get", "/search", "200")),
        fixes=(
            (
                "IssueBean",
                ("properties", "fields", "additionalProperties"),
                {},
                "Declares every issue field an object, but its own example has "
                '"description": "example bug report" and "updated": 1, and '
                "summary, labels and created are strings and lists in practice.",
            ),
        ),
        extra=(
            "ErrorCollection",
            "StatusJsonBean",
            "IssueTypeJsonBean",
            "UserJsonBean",
            "PriorityJsonBean",
            "CommentsWithPaginationJsonBean",
            "ProjectJsonBean",
        ),
    ),
}


def _resolve(document: dict[str, Any], node: Any) -> Any:
    """Follow a components/responses $ref to the response object."""
    while isinstance(node, dict) and "$ref" in node:
        parts = node["$ref"].removeprefix("#/").split("/")
        node = document
        for part in parts:
            node = node[part]
    return node


def _refs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(REF_PREFIX):
            found.add(ref.removeprefix(REF_PREFIX))
        for value in node.values():
            found |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs(value)
    return found


def _repair(node: Any, *, names_properties: bool = False) -> Any:
    """Rewrite the description into plain JSON Schema 2020-12.

    - OpenAPI 3.0's ``nullable: true`` becomes a ``null`` type (or, beside a
      ``$ref``, "the reference or null").
    - GitHub's 3.1 description encodes nullable references as
      ``{"allOf": [X], "type": ["null"]}``, which nothing satisfies: a null
      fails X, and anything else fails the type. GitHub's own responses send
      null there (label ``archived_by``, checked against api.github.com on
      2026-11-26), so the pattern becomes "X or null", as the 3.0
      description has it.
    """
    if isinstance(node, list):
        return [_repair(item) for item in node]
    if not isinstance(node, dict):
        return node
    if names_properties:
        # Keys here are property names, so one called "example" is kept.
        return {key: _repair(value) for key, value in node.items()}
    # Examples document; they never constrain. Dropping them keeps the subset
    # small.
    repaired = {
        key: _repair(value, names_properties=key == "properties")
        for key, value in node.items()
        if key not in _DOCUMENTATION_ONLY
    }
    if "allOf" in repaired and repaired.get("type") == ["null"]:
        rest = {k: v for k, v in repaired.items() if k not in {"allOf", "type"}}
        return {**rest, "anyOf": [*repaired["allOf"], NULL]}
    if repaired.pop("nullable", False):
        kind = repaired.get("type")
        if isinstance(kind, str):
            repaired["type"] = [kind, "null"]
        elif isinstance(kind, list) and "null" not in kind:
            repaired["type"] = [*kind, "null"]
        elif kind is None:
            return {"anyOf": [repaired, NULL]}
    return repaired


def trim(document: dict[str, Any], target: Target, source_ref: str) -> dict[str, Any]:
    operations: dict[str, Any] = {}
    for method, path, status in target.operations:
        responses = document["paths"][f"{target.prefix}{path}"][method]["responses"]
        response = _resolve(document, responses[status])
        content = response["content"]
        schema = next(iter(content.values()))["schema"]
        operations[f"{method.upper()} {path} {status}"] = schema

    all_schemas = document["components"]["schemas"]
    for component, path, value, _reason in target.fixes:
        node = all_schemas[component]
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value
    wanted = set(target.extra) | _refs(operations)
    kept: dict[str, Any] = {}
    while wanted:
        name = wanted.pop()
        if name in kept:
            continue
        kept[name] = all_schemas[name]
        wanted |= _refs(kept[name]) - kept.keys()

    return _repair(
        {
            "source": target.url,
            "source_ref": source_ref,
            "version": document.get("info", {}).get("version"),
            "fixes": [
                {"component": c, "path": "/".join(p), "why": why}
                for c, p, _, why in target.fixes
            ],
            "operations": operations,
            "components": {"schemas": dict(sorted(kept.items()))},
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", choices=sorted(TARGETS))
    parser.add_argument("description", type=Path, help="the full OpenAPI JSON")
    parser.add_argument("source_ref", help="the commit or version it came from")
    args = parser.parse_args()
    target = TARGETS[args.target]
    document = json.loads(args.description.read_text())
    target.output.parent.mkdir(parents=True, exist_ok=True)
    subset = trim(document, target, args.source_ref)
    target.output.write_text(json.dumps(subset, indent=1) + "\n")
    print(f"wrote {target.output}")


if __name__ == "__main__":
    main()
