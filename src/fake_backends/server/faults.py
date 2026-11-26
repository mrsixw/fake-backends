"""Fault rules: what to match, when to fire, and what to do instead."""

import fnmatch
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fake_backends.server.constants import MAX_HTTP_STATUS, MIN_HTTP_STATUS

__all__ = [
    "Action",
    "ActionType",
    "FaultBook",
    "FireOn",
    "Rule",
    "RuleError",
    "parse_rule",
]

# Match keys the core understands; any other key is a backend detail.
_CORE_MATCH_KEYS = frozenset({"method", "path", "identity"})


class RuleError(ValueError):
    """A fault rule that cannot be registered."""


class ActionType(StrEnum):
    RESPOND = "respond"
    DELAY = "delay"
    DROP = "drop"
    TRUNCATE = "truncate"
    MALFORMED_JSON = "malformed_json"
    HOOK = "hook"


class FireOn(StrEnum):
    ALWAYS = "always"
    NTH = "nth"
    AFTER = "after"


@dataclass(frozen=True)
class Action:
    type: ActionType
    spec: Mapping[str, Any]
    delay_seconds: float = 0.0


@dataclass
class Rule:
    id: int
    match: Mapping[str, Any]
    fire_on: FireOn
    n: int | None
    cap: int | None
    action: Action
    matched: int = 0
    fired: int = 0
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def spent(self) -> bool:
        return self.cap is not None and self.fired >= self.cap

    def matches(
        self, method: str, path: str, identity: str | None, details: Mapping
    ) -> bool:
        for key, wanted in self.match.items():
            if key == "method":
                if method.upper() != str(wanted).upper():
                    return False
            elif key == "path":
                if not fnmatch.fnmatchcase(path, str(wanted)):
                    return False
            elif key == "identity":
                if identity != wanted:
                    return False
            else:
                actual = details.get(key)
                if isinstance(actual, list | tuple | set | frozenset):
                    if wanted not in actual:
                        return False
                elif actual != wanted:
                    return False
        return True

    def would_fire(self) -> bool:
        """Whether this rule fires on the request it has just counted."""
        if self.spent:
            return False
        if self.fire_on is FireOn.NTH:
            return self.matched == self.n
        if self.fire_on is FireOn.AFTER:
            return self.matched > self.n
        return True

    def describe(self) -> dict[str, Any]:
        return {
            **self.raw,
            "id": self.id,
            "matched": self.matched,
            "fired": self.fired,
            "spent": self.spent,
        }


def _positive_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuleError(f"{what} must be a positive integer")
    return value


def _non_negative_number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise RuleError(f"{what} must be a non-negative number")
    return float(value)


def _parse_action(raw: Any, supports_hook: Callable[[Mapping], bool]) -> Action:
    if not isinstance(raw, Mapping):
        raise RuleError("rule 'action' must be a mapping")
    try:
        kind = ActionType(raw.get("type"))
    except ValueError as exc:
        known = ", ".join(t.value for t in ActionType)
        raise RuleError(f"action 'type' must be one of: {known}") from exc
    delay = _non_negative_number(raw.get("delay_seconds", 0), "delay_seconds")
    match kind:
        case ActionType.RESPOND:
            status = raw.get("status")
            if (
                isinstance(status, bool)
                or not isinstance(status, int)
                or not MIN_HTTP_STATUS <= status <= MAX_HTTP_STATUS
            ):
                raise RuleError("respond needs an HTTP 'status'")
            headers = raw.get("headers", {})
            if not isinstance(headers, Mapping):
                raise RuleError("respond 'headers' must be a mapping")
        case ActionType.DELAY:
            if "seconds" not in raw:
                raise RuleError("delay needs 'seconds'")
            delay += _non_negative_number(raw["seconds"], "seconds")
        case ActionType.TRUNCATE:
            if "keep_bytes" in raw:
                keep = raw["keep_bytes"]
                if isinstance(keep, bool) or not isinstance(keep, int) or keep < 0:
                    raise RuleError("keep_bytes must be a non-negative integer")
        case ActionType.HOOK:
            if not supports_hook(raw):
                raise RuleError(f"this backend has no hook action {raw.get('name')!r}")
    return Action(type=kind, spec=raw, delay_seconds=delay)


def parse_rule(
    raw: Any, rule_id: int, supports_hook: Callable[[Mapping], bool]
) -> Rule:
    """Validate a rule document and build the Rule it describes."""
    if not isinstance(raw, Mapping):
        raise RuleError("a rule must be a mapping")
    match = raw.get("match", {})
    if not isinstance(match, Mapping):
        raise RuleError("rule 'match' must be a mapping")
    if "action" not in raw:
        raise RuleError("a rule needs an 'action'")
    fire = raw.get("fire", {})
    if not isinstance(fire, Mapping):
        raise RuleError("rule 'fire' must be a mapping")
    try:
        fire_on = FireOn(fire.get("on", FireOn.ALWAYS))
    except ValueError as exc:
        known = ", ".join(f.value for f in FireOn)
        raise RuleError(f"fire 'on' must be one of: {known}") from exc
    n = None
    if fire_on is not FireOn.ALWAYS:
        n = _positive_int(fire.get("n"), f"fire '{fire_on}' needs 'n', which")
    cap = _positive_int(fire["cap"], "cap") if "cap" in fire else None
    action = _parse_action(raw["action"], supports_hook)
    return Rule(
        id=rule_id,
        match=dict(match),
        fire_on=fire_on,
        n=n,
        cap=cap,
        action=action,
        raw=dict(raw),
    )


class FaultBook:
    """The registered rules, in registration order."""

    def __init__(self) -> None:
        self._rules: list[Rule] = []
        self._next_id = 1
        self._lock = threading.Lock()

    def add(self, raw: Any, supports_hook: Callable[[Mapping], bool]) -> Rule:
        with self._lock:
            rule = parse_rule(raw, self._next_id, supports_hook)
            self._next_id += 1
            self._rules.append(rule)
            return rule

    def remove(self, rule_id: int) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.id != rule_id]
            return len(self._rules) != before

    def clear(self) -> None:
        with self._lock:
            self._rules = []

    def describe(self) -> list[dict[str, Any]]:
        with self._lock:
            return [rule.describe() for rule in self._rules]

    def decide(
        self, method: str, path: str, identity: str | None, details: Mapping
    ) -> Rule | None:
        """Count the request against every matching rule; return the winner.

        Every matching rule counts the request. The earliest registered rule
        that fires on it wins, and only the winner's fired count goes up.
        """
        with self._lock:
            winner = None
            for rule in self._rules:
                if not rule.matches(method, path, identity, details):
                    continue
                rule.matched += 1
                if winner is None and rule.would_fire():
                    winner = rule
            if winner is not None:
                winner.fired += 1
            return winner
