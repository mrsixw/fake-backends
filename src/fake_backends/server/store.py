"""Seed state: the tokens a fake accepts, and the data it serves."""

import json
from collections.abc import Callable, Mapping
from typing import Any

import yaml

from fake_backends.server.constants import YAML_CONTENT_TYPES

__all__ = ["SeedError", "Store", "parse_seed"]


class SeedError(ValueError):
    """A seed document that cannot be loaded."""


class Store:
    """The fake's current state.

    ``tokens`` maps each accepted token to the identity it authenticates as.
    ``data`` is what ``parse`` (the backend's ``parse_state``) made of every
    other top-level seed key.
    """

    def __init__(self, parse: Callable[[Mapping[str, Any]], Any] = dict) -> None:
        self._parse = parse
        self.tokens: dict[str, str] = {}
        self.data: Any = parse({})

    def load(self, seed: Mapping[str, Any]) -> None:
        """Replace the state with ``seed``, or change nothing if it is invalid."""
        tokens = seed.get("tokens", {})
        if not isinstance(tokens, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in tokens.items()
        ):
            raise SeedError("seed 'tokens' must map token strings to identities")
        data = self._parse({k: v for k, v in seed.items() if k != "tokens"})
        self.tokens = dict(tokens)
        self.data = data

    def reset(self) -> None:
        self.tokens = {}
        self.data = self._parse({})


def parse_seed(body: bytes, content_type: str) -> Mapping[str, Any]:
    """Decode a seed document as YAML or JSON, by its content type."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    try:
        if media_type in YAML_CONTENT_TYPES:
            seed = yaml.safe_load(body)
        else:
            seed = json.loads(body)
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeDecodeError) as exc:
        raise SeedError(f"seed is not valid {media_type or 'JSON'}: {exc}") from exc
    if not isinstance(seed, Mapping):
        raise SeedError("seed must be a mapping at the top level")
    return seed
