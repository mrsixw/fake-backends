"""The subset of JQL that grind sends, and nothing more.

grind builds its queries in ``JiraProvider._jql``: clauses joined with
``AND``, a source's own ``jql`` wrapped in parentheses, and ``ORDER BY``.
This parser accepts:

- ``project = X``, ``project != X``, ``project in (X, Y)``
- ``assignee`` and ``reporter``: ``= currentUser()``, ``= name``,
  ``is EMPTY``, ``is not EMPTY``
- ``statusCategory`` and ``status``: ``=``, ``!=``, ``in (...)``
- ``issuetype`` (or ``type``) and ``labels``: ``=``, ``in (...)``
- ``priority = X``, ``key = X``
- ``text ~ "words"`` and ``summary ~ "words"``
- ``AND`` and parentheses
- ``ORDER BY updated|created|key|priority|status [ASC|DESC], ...``

Anything else, ``OR`` and ``NOT`` included, is a ``JqlError``, which the API
answers with JIRA's 400. A query a new grind sends then fails loudly instead
of quietly matching nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from fake_backends.server.jira.model import CATEGORY_NAMES, PRIORITIES, Issue

__all__ = ["JqlError", "Query", "parse"]

Predicate = Callable[[Issue, str | None], bool]


class JqlError(ValueError):
    """A query the fake does not understand."""


_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<op>!=|!~|=|~|\(|\)|,)
      | (?P<word>[A-Za-z0-9_.\-@]+)
    )""",
    re.VERBOSE,
)
_ESCAPE = re.compile(r"\\(.)")


@dataclass
class _Token:
    kind: str  # "string", "op" or "word"
    text: str

    def is_word(self, *words: str) -> bool:
        return self.kind == "word" and self.text.lower() in words


def _tokens(text: str) -> list[_Token]:
    tokens, position = [], 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN.match(text, position)
        if match is None or match.end() == position:
            raise JqlError(
                f"Error in the JQL Query: unexpected character at {position}."
            )
        kind = match.lastgroup
        value = match.group(kind)
        if kind == "string":
            value = _ESCAPE.sub(r"\1", value[1:-1])
        tokens.append(_Token(kind, value))
        position = match.end()
    return tokens


@dataclass
class Query:
    predicate: Predicate
    order: list[tuple[str, bool]] = field(default_factory=list)  # (field, descending)
    bounded: bool = True

    def matches(self, issue: Issue, identity: str | None) -> bool:
        return self.predicate(issue, identity)


_ORDER_FIELDS = {"updated", "created", "key", "priority", "status"}
_FIELD_ALIASES = {"type": "issuetype", "statuscategory": "statuscategory"}
_SUPPORTED_FIELDS = {
    "project",
    "assignee",
    "reporter",
    "statuscategory",
    "status",
    "issuetype",
    "labels",
    "priority",
    "key",
    "text",
    "summary",
}
_CATEGORY_LOOKUP = {
    name.lower(): category for category, name in CATEGORY_NAMES.items()
} | {category.value: category for category in CATEGORY_NAMES}


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.position = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise JqlError("Error in the JQL Query: the query ended unexpectedly.")
        self.position += 1
        return token

    def expect_op(self, op: str) -> None:
        token = self.take()
        if token.kind != "op" or token.text != op:
            raise JqlError(
                f"Error in the JQL Query: expected '{op}' but found '{token.text}'."
            )

    def value(self) -> str:
        token = self.take()
        if token.kind == "op":
            raise JqlError(
                f"Error in the JQL Query: expected a value but found '{token.text}'."
            )
        return token.text

    def values(self) -> list[str]:
        self.expect_op("(")
        found = [self.value()]
        while (
            (token := self.peek()) is not None
            and token.kind == "op"
            and token.text == ","
        ):
            self.take()
            found.append(self.value())
        self.expect_op(")")
        return found

    # query := [expr] [ORDER BY ...]
    def query(self) -> Query:
        predicate: Predicate = lambda issue, identity: True  # noqa: E731
        bounded = False
        token = self.peek()
        if token is not None and not token.is_word("order"):
            predicate = self.expr()
            bounded = True
        order = self.order_by()
        if self.peek() is not None:
            raise self.unsupported(self.peek())
        return Query(predicate, order, bounded)

    def unsupported(self, token: _Token) -> JqlError:
        if token.is_word("or", "not"):
            return JqlError(
                "Error in the JQL Query: fake-jira does not support "
                f"'{token.text.upper()}'. "
                "It understands the clauses grind sends, joined with AND."
            )
        return JqlError(f"Error in the JQL Query: unexpected '{token.text}'.")

    def expr(self) -> Predicate:
        parts = [self.term()]
        while (token := self.peek()) is not None and token.is_word("and"):
            self.take()
            parts.append(self.term())
        return lambda issue, identity: all(p(issue, identity) for p in parts)

    def term(self) -> Predicate:
        token = self.peek()
        if token is not None and token.kind == "op" and token.text == "(":
            self.take()
            inner = self.expr()
            self.expect_op(")")
            return inner
        return self.clause()

    def clause(self) -> Predicate:
        token = self.take()
        if token.kind != "word":
            raise self.unsupported(token)
        name = _FIELD_ALIASES.get(token.text.lower(), token.text.lower())
        if name not in _SUPPORTED_FIELDS:
            raise JqlError(
                f"Field '{token.text}' does not exist or you do not have "
                "permission to view it."
            )
        operator = self.take()
        if operator.is_word("in"):
            return self.membership(name, self.values())
        if operator.is_word("is"):
            negate = False
            if (nxt := self.peek()) is not None and nxt.is_word("not"):
                self.take()
                negate = True
            if not self.take().is_word("empty", "null"):
                raise JqlError("Error in the JQL Query: expected EMPTY after IS.")
            return self.emptiness(name, negate)
        if operator.kind != "op" or operator.text not in {"=", "!=", "~"}:
            raise self.unsupported(operator)
        if operator.text == "~":
            if name not in {"text", "summary"}:
                raise JqlError(
                    f"Error in the JQL Query: '~' is not supported for {name}."
                )
            return self.contains(name, self.value())
        if name in {"text", "summary"}:
            raise JqlError(f"Error in the JQL Query: {name} only supports '~'.")
        if self.function_follows():
            return self.current_user(name, negate=operator.text == "!=")
        predicate = self.membership(name, [self.value()])
        if operator.text == "!=":
            return lambda issue, identity: not predicate(issue, identity)
        return predicate

    def function_follows(self) -> bool:
        token = self.peek()
        following = (
            self.tokens[self.position + 1]
            if self.position + 1 < len(self.tokens)
            else None
        )
        return (
            token is not None
            and token.is_word("currentuser")
            and following is not None
            and following.kind == "op"
            and following.text == "("
        )

    def current_user(self, name: str, *, negate: bool) -> Predicate:
        self.take()
        self.expect_op("(")
        self.expect_op(")")
        if name not in {"assignee", "reporter"}:
            raise JqlError(
                f"Error in the JQL Query: currentUser() does not apply to {name}."
            )

        def test(issue: Issue, identity: str | None) -> bool:
            person = getattr(issue, name)
            same = (
                person is not None
                and identity is not None
                and person.lower() == identity.lower()
            )
            return same != negate

        return test

    def emptiness(self, name: str, negate: bool) -> Predicate:
        if name not in {"assignee", "reporter", "labels"}:
            raise JqlError(
                f"Error in the JQL Query: IS EMPTY is not supported for {name}."
            )
        return lambda issue, identity: (not getattr(issue, name)) != negate

    def membership(self, name: str, raw: list[str]) -> Predicate:
        wanted = {value.lower() for value in raw}
        if name == "statuscategory":
            unknown = sorted(wanted - _CATEGORY_LOOKUP.keys())
            if unknown:
                raise JqlError(
                    f"The value '{unknown[0]}' does not exist for the field "
                    "'statusCategory'."
                )
            categories = {_CATEGORY_LOOKUP[value] for value in wanted}
            return lambda issue, identity: issue.status.category in categories
        if name == "priority":
            known = {p.lower() for p in PRIORITIES}
            if not wanted <= known:
                raise JqlError(
                    f"The value '{raw[0]}' does not exist for the field 'priority'."
                )
        getters: dict[str, Callable[[Issue], set[str]]] = {
            "project": lambda i: {i.project.lower()},
            "assignee": lambda i: {i.assignee.lower()} if i.assignee else set(),
            "reporter": lambda i: {i.reporter.lower()},
            "status": lambda i: {i.status.name.lower()},
            "issuetype": lambda i: {i.type.lower()},
            "labels": lambda i: {label.lower() for label in i.labels},
            "priority": lambda i: {i.priority.lower()},
            "key": lambda i: {i.key.lower()},
        }
        get = getters[name]
        return lambda issue, identity: bool(get(issue) & wanted)

    def contains(self, name: str, text: str) -> Predicate:
        words = text.lower().split()

        def test(issue: Issue, identity: str | None) -> bool:
            haystack = (issue.summary if name == "summary" else issue.text()).lower()
            return all(word in haystack for word in words)

        return test

    def order_by(self) -> list[tuple[str, bool]]:
        token = self.peek()
        if token is None:
            return []
        if not token.is_word("order"):
            raise self.unsupported(token)
        self.take()
        if not self.take().is_word("by"):
            raise JqlError("Error in the JQL Query: expected BY after ORDER.")
        order = [self.order_field()]
        while (
            (token := self.peek()) is not None
            and token.kind == "op"
            and token.text == ","
        ):
            self.take()
            order.append(self.order_field())
        return order

    def order_field(self) -> tuple[str, bool]:
        token = self.take()
        name = token.text.lower()
        if token.kind != "word" or name not in _ORDER_FIELDS:
            raise JqlError(f"Error in the JQL Query: cannot order by '{token.text}'.")
        descending = False
        if (nxt := self.peek()) is not None and nxt.is_word("asc", "desc"):
            descending = self.take().text.lower() == "desc"
        return name, descending


def parse(text: str) -> Query:
    return _Parser(text).query()
