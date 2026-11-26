"""The helper must never import the server: see ADR 0003.

The server needs a newer Python than the consumers run, so an import would
break every consumer. The helper's own environment does not install the
server either, so an import would also fail these tests outright; this check
names the offending line instead.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"
FORBIDDEN = "fake_backends.server"
# The helper modules live in fake_backends.testing; relative imports in them
# resolve against it.
HELPER_PACKAGE = "fake_backends.testing"


def _is_forbidden(module: str) -> bool:
    return module == FORBIDDEN or module.startswith(f"{FORBIDDEN}.")


def server_imports(source: str, package: str = HELPER_PACKAGE) -> Iterator[int]:
    """The line of every import of the server in ``source``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(_is_forbidden(alias.name) for alias in node.names):
                yield node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            # `from fake_backends import server` names the server as an alias.
            targets = [module] + [f"{module}.{alias.name}" for alias in node.names]
            if any(_is_forbidden(target) for target in targets):
                yield node.lineno


@pytest.mark.parametrize(
    "source",
    [
        "import fake_backends.server",
        "import fake_backends.server.app as app",
        "from fake_backends.server import create_app",
        "from fake_backends.server.constants import HEALTH_PATH",
        "from fake_backends import server",
        "from .. import server",
        "from ..server import app",
        "def f():\n    from fake_backends import server",
    ],
)
def test_the_check_catches_each_form_of_server_import(source):
    assert list(server_imports(source))


@pytest.mark.parametrize(
    "source",
    [
        "import fake_backends.testing",
        "from fake_backends.testing import client",
        "from . import client",
        "from fake_backends import testing",
        "import fake_backends_server",
    ],
)
def test_the_check_allows_everything_else(source):
    assert list(server_imports(source)) == []


def test_no_helper_module_imports_the_server():
    offenders = [
        f"{path.relative_to(SOURCE)}:{line}"
        for path in sorted(SOURCE.rglob("*.py"))
        for line in server_imports(path.read_text())
    ]
    assert offenders == []


def test_the_check_sees_the_helper_source():
    assert any(SOURCE.rglob("plugin.py"))
