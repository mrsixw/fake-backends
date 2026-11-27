"""Compare a real service's response with a fake's, by shape and chosen values.

A response is reduced to its *shape*: every field's path and JSON type
(``string``, ``number``, ``boolean``, ``null``, ``object``, ``array``), with
list items merged under ``[]``. Two shapes differ when:

- the real service has a field the fake lacks (unless it is ``not_modelled``);
- the fake has a field the real service lacks (the fake invented it);
- a field's type differs, null included (unless it is ``volatile``).

Values are compared only at the paths a request names in ``values``: the
fields a seed fixes, such as titles, states and counts. Lists are compared in
order on purpose: order is part of an API, and the check caught Jenkins
listing jobs by name where fake-jenkins kept seed order.

Paths are dotted, ``[]`` for list items: ``head.repo.owner.login``,
``labels[].name``. A pattern may use ``*`` for one segment and ``**`` for any
run of segments, so ``**.created_at`` matches every ``created_at``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Difference", "Rules", "Sample", "compare", "shape"]

LIST_ITEM = "[]"
# The path of the body itself, so a text body that turns into JSON is caught.
BODY = "(body)"
_TYPES = (
    (bool, "boolean"),
    (int, "number"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
)


def _type(value: Any) -> str:
    if value is None:
        return "null"
    for kind, name in _TYPES:
        if isinstance(value, kind):
            return name
    return type(value).__name__


def _join(prefix: str, key: str) -> str:
    if key == LIST_ITEM:
        return f"{prefix}{LIST_ITEM}"
    return f"{prefix}.{key}" if prefix else key


def shape(value: Any, prefix: str = "") -> dict[str, set[str]]:
    """Every path in ``value`` and the types seen there."""
    found: dict[str, set[str]] = {}

    def walk(node: Any, path: str) -> None:
        found.setdefault(path or BODY, set()).add(_type(node))
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, _join(path, str(key)))
        elif isinstance(node, list):
            for child in node:
                walk(child, _join(path, LIST_ITEM))

    walk(value, prefix)
    return found


def _pattern(glob: str) -> re.Pattern[str]:
    """``**.`` is any run of leading segments (or none), ``**`` anything,
    ``*`` one segment. A match also covers everything below the path."""
    regex = re.escape(glob)
    regex = regex.replace(r"\*\*\.", r"(?:.*\.)?").replace(r"\*\*", ".*")
    regex = regex.replace(r"\*", r"[^.\[\]]*")
    return re.compile(f"^{regex}(?:$|[.\\[].*$)")


@dataclass
class Rules:
    """What a comparison may ignore, and what it must match exactly."""

    # Real-only fields the fake deliberately leaves out (and their children).
    not_modelled: list[str] = field(default_factory=list)
    # Fields whose type or nullability changes from response to response.
    volatile: list[str] = field(default_factory=list)
    # Fields whose values must be equal.
    values: list[str] = field(default_factory=list)
    # Fields either side may have or lack, such as those a token's scope
    # decides; their types are still compared when both have them.
    optional: list[str] = field(default_factory=list)

    def _matches(self, patterns: Iterable[str], path: str) -> bool:
        return any(_pattern(p).match(path) for p in patterns)

    def ignored_absence(self, path: str) -> bool:
        return self._matches([*self.not_modelled, *self.optional], path)

    def ignored_presence(self, path: str) -> bool:
        return self._matches(self.optional, path)

    def ignored_type(self, path: str) -> bool:
        return self._matches(self.volatile, path)

    @classmethod
    def merge(cls, *rules: Rules) -> Rules:
        return cls(
            not_modelled=[p for r in rules for p in r.not_modelled],
            volatile=[p for r in rules for p in r.volatile],
            values=[p for r in rules for p in r.values],
            optional=[p for r in rules for p in r.optional],
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> Rules:
        raw = raw or {}
        return cls(
            not_modelled=list(raw.get("not_modelled", [])),
            volatile=list(raw.get("volatile", [])),
            values=list(raw.get("values", [])),
            optional=list(raw.get("optional", [])),
        )


@dataclass(frozen=True)
class Sample:
    """One response: its status, the headers worth comparing, and its body."""

    status: int
    headers: frozenset[str]
    body: Any


@dataclass(frozen=True)
class Difference:
    path: str
    problem: str

    def __str__(self) -> str:
        return f"`{self.path}`: {self.problem}"


def _get(value: Any, path: str) -> list[Any]:
    """Every value at ``path``; ``[]`` fans out over a list."""
    current = [value]
    for segment in re.findall(r"[^.\[\]]+|\[\]", path):
        nxt = []
        for node in current:
            if segment == LIST_ITEM and isinstance(node, list):
                nxt.extend(node)
            elif isinstance(node, dict) and segment in node:
                nxt.append(node[segment])
        current = nxt
    return current


def compare(
    real: Sample, fake: Sample, rules: Rules, headers: Iterable[str] = ()
) -> list[Difference]:
    found: list[Difference] = []
    if real.status != fake.status:
        found.append(Difference("(status)", f"real {real.status}, fake {fake.status}"))
    for name in headers:
        if (name.lower() in real.headers) != (name.lower() in fake.headers):
            where = "real only" if name.lower() in real.headers else "fake only"
            found.append(Difference(f"(header) {name}", where))

    real_shape, fake_shape = shape(real.body), shape(fake.body)
    for path in sorted(real_shape.keys() - fake_shape.keys()):
        if not rules.ignored_absence(path) and not _parent_absent(
            path, real_shape, fake_shape
        ):
            found.append(Difference(path, "missing from the fake"))
    for path in sorted(fake_shape.keys() - real_shape.keys()):
        if rules.ignored_presence(path):
            continue
        if not _parent_absent(path, fake_shape, real_shape):
            found.append(Difference(path, "the fake has it; the real service does not"))
    for path in sorted(real_shape.keys() & fake_shape.keys()):
        if rules.ignored_type(path):
            continue
        real_types, fake_types = real_shape[path], fake_shape[path]
        if real_types != fake_types:
            found.append(
                Difference(
                    path,
                    f"real {_types(real_types)}, fake {_types(fake_types)}",
                )
            )
    for path in rules.values:
        real_values, fake_values = _get(real.body, path), _get(fake.body, path)
        if real_values != fake_values:
            found.append(
                Difference(path, f"real {real_values!r}, fake {fake_values!r}")
            )
    return found


def _types(kinds: set[str]) -> str:
    return "/".join(sorted(kinds))


def _parent_absent(
    path: str, here: Mapping[str, Any], there: Mapping[str, Any]
) -> bool:
    """Report only the topmost missing field, not each of its children."""
    parent = re.sub(r"(\.[^.\[\]]+|\[\])$", "", path)
    return parent != path and parent in here and parent not in there
