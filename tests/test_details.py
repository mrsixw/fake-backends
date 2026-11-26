"""A backend's details must survive the trip to the journal as JSON."""

import pytest

from fake_backends.server.middleware import checked_details


def test_json_details_are_kept():
    details = {"word": "x", "fields": ["a"], "count": 1, "nested": {"ok": True}}
    assert checked_details(details, "echo") == details


def test_tuples_become_lists():
    assert checked_details({"fields": ("a", "b")}, "echo") == {"fields": ["a", "b"]}


@pytest.mark.parametrize("bad", [{"s": {"a"}}, {"b": b"bytes"}, {"o": object()}])
def test_non_json_details_name_the_backend(bad):
    with pytest.raises(TypeError, match="echo"):
        checked_details(bad, "echo")
