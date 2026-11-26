"""Cut GitHub's OpenAPI description down to the endpoints fake-github serves.

The full description is about 14 MB. The tests need only the response schemas
of the endpoints below and every component they reference, so this writes
that subset, which is committed:

    curl -fsSL -o /tmp/github.json \\
      https://raw.githubusercontent.com/github/rest-api-description/<sha>/descriptions-next/api.github.com/api.github.com.2022-11-28.json
    uv run python utils/trim_github_openapi.py /tmp/github.json <sha>

``descriptions-next`` is the OpenAPI 3.1 variant, whose schemas are plain JSON
Schema (2020-12), so the tests validate with jsonschema directly. Re-run this
with a newer commit to pick up GitHub's changes; the conformance check
(issue 5) is what notices that they matter.
"""

import argparse
import json
from pathlib import Path
from typing import Any

__all__ = ["OPERATIONS", "OUTPUT", "main", "trim"]

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "tests" / "github" / "openapi_subset.json"
SOURCE_REPO = "github/rest-api-description"
REF_PREFIX = "#/components/schemas/"

# (method, path, status) for every REST response fake-github serves.
OPERATIONS = [
    ("get", "/user", "200"),
    ("get", "/repos/{owner}/{repo}/pulls/{pull_number}", "200"),
    ("get", "/repos/{owner}/{repo}/pulls/{pull_number}/reviews", "200"),
    (
        "get",
        "/repos/{owner}/{repo}/branches/{branch}/protection/required_pull_request_reviews",
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
]
# Error bodies, checked for every 4xx the fake sends.
EXTRA_SCHEMAS = ["basic-error", "validation-error"]


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


def _repair(node: Any) -> Any:
    """Fix one known defect in GitHub's 3.1 description.

    Nullable references come out as ``{"allOf": [X], "type": ["null"]}``,
    which nothing satisfies: a null fails X, and anything else fails the type.
    GitHub's own responses send null there (label ``archived_by``, checked
    against api.github.com on 2026-11-26), so this rewrites the pattern to
    "X or null", which is what the 3.0 description says.
    """
    if isinstance(node, dict):
        repaired = {key: _repair(value) for key, value in node.items()}
        if "allOf" in repaired and repaired.get("type") == ["null"]:
            rest = {k: v for k, v in repaired.items() if k not in {"allOf", "type"}}
            return {**rest, "anyOf": [*repaired["allOf"], {"type": "null"}]}
        return repaired
    if isinstance(node, list):
        return [_repair(item) for item in node]
    return node


def trim(document: dict[str, Any], commit: str) -> dict[str, Any]:
    operations: dict[str, Any] = {}
    for method, path, status in OPERATIONS:
        response = _resolve(
            document, document["paths"][path][method]["responses"][status]
        )
        schema = response["content"]["application/json"]["schema"]
        operations[f"{method.upper()} {path} {status}"] = schema

    all_schemas = document["components"]["schemas"]
    wanted = set(EXTRA_SCHEMAS) | _refs(operations)
    kept: dict[str, Any] = {}
    while wanted:
        name = wanted.pop()
        if name in kept:
            continue
        kept[name] = all_schemas[name]
        wanted |= _refs(kept[name]) - kept.keys()

    return _repair(
        {
            "source": f"https://github.com/{SOURCE_REPO}/blob/{commit}/descriptions-next/api.github.com/api.github.com.2022-11-28.json",
            "operations": operations,
            "components": {"schemas": dict(sorted(kept.items()))},
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("description", type=Path, help="the full OpenAPI JSON")
    parser.add_argument("commit", help=f"the {SOURCE_REPO} commit it came from")
    args = parser.parse_args()
    document = json.loads(args.description.read_text())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(trim(document, args.commit), indent=1) + "\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
