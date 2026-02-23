"""Unit tests for corpus ingestion."""

import os
import pytest
from pathlib import Path
from ingestion.corpus import add_file, add_directory, list_documents, remove_document


class TestAddFile:
    def test_add_txt_file(self, tmp_corpus, tmp_path):
        src = tmp_path / "notes.txt"
        src.write_text("Some research notes.")

        result = add_file(str(src))
        assert result is True
        assert (tmp_corpus / "notes.txt").exists()

    def test_add_nonexistent_file(self):
        result = add_file("/nonexistent/path/file.txt")
        assert result is False

    def test_skip_unsupported_format(self, tmp_corpus, tmp_path):
        src = tmp_path / "image.png"
        src.write_bytes(b"\x89PNG")

        result = add_file(str(src))
        assert result is False

    def test_custom_dest_name(self, tmp_corpus, tmp_path):
        src = tmp_path / "data.txt"
        src.write_text("Content here.")

        result = add_file(str(src), dest_name="custom_name.txt")
        assert result is True
        assert (tmp_corpus / "custom_name.txt").exists()


class TestAddDirectory:
    def test_add_directory_recursive(self, tmp_corpus, tmp_path):
        subdir = tmp_path / "papers"
        subdir.mkdir()
        (subdir / "paper1.txt").write_text("Paper one content.")
        (subdir / "paper2.md").write_text("Paper two content.")
        (subdir / "image.png").write_bytes(b"\x89PNG")

        count = add_directory(str(subdir))
        assert count == 2

    def test_add_empty_directory(self, tmp_corpus, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        count = add_directory(str(empty))
        assert count == 0


class TestListDocuments:
    def test_list_shows_existing_files(self, tmp_corpus, capsys):
        list_documents()
        captured = capsys.readouterr()
        assert "battery_notes.txt" in captured.out
        assert "polymer_log.txt" in captured.out

    def test_list_empty_corpus(self, tmp_path, capsys, monkeypatch):
        from ingestion import corpus as ingest_mod
        monkeypatch.setattr(ingest_mod, "CORPUS_DIR", str(tmp_path / "nonexistent"))
        list_documents()
        captured = capsys.readouterr()
        assert "empty" in captured.out.lower()


class TestRemoveDocument:
    def test_remove_existing_file(self, tmp_corpus, capsys):
        assert (tmp_corpus / "battery_notes.txt").exists()
        remove_document("battery_notes.txt")
        assert not (tmp_corpus / "battery_notes.txt").exists()

    def test_remove_nonexistent_file(self, tmp_corpus, capsys):
        remove_document("no_such_file.txt")
        captured = capsys.readouterr()
        assert "Not found" in captured.out
