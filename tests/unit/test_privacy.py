"""Unit tests for privacy sanitisation."""

import pytest
from core.privacy import sanitise_for_cloud, is_cloud_safe, filter_tools_for_cloud, filter_tools_for_local
from core.tools import ALL_TOOLS, LOCAL_ONLY_TOOLS, CLOUD_SAFE_TOOLS


class TestSanitiseForCloud:
    def test_strips_file_paths(self):
        messages = [{"role": "user", "content": "Check /data/experiment_1.csv for results"}]
        result = sanitise_for_cloud(messages)
        assert "/data/experiment_1.csv" not in result[0]["content"]
        assert "[PATH]" in result[0]["content"]

    def test_strips_measurements(self):
        messages = [{"role": "user", "content": "The sample measured 3.5 mg at 25.0 °C"}]
        result = sanitise_for_cloud(messages)
        assert "3.5 mg" not in result[0]["content"]

    def test_strips_sample_ids(self):
        messages = [{"role": "user", "content": "Check sample #A42 and batch B-17"}]
        result = sanitise_for_cloud(messages)
        assert "sample #A42" not in result[0]["content"]

    def test_strips_lab_codes(self):
        messages = [{"role": "user", "content": "Results from AB-1234 look promising"}]
        result = sanitise_for_cloud(messages)
        assert "AB-1234" not in result[0]["content"]

    def test_preserves_normal_text(self):
        messages = [{"role": "user", "content": "What causes polymer degradation over time?"}]
        result = sanitise_for_cloud(messages)
        assert result[0]["content"] == "What causes polymer degradation over time?"

    def test_preserves_role_field(self):
        messages = [{"role": "system", "content": "You are a helpful assistant."}]
        result = sanitise_for_cloud(messages)
        assert result[0]["role"] == "system"

    def test_strips_email_addresses(self):
        messages = [{"role": "user", "content": "Contact researcher at jane.doe@labcorp.org"}]
        result = sanitise_for_cloud(messages)
        assert "jane.doe@labcorp.org" not in result[0]["content"]
        assert "[EMAIL]" in result[0]["content"]

    def test_strips_urls(self):
        messages = [{"role": "user", "content": "Data at https://internal.lab.io/exp/42"}]
        result = sanitise_for_cloud(messages)
        assert "https://internal.lab.io/exp/42" not in result[0]["content"]

    def test_strips_ip_addresses(self):
        messages = [{"role": "user", "content": "Server at 192.168.1.42 has the data"}]
        result = sanitise_for_cloud(messages)
        assert "192.168.1.42" not in result[0]["content"]

    def test_strips_dates(self):
        messages = [{"role": "user", "content": "Experiment run on 2025-03-15"}]
        result = sanitise_for_cloud(messages)
        assert "2025-03-15" not in result[0]["content"]

    def test_strips_gps_coordinates(self):
        messages = [{"role": "user", "content": "Field site at 51.5074, -0.1278"}]
        result = sanitise_for_cloud(messages)
        assert "51.5074, -0.1278" not in result[0]["content"]

    def test_does_not_mutate_original(self):
        messages = [{"role": "user", "content": "Check /data/file.txt"}]
        sanitise_for_cloud(messages)
        assert "/data/file.txt" in messages[0]["content"]


class TestCloudSafety:
    def test_local_only_tools_not_cloud_safe(self):
        for name in LOCAL_ONLY_TOOLS:
            assert not is_cloud_safe(name)

    def test_cloud_safe_tools_are_safe(self):
        for name in CLOUD_SAFE_TOOLS:
            assert is_cloud_safe(name)

    def test_filter_tools_for_cloud(self):
        cloud_tools = filter_tools_for_cloud(ALL_TOOLS)
        cloud_names = {t["name"] for t in cloud_tools}
        assert cloud_names == CLOUD_SAFE_TOOLS

    def test_filter_tools_for_local(self):
        local_tools = filter_tools_for_local(ALL_TOOLS)
        local_names = {t["name"] for t in local_tools}
        assert local_names == LOCAL_ONLY_TOOLS
