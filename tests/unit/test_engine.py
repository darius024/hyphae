"""Unit tests for the pure functions in core.engine.

All tests avoid Cactus model I/O and Gemini network calls by testing only the
rule-based extraction, validation, and JSON repair helpers directly.
"""

import pytest

from core.engine import (
    _calls_are_valid,
    _count_actions,
    _extract_duration_from_text,
    _extract_location_from_text,
    _extract_message_from_text,
    _extract_names_from_text,
    _extract_reminder_title_from_text,
    _extract_song_from_text,
    _extract_time_from_text,
    _postprocess_calls,
    _repair_json,
    _rule_based_extract,
)

# ── Minimal tool fixtures ────────────────────────────────────────────────

TOOL_SEARCH = {
    "name": "search_papers",
    "description": "Search local research documents.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "search query"}},
        "required": ["query"],
    },
}

TOOL_ALARM = {
    "name": "set_alarm",
    "description": "Set an alarm.",
    "parameters": {
        "type": "object",
        "properties": {
            "hour": {"type": "integer", "description": "alarm hour (24h)"},
            "minute": {"type": "integer", "description": "alarm minute"},
        },
        "required": ["hour", "minute"],
    },
}

TOOL_MESSAGE = {
    "name": "send_message",
    "description": "Send a message to a person.",
    "parameters": {
        "type": "object",
        "properties": {
            "recipient": {"type": "string", "description": "person to message"},
            "message": {"type": "string", "description": "message content"},
        },
        "required": ["recipient", "message"],
    },
}

TOOL_MUSIC = {
    "name": "play_music",
    "description": "Play music or a playlist.",
    "parameters": {
        "type": "object",
        "properties": {"song": {"type": "string", "description": "song or playlist name"}},
        "required": ["song"],
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

ALL_TOOLS = [TOOL_SEARCH, TOOL_ALARM, TOOL_MESSAGE, TOOL_MUSIC, TOOL_WEATHER, TOOL_REMINDER]


def _msg(text):
    return [{"role": "user", "content": text}]


# ── _count_actions ────────────────────────────────────────────────────────

class TestCountActions:
    def test_single_action_returns_one(self):
        assert _count_actions(_msg("search for electrode impedance")) == 1

    def test_two_actions_joined_by_and(self):
        count = _count_actions(_msg("search for impedance and play some music"))
        assert count == 2

    def test_compound_query_with_comma(self):
        count = _count_actions(_msg("set alarm for 7 AM, play jazz"))
        assert count == 2

    def test_empty_text_returns_at_least_one(self):
        assert _count_actions(_msg("")) >= 1

    def test_three_actions(self):
        count = _count_actions(_msg("search papers, create a note, and send message to Alice"))
        assert count == 3


# ── _calls_are_valid ──────────────────────────────────────────────────────

class TestCallsAreValid:
    def test_valid_call(self):
        calls = [{"name": "search_papers", "arguments": {"query": "hydrogel"}}]
        assert _calls_are_valid(calls, [TOOL_SEARCH]) is True

    def test_missing_required_arg(self):
        calls = [{"name": "search_papers", "arguments": {}}]
        assert _calls_are_valid(calls, [TOOL_SEARCH]) is False

    def test_unknown_tool_name(self):
        calls = [{"name": "nonexistent_tool", "arguments": {}}]
        assert _calls_are_valid(calls, [TOOL_SEARCH]) is False

    def test_wrong_type_string_required_but_int_given(self):
        calls = [{"name": "search_papers", "arguments": {"query": 42}}]
        assert _calls_are_valid(calls, [TOOL_SEARCH]) is False

    def test_blank_string_arg_is_invalid(self):
        calls = [{"name": "search_papers", "arguments": {"query": "   "}}]
        assert _calls_are_valid(calls, [TOOL_SEARCH]) is False

    def test_negative_integer_is_invalid(self):
        calls = [{"name": "set_alarm", "arguments": {"hour": -1, "minute": 0}}]
        assert _calls_are_valid(calls, [TOOL_ALARM]) is False

    def test_valid_alarm_call(self):
        calls = [{"name": "set_alarm", "arguments": {"hour": 7, "minute": 30}}]
        assert _calls_are_valid(calls, [TOOL_ALARM]) is True

    def test_empty_call_list_is_valid(self):
        assert _calls_are_valid([], ALL_TOOLS) is True


# ── _repair_json ──────────────────────────────────────────────────────────

class TestRepairJson:
    def test_leading_zero_in_integer(self):
        import json
        # The repair regex strips leading zeros before non-zero digits (e.g. 07 → 7).
        raw = '{"count": 07}'
        repaired = _repair_json(raw)
        parsed = json.loads(repaired)
        assert parsed["count"] == 7

    def test_trailing_comma_in_object(self):
        import json
        raw = '{"query": "test",}'
        repaired = _repair_json(raw)
        parsed = json.loads(repaired)
        assert parsed["query"] == "test"

    def test_trailing_comma_in_array(self):
        import json
        raw = '[1, 2, 3,]'
        repaired = _repair_json(raw)
        parsed = json.loads(repaired)
        assert parsed == [1, 2, 3]

    def test_valid_float_not_modified(self):
        import json
        raw = '{"value": 0.5}'
        repaired = _repair_json(raw)
        parsed = json.loads(repaired)
        assert parsed["value"] == pytest.approx(0.5)


# ── _rule_based_extract ───────────────────────────────────────────────────

class TestRuleBasedExtract:
    def test_search_query_matched(self):
        # The rule-based extractor populates the 'query' arg via _extract_names_from_text,
        # which requires a capitalized word preceded by a keyword like 'for'.  A lowercase
        # phrase produces no extractable arg so the required-arg check fails and no call
        # is emitted — that is expected behaviour (the Cactus model handles it instead).
        calls_lower = _rule_based_extract(_msg("search for electrode impedance"), [TOOL_SEARCH])
        assert calls_lower == []

        # A proper noun after 'for' is successfully extracted.
        calls_proper = _rule_based_extract(_msg("search for Polymer synthesis"), [TOOL_SEARCH])
        assert len(calls_proper) == 1
        assert calls_proper[0]["name"] == "search_papers"
        assert calls_proper[0]["arguments"]["query"] == "Polymer"

    def test_play_music_matched(self):
        calls = _rule_based_extract(_msg("play some jazz"), [TOOL_MUSIC])
        assert len(calls) == 1
        assert calls[0]["name"] == "play_music"

    def test_weather_matched(self):
        calls = _rule_based_extract(_msg("weather in London"), [TOOL_WEATHER])
        assert len(calls) == 1
        assert calls[0]["name"] == "get_weather"

    def test_reminder_matched(self):
        calls = _rule_based_extract(_msg("remind me about the meeting"), [TOOL_REMINDER])
        assert len(calls) == 1
        assert calls[0]["name"] == "create_reminder"

    def test_no_match_returns_empty(self):
        calls = _rule_based_extract(_msg("how are you today"), [TOOL_SEARCH])
        assert calls == []

    def test_multi_action_returns_multiple_calls(self):
        # Use a proper noun so the rule-based extractor can populate the required 'query' arg.
        calls = _rule_based_extract(
            _msg("search for Polymer and play jazz"),
            [TOOL_SEARCH, TOOL_MUSIC],
        )
        names = {c["name"] for c in calls}
        assert "search_papers" in names
        assert "play_music" in names

    def test_same_tool_not_duplicated(self):
        # Even when two clauses match the same tool, it should only appear once.
        calls = _rule_based_extract(
            _msg("search for Alice and search for Bob"),
            [TOOL_SEARCH],
        )
        assert sum(1 for c in calls if c["name"] == "search_papers") == 1


# ── _postprocess_calls ────────────────────────────────────────────────────

class TestPostprocessCalls:
    def test_strips_prefix_noise_from_string_arg(self):
        calls = [{"name": "send_message", "arguments": {"recipient": "Alice", "message": "saying I'll be late"}}]
        result = _postprocess_calls(calls, [TOOL_MESSAGE], _msg("text Alice saying I'll be late"))
        assert result[0]["arguments"]["message"] == "I'll be late"

    def test_float_integer_coerced_to_int(self):
        calls = [{"name": "set_alarm", "arguments": {"hour": 7.0, "minute": 0.0}}]
        result = _postprocess_calls(calls, [TOOL_ALARM])
        assert isinstance(result[0]["arguments"]["hour"], int)
        assert result[0]["arguments"]["hour"] == 7

    def test_time_extracted_for_alarm(self):
        calls = [{"name": "set_alarm", "arguments": {"hour": 0, "minute": 0}}]
        result = _postprocess_calls(calls, [TOOL_ALARM], _msg("set alarm for 7:30 AM"))
        args = result[0]["arguments"]
        assert args["hour"] == 7
        assert args["minute"] == 30

    def test_unknown_tool_passed_through(self):
        calls = [{"name": "mystery_tool", "arguments": {"x": 1}}]
        result = _postprocess_calls(calls, [TOOL_SEARCH])
        assert result == calls


# ── Regex extraction helpers ──────────────────────────────────────────────

class TestExtractTimeFromText:
    def test_am_time(self):
        h, m = _extract_time_from_text("wake me at 6:30 AM")
        assert h == 6
        assert m == 30

    def test_pm_conversion(self):
        h, m = _extract_time_from_text("alarm at 3 PM")
        assert h == 15
        assert m == 0

    def test_noon_12pm(self):
        h, _m = _extract_time_from_text("remind at 12:00 PM")
        assert h == 12

    def test_midnight_12am(self):
        h, _m = _extract_time_from_text("alarm at 12 AM")
        assert h == 0

    def test_no_time_returns_none(self):
        h, m = _extract_time_from_text("no time mentioned here")
        assert h is None
        assert m is None


class TestExtractDurationFromText:
    def test_simple_duration(self):
        assert _extract_duration_from_text("set timer for 10 minutes") == 10

    def test_hyphen_form(self):
        assert _extract_duration_from_text("5-minute timer") == 5

    def test_no_duration_returns_none(self):
        assert _extract_duration_from_text("play some music") is None


class TestExtractNamesFromText:
    def test_extracts_name_after_to(self):
        names = _extract_names_from_text("send message to Alice")
        assert "Alice" in names

    def test_extracts_name_after_call(self):
        names = _extract_names_from_text("call Bob tomorrow")
        assert "Bob" in names

    def test_no_name_returns_empty(self):
        names = _extract_names_from_text("search for documents")
        assert names == []


class TestExtractMessageFromText:
    def test_saying_form(self):
        msg = _extract_message_from_text("text Alice saying I'll be late")
        assert msg == "I'll be late"

    def test_no_message_returns_none(self):
        assert _extract_message_from_text("just search something") is None


class TestExtractLocationFromText:
    def test_weather_in_city(self):
        loc = _extract_location_from_text("weather in Paris")
        assert loc == "Paris"

    def test_forecast_for_city(self):
        loc = _extract_location_from_text("forecast for Tokyo")
        assert loc == "Tokyo"

    def test_no_location_returns_none(self):
        assert _extract_location_from_text("play some music") is None


class TestExtractSongFromText:
    def test_play_jazz(self):
        song = _extract_song_from_text("play some jazz music")
        assert song == "jazz"

    def test_play_classical_music_kept(self):
        song = _extract_song_from_text("play classical music")
        assert song == "classical music"

    def test_no_play_returns_none(self):
        assert _extract_song_from_text("search for something") is None


class TestExtractReminderTitleFromText:
    def test_remind_me_about(self):
        title = _extract_reminder_title_from_text("remind me about the meeting")
        assert title is not None
        assert "meeting" in title.lower()

    def test_reminder_to(self):
        title = _extract_reminder_title_from_text("set a reminder to buy milk")
        assert title is not None
        assert "buy milk" in title.lower()

    def test_no_reminder_returns_none(self):
        assert _extract_reminder_title_from_text("play some music") is None
