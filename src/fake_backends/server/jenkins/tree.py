"""Jenkins' ``tree`` query parameter: pick fields, nest, and slice arrays.

``tree=jobs[name,builds[number,result]{0,5}],description`` keeps those fields
only. ``{M,N}`` slices the array it follows, as Jenkins does: ``{N}`` is the
element at index N, ``{M,}`` from M on, ``{,N}`` and ``{M,N}`` up to N.
``_class`` is always kept, and an object named without a sub-tree keeps only
its ``_class``, as on a real Jenkins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["Field", "TreeError", "apply", "parse"]

CLASS_KEY = "_class"


class TreeError(ValueError):
    """A tree expression that does not parse."""


@dataclass
class Field:
    name: str
    children: list[Field] | None = None
    start: int | None = None
    stop: int | None = None
    sliced: bool = field(default=False)


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text.replace(" ", "")
        self.at = 0

    def peek(self) -> str:
        return self.text[self.at] if self.at < len(self.text) else ""

    def take(self, char: str) -> None:
        if self.peek() != char:
            raise TreeError(f"expected '{char}' at {self.at} in tree {self.text!r}")
        self.at += 1

    def fields(self) -> list[Field]:
        found = [self.field()]
        while self.peek() == ",":
            self.at += 1
            found.append(self.field())
        return found

    def name(self) -> str:
        start = self.at
        while self.peek() and self.peek() not in ",[]{}":
            self.at += 1
        if self.at == start:
            raise TreeError(f"expected a field name at {start} in tree {self.text!r}")
        return self.text[start : self.at]

    def number(self) -> int | None:
        start = self.at
        while self.peek().isdigit():
            self.at += 1
        return int(self.text[start : self.at]) if self.at > start else None

    def field(self) -> Field:
        found = Field(self.name())
        if self.peek() == "[":
            self.at += 1
            found.children = self.fields()
            self.take("]")
        if self.peek() == "{":
            self.at += 1
            first = self.number()
            if self.peek() == ",":
                self.at += 1
                found.start, found.stop = first, self.number()
            else:
                if first is None:
                    raise TreeError(f"empty range in tree {self.text!r}")
                found.start, found.stop = first, first + 1
            found.sliced = True
            self.take("}")
        return found


def parse(text: str) -> list[Field]:
    parser = _Parser(text)
    fields = parser.fields()
    if parser.peek():
        raise TreeError(f"unexpected '{parser.peek()}' in tree {text!r}")
    return fields


def _shape(value: Any, spec: Field) -> Any:
    if isinstance(value, list):
        if spec.sliced:
            value = value[spec.start : spec.stop]
        return [_shape(item, spec) for item in value]
    if isinstance(value, dict):
        if spec.children is None:
            return {CLASS_KEY: value[CLASS_KEY]} if CLASS_KEY in value else {}
        return apply(value, spec.children)
    return value


def apply(value: dict[str, Any], fields: list[Field]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    if CLASS_KEY in value:
        kept[CLASS_KEY] = value[CLASS_KEY]
    for spec in fields:
        if spec.name in value:
            kept[spec.name] = _shape(value[spec.name], spec)
    return kept
