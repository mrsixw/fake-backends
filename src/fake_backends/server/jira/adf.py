"""Atlassian Document Format: the JSON bodies JIRA Cloud's v3 API speaks.

Only what a fake needs: recognise a document, flatten one to text for
searching, and build one from a seed's plain text. A document a client sends
is stored and returned exactly as sent, so ADF round-trips untouched.
"""

from __future__ import annotations

from typing import Any

__all__ = ["ADF_VERSION", "adf_to_text", "is_adf", "text_to_adf"]

ADF_VERSION = 1


def is_adf(value: Any) -> bool:
    """Whether ``value`` is an ADF document at the top level."""
    return (
        isinstance(value, dict)
        and value.get("type") == "doc"
        and value.get("version") == ADF_VERSION
        and isinstance(value.get("content"), list)
    )


def text_to_adf(text: str) -> dict[str, Any]:
    """One paragraph per blank-line-separated block, hard breaks within it."""
    paragraphs = []
    for block in text.split("\n\n"):
        content: list[dict[str, Any]] = []
        for i, line in enumerate(block.split("\n")):
            if i:
                content.append({"type": "hardBreak"})
            if line:
                content.append({"type": "text", "text": line})
        paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": ADF_VERSION, "content": paragraphs}


def adf_to_text(node: Any) -> str:
    """The text of a document, for ``text ~`` searches. Marks are dropped."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return str(node.get("attrs", {}).get("text", ""))
    children = [adf_to_text(child) for child in node.get("content", [])]
    separator = "\n\n" if kind == "doc" else ""
    return separator.join(children)
