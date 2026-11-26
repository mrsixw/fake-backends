"""Values the server core shares between modules."""

from http import HTTPStatus

__all__ = [
    "CONTROL_PREFIX",
    "HEALTH_PATH",
    "JSON_CONTENT_TYPE",
    "MALFORMED_JSON_BODY",
    "MAX_HTTP_STATUS",
    "MIN_HTTP_STATUS",
    "TEXT_CONTENT_TYPE",
    "TRUNCATE_KEEP_DIVISOR",
    "UNAUTHORIZED_BODY",
    "YAML_CONTENT_TYPES",
]

# Everything under this prefix configures the fake and is never faulted,
# journaled or authenticated.
CONTROL_PREFIX = "/_fake"
HEALTH_PATH = "/healthz"

JSON_CONTENT_TYPE = "application/json"
TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
YAML_CONTENT_TYPES = frozenset(
    {"application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml"}
)

# The range a canned response's status must fall in.
MIN_HTTP_STATUS = HTTPStatus.CONTINUE
MAX_HTTP_STATUS = 599

# A truncate action with no keep_bytes sends this fraction (1/N) of the body.
TRUNCATE_KEEP_DIVISOR = 2

# An unterminated object: valid UTF-8, never valid JSON.
MALFORMED_JSON_BODY = b'{"truncated": '

# GitHub's 401 body, the default for a backend that does not override it.
UNAUTHORIZED_BODY = {"message": "Bad credentials"}
