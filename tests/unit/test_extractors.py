"""Tests for :mod:`core.extractors`."""

from core.extractors import (
    extract_duration,
    extract_location,
    extract_message,
    extract_names,
    extract_reminder_title,
    extract_song,
    extract_time,
    extract_time_string,
)


class TestExtractTime:
    def test_pm_conversion(self):
        assert extract_time("at 3:30 PM") == (15, 30)

    def test_am_with_no_minutes(self):
        assert extract_time("at 9 AM") == (9, 0)

    def test_midnight_handling(self):
        assert extract_time("at 12 AM") == (0, 0)

    def test_noon_handling(self):
        assert extract_time("at 12 PM") == (12, 0)


    def test_no_match(self):
        assert extract_time("no time here") == (None, None)


class TestExtractDuration:
    def test_simple_minutes(self):
        assert extract_duration("for 5 minutes") == 5

    def test_singular(self):
        assert extract_duration("1 minute timer") == 1

    def test_no_match(self):
        assert extract_duration("no duration") is None


class TestExtractTimeString:
    def test_returns_literal(self):
        assert extract_time_string("meet at 3:00 PM tomorrow") == "3:00 PM"

    def test_no_match(self):
        assert extract_time_string("no time") is None


class TestExtractNames:
    def test_after_send_to(self):
        assert "Alice" in extract_names("send a message to Alice")

    def test_after_call(self):
        assert "Bob" in extract_names("please call Bob")

    def test_ignores_lone_capitals(self):
        # "I" should not be returned because there is no preceding keyword.
        assert extract_names("I am here") == []


class TestExtractMessage:
    def test_after_saying(self):
        assert extract_message("text Alice saying hello world") == "hello world"

    def test_no_match(self):
        assert extract_message("just plain text") is None


class TestExtractLocation:
    def test_weather_in(self):
        assert extract_location("weather in Berlin") == "Berlin"

    def test_forecast_for(self):
        # Trailing punctuation is required to terminate the location capture.
        assert extract_location("forecast for Tokyo today.") == "Tokyo today"


class TestExtractSong:
    def test_strips_redundant_music_suffix(self):
        assert extract_song("play some jazz music") == "jazz"

    def test_keeps_classical_music(self):
        # "classical music" is canonical — the suffix is *part of* the genre.
        assert extract_song("play classical music") == "classical music"


class TestExtractReminderTitle:
    def test_remind_me_to(self):
        assert extract_reminder_title("remind me to buy milk") == "buy milk"

    def test_strips_leading_the(self):
        assert extract_reminder_title("remind me about the meeting") == "meeting"
