"""Tests for :mod:`core.json_repair`."""

import json

from core.json_repair import repair_json


class TestLeadingZeros:
    def test_strips_leading_zero_from_integer(self):
        assert json.loads(repair_json('{"x": 007}')) == {"x": 7}

    def test_strips_multiple_leading_zeros(self):
        assert json.loads(repair_json('{"x": 0042}')) == {"x": 42}

    def test_preserves_floats_with_leading_zero(self):
        # 0.5 is a valid JSON literal — must not be touched.
        assert json.loads(repair_json('{"p": 0.5}')) == {"p": 0.5}

    def test_preserves_zero(self):
        # Standalone 0 stays as 0 because there is no [1-9] follow-up.
        assert json.loads(repair_json('{"x": 0}')) == {"x": 0}


class TestTrailingCommas:
    def test_strips_trailing_comma_in_object(self):
        assert json.loads(repair_json('{"a": 1,}')) == {"a": 1}

    def test_strips_trailing_comma_in_array(self):
        assert json.loads(repair_json('[1, 2, 3,]')) == [1, 2, 3]

    def test_preserves_non_trailing_comma(self):
        assert json.loads(repair_json('{"a": 1, "b": 2}')) == {"a": 1, "b": 2}


class TestIdempotence:
    def test_repair_is_idempotent(self):
        sample = '{"x": 007, "y": [1,]}'
        once = repair_json(sample)
        assert repair_json(once) == once
