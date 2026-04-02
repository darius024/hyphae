"""Unit tests for web/routes/corpus.py — pure utility helpers.

Covers _safe_name: the filename validation guard used before any
file-system operation on the corpus directory.
"""

from __future__ import annotations

import pytest


class TestSafeName:
    """Tests for routes.corpus._safe_name."""

    def _fn(self):
        from routes.corpus import _safe_name
        return _safe_name

    def test_valid_filename_returned_unchanged(self):
        assert self._fn()("report.txt") == "report.txt"

    def test_valid_pdf_filename(self):
        assert self._fn()("my_study.pdf") == "my_study.pdf"

    def test_valid_filename_with_numbers(self):
        assert self._fn()("data2024.csv") == "data2024.csv"

    def test_rejects_path_traversal_dotdot(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._fn()("../../etc/passwd")
        assert exc_info.value.status_code == 400

    def test_rejects_dotdot_in_middle(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn()("subdir/../secret.txt")

    def test_rejects_empty_string(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._fn()("")
        assert exc_info.value.status_code == 400

    def test_rejects_subdirectory_path(self):
        """A name containing a directory separator must be rejected."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn()("subdir/file.txt")

    def test_rejects_null_byte(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn()("file\x00.txt")

    def test_rejects_control_characters(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn()("file\x1f.txt")

    def test_rejects_windows_reserved_con(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn()("CON.txt")

    def test_rejects_windows_reserved_nul(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn()("NUL")

    def test_rejects_windows_reserved_com1(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn()("COM1.log")

    def test_rejects_backslash(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn__inner("dir\\file.txt")

    def _fn__inner(self, name: str):
        """Alias to avoid attribute collision in test method."""
        from routes.corpus import _safe_name
        return _safe_name(name)

    def test_rejects_colon(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._fn__inner("C:file.txt")
