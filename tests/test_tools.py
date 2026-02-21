"""Unit tests for tool dispatch and execution."""

import os
import pytest
from tools import (
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
        import tools
        monkeypatch.setattr(tools, "CORPUS_DIR", str(empty))
        result = execute_tool("list_documents", {})
        assert result["count"] == 0

    def test_execute_tool_catches_exceptions(self):
        result = execute_tool("create_note", {})
        assert "error" in result
