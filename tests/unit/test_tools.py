"""Unit tests for tool dispatch and execution."""

import os
import pytest
from core.tools import (
    execute_tool,
    ALL_TOOLS,
    LOCAL_ONLY_TOOLS,
    CLOUD_SAFE_TOOLS,
    TOOL_SEARCH_PAPERS,
    TOOL_CREATE_NOTE,
    TOOL_LIST_DOCUMENTS,
    TOOL_COMPARE_DOCUMENTS,
)


class TestToolRegistry:
    def test_all_tools_have_required_fields(self):
        for tool in ALL_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool
            assert "type" in tool["parameters"]
            assert "properties" in tool["parameters"]

    def test_local_and_cloud_partition_covers_all_tools(self):
        all_names = {t["name"] for t in ALL_TOOLS}
        covered = LOCAL_ONLY_TOOLS | CLOUD_SAFE_TOOLS
        assert all_names == covered

    def test_no_overlap_between_local_and_cloud(self):
        assert LOCAL_ONLY_TOOLS & CLOUD_SAFE_TOOLS == set()


class TestExecuteTool:
    def test_unknown_tool_returns_error(self):
        result = execute_tool("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    def test_create_note_writes_file(self, tmp_corpus):
        result = execute_tool("create_note", {
            "title": "Test observation",
            "content": "This is a test note.",
        })
        assert "saved" in result
        assert result["source"] == "local"
        assert os.path.exists(result["saved"])

        with open(result["saved"]) as f:
            content = f.read()
        assert "# Test observation" in content
        assert "This is a test note." in content

    def test_list_documents_returns_files(self, tmp_corpus):
        result = execute_tool("list_documents", {})
        assert result["source"] == "local"
        assert result["count"] == 2
        names = [d["name"] for d in result["documents"]]
        assert "battery_notes.txt" in names
        assert "polymer_log.txt" in names

    def test_list_documents_empty_corpus(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty_corpus"
        empty.mkdir()
        from core import tools as tools_mod
        monkeypatch.setattr(tools_mod, "CORPUS_DIR", str(empty))
        result = execute_tool("list_documents", {})
        assert result["count"] == 0

    def test_execute_tool_catches_exceptions(self):
        result = execute_tool("create_note", {})
        assert "error" in result


class TestReadDocument:
    def test_reads_existing_file(self, tmp_corpus):
        result = execute_tool("read_document", {"name": "battery_notes.txt"})
        assert result["source"] == "local"
        assert result["name"] == "battery_notes.txt"
        assert "Battery cycling" in result["content"]
        assert "truncated" in result
        assert "size_kb" in result

    def test_truncates_at_max_chars(self, tmp_corpus):
        result = execute_tool("read_document", {"name": "battery_notes.txt", "max_chars": 10})
        assert len(result["content"]) <= 10
        assert result["truncated"] is True

    def test_full_file_not_marked_truncated(self, tmp_corpus):
        result = execute_tool("read_document", {"name": "battery_notes.txt", "max_chars": 100000})
        assert result["truncated"] is False

    def test_missing_file_returns_error(self, tmp_corpus):
        result = execute_tool("read_document", {"name": "no_such_file.txt"})
        assert "error" in result

    def test_path_traversal_returns_error(self, tmp_corpus):
        # Path.name strips directory components, so ../secrets is normalised to "secrets"
        # which won't exist — the result must be an error, not a file outside the corpus.
        result = execute_tool("read_document", {"name": "../../../etc/passwd"})
        assert "error" in result


class TestSearchText:
    def test_finds_keyword_in_corpus(self, tmp_corpus):
        result = execute_tool("search_text", {"query": "FEC-3"})
        assert result["source"] == "local"
        assert result["count"] >= 1
        assert any("FEC-3" in m["paragraph"] for m in result["matches"])

    def test_returns_filename_for_citation(self, tmp_corpus):
        result = execute_tool("search_text", {"query": "capacity retention"})
        assert result["count"] >= 1
        assert all("name" in m for m in result["matches"])
        assert all(m["name"].endswith(".txt") for m in result["matches"])

    def test_absent_keyword_returns_empty(self, tmp_corpus):
        result = execute_tool("search_text", {"query": "xyzzy_not_in_corpus_9999"})
        assert result["count"] == 0
        assert result["matches"] == []

    def test_max_snippets_limit(self, tmp_corpus):
        result = execute_tool("search_text", {"query": "a", "max_snippets": 1})
        assert result["count"] <= 1


class TestCompareDocuments:
    def test_returns_comparison_key(self, tmp_corpus):
        # RAG model is unavailable in the test environment; the tool falls back
        # to a paragraph-level text comparison — the response shape must still be correct.
        result = execute_tool("compare_documents", {
            "doc_a": "battery_notes.txt",
            "doc_b": "polymer_log.txt",
            "topic": "temperature",
        })
        assert "comparison" in result
        assert result["source"] == "local"

    def test_missing_both_docs_still_responds(self, tmp_corpus):
        result = execute_tool("compare_documents", {
            "doc_a": "ghost_a.txt",
            "doc_b": "ghost_b.txt",
            "topic": "anything",
        })
        # No raw exception — execute_tool wraps errors into {"error": ...}
        # or the fallback returns the "no relevant content" message.
        assert "comparison" in result or "error" in result

    def test_missing_required_arg_returns_error(self):
        result = execute_tool("compare_documents", {"doc_a": "battery_notes.txt"})
        assert "error" in result

