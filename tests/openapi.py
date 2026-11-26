"""Validate responses against a subset made by utils/trim_openapi.py."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012

__all__ = ["Schemas"]

_URI = "urn:fake-backends:openapi"


def _rewrite_refs(node):
    """Point the subset's local $refs at the registered document."""
    if isinstance(node, dict):
        return {
            key: (
                f"{_URI}{value}"
                if key == "$ref" and isinstance(value, str) and value.startswith("#/")
                else _rewrite_refs(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_refs(item) for item in node]
    return node


class Schemas:
    def __init__(self, path: Path) -> None:
        self.document = json.loads(path.read_text())
        self._registry = Registry().with_resource(
            _URI, DRAFT202012.create_resource(self.document)
        )

    def _errors(self, schema, body) -> list[str]:
        validator = Draft202012Validator(_rewrite_refs(schema), registry=self._registry)
        return [
            f"{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in validator.iter_errors(body)
        ]

    def errors(self, operation: str, body) -> list[str]:
        """How ``body`` breaks the schema for "METHOD /path STATUS"."""
        return self._errors(self.document["operations"][operation], body)

    def component_errors(self, name: str, body) -> list[str]:
        return self._errors({"$ref": f"#/components/schemas/{name}"}, body)
