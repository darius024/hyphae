"""Tests for the hybrid routing orchestrator in :mod:`core.engine`.

These tests exercise ``generate_hybrid`` *without* loading the Cactus model
or hitting the Gemini API.  We use queries the rule-based extractor handles
directly (alarms, reminders, weather) and patch the cloud / on-device paths
to assert the router's fallback chain is followed correctly.
"""
from __future__ import annotations

import core.engine as engine

# Tool schemas the rule-based extractor knows how to populate end-to-end.
TOOL_ALARM = {
    "name": "set_alarm",
    "description": "Set an alarm at a specific time.",
    "parameters": {
        "type": "object",
        "properties": {
            "hour": {"type": "integer", "description": "alarm hour (24h)"},
            "minute": {"type": "integer", "description": "alarm minute"},
        },
        "required": ["hour", "minute"],
    },
}

TOOL_WEATHER = {
    "name": "get_weather",
    "description": "Get weather for a city.",
    "parameters": {
        "type": "object",
        "properties": {"location": {"type": "string", "description": "city name or location"}},
        "required": ["location"],
    },
}

TOOL_REMINDER = {
    "name": "create_reminder",
    "description": "Create a reminder.",
    "parameters": {
        "type": "object",
        "properties": {"title": {"type": "string", "description": "reminder title"}},
        "required": ["title"],
    },
}


class TestRuleBasedFastPath:
    """When rule extraction yields enough valid calls, the router resolves on-device."""

    def test_single_alarm_resolves_on_device(self):
        result = engine.generate_hybrid(
            [{"role": "user", "content": "set an alarm for 7:30 AM"}],
            [TOOL_ALARM],
        )
        assert result["source"] == "on-device"
        assert len(result["function_calls"]) == 1
        call = result["function_calls"][0]
        assert call["name"] == "set_alarm"
        assert call["arguments"] == {"hour": 7, "minute": 30}

    def test_two_action_query_resolves_on_device(self):
        result = engine.generate_hybrid(
            [{
                "role": "user",
                "content": "set an alarm for 7 AM and remind me to buy milk",
            }],
            [TOOL_ALARM, TOOL_REMINDER],
        )
        assert result["source"] == "on-device"
        names = sorted(call["name"] for call in result["function_calls"])
        assert names == ["create_reminder", "set_alarm"]


class TestCloudFallback:
    """When local extraction fails, the router falls back to Gemini."""

    def test_falls_back_to_cloud_when_local_yields_nothing(self, monkeypatch):
        # Force generate_cactus to return no calls — simulating the model not
        # being available *and* rule-based failing on this query.
        monkeypatch.setattr(
            engine,
            "generate_cactus",
            lambda messages, tools: {
                "function_calls": [],
                "total_time_ms": 0,
                "confidence": 0,
            },
        )

        cloud_called = {"value": False}

        def fake_cloud(messages, tools):
            cloud_called["value"] = True
            return {
                "function_calls": [
                    {"name": "get_weather", "arguments": {"location": "Berlin"}}
                ],
                "total_time_ms": 1.0,
            }

        monkeypatch.setattr(engine, "generate_cloud", fake_cloud)

        # A query rule-based cannot solve — no recognised verb.
        result = engine.generate_hybrid(
            [{"role": "user", "content": "totally unstructured query xyzzy"}],
            [TOOL_WEATHER],
        )
        assert cloud_called["value"]
        assert result["source"] == "cloud (fallback)"
        assert result["function_calls"][0]["name"] == "get_weather"

    def test_returns_partial_local_when_cloud_raises(self, monkeypatch):
        """When both rule-based and cloud fail, return whatever local got."""
        monkeypatch.setattr(
            engine,
            "generate_cactus",
            lambda messages, tools: {
                "function_calls": [],
                "total_time_ms": 0,
                "confidence": 0,
            },
        )

        def boom(messages, tools):
            raise RuntimeError("network down")

        monkeypatch.setattr(engine, "generate_cloud", boom)

        result = engine.generate_hybrid(
            [{"role": "user", "content": "totally unstructured query xyzzy"}],
            [TOOL_WEATHER],
        )
        # Source must remain on-device — we never reached the cloud successfully.
        assert result["source"] == "on-device"
        assert isinstance(result["function_calls"], list)


class TestCloudOnlyMode:
    """CLOUD_ONLY=1 must skip the Cactus model but still use rule-based shortcut."""

    def test_cloud_only_takes_rule_based_shortcut_when_valid(self, monkeypatch):
        monkeypatch.setattr(engine, "CLOUD_ONLY", True)

        def must_not_be_called(messages, tools):
            raise AssertionError("generate_cactus should not run in CLOUD_ONLY mode")

        monkeypatch.setattr(engine, "generate_cactus", must_not_be_called)

        result = engine.generate_hybrid(
            [{"role": "user", "content": "set an alarm for 9 AM"}],
            [TOOL_ALARM],
        )
        assert result["source"] == "on-device"
        assert result["confidence"] == 1.0
        assert result["function_calls"][0]["name"] == "set_alarm"
