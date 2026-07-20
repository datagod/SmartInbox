"""Delivery mode greeting tests."""

from __future__ import annotations

from unittest.mock import patch

from smartinbox.delivery_modes import (
    pick_name_greeting,
    prepend_name_greeting,
    time_greeting_templates,
)


def test_time_greeting_templates_morning():
    templates = time_greeting_templates(8)
    assert "Good morning {name}" in templates
    assert all("{name}" in t for t in templates)


def test_time_greeting_templates_evening():
    templates = time_greeting_templates(19)
    assert "Good evening {name}" in templates


def test_pick_name_greeting_uses_time_templates():
    with patch("smartinbox.delivery_modes.random.random", return_value=0.0):
        with patch(
            "smartinbox.delivery_modes.random.choice",
            return_value="Good morning {name}",
        ):
            result = pick_name_greeting("Bill", timezone="America/New_York")
    assert result == "Good morning Bill"


def test_prepend_name_greeting_disabled():
    assert prepend_name_greeting("New email.", "Bill", enabled=False) == "New email."


def test_prepend_name_greeting_enabled():
    with patch(
        "smartinbox.delivery_modes.pick_name_greeting",
        return_value="Good afternoon Bill",
    ):
        out = prepend_name_greeting(
            "New email from Alice.",
            "Bill",
            enabled=True,
            timezone="America/Toronto",
        )
    assert out == "Good afternoon Bill. New email from Alice."