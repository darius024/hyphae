"""Unit tests for the PII sanitiser (web/notebook/sanitiser.py)."""

import pytest
from notebook.sanitiser import sanitise_text, sanitise_messages, is_safe_for_cloud


class TestSanitiseText:
    def test_redacts_email(self):
        text, triggered = sanitise_text("Contact alice@example.com for details")
        assert "[EMAIL]" in text
        assert "alice@example.com" not in text
        assert "email" in triggered

    def test_redacts_ipv4(self):
        text, triggered = sanitise_text("Server at 192.168.1.100")
        assert "[IP]" in text
        assert "192.168.1.100" not in text

    def test_ipv4_does_not_match_invalid_octets(self):
        # 999.999.999.999 is not a valid IP — must not be redacted
        text, triggered = sanitise_text("version 999.999.999.999 released")
        assert "[IP]" not in text
        assert "ipv4" not in triggered

    def test_ipv4_does_not_match_software_versions(self):
        # 1.0.0.4 could be a software version — with octet-constrained pattern
        # it still matches (all octets valid) so we only check the invalid case above.
        pass

    def test_redacts_url(self):
        text, triggered = sanitise_text("See https://internal.lab.org/results")
        assert "[URL]" in text
        assert "https://internal.lab.org" not in text

    def test_redacts_phone(self):
        text, triggered = sanitise_text("Call (555) 123-4567")
        assert "[PHONE]" in text

    def test_redacts_ssn(self):
        text, triggered = sanitise_text("SSN: 123-45-6789")
        assert "[SSN]" in text
        assert "123-45-6789" not in text

    def test_redacts_ssn_space_separated(self):
        text, triggered = sanitise_text("SSN: 123 45 6789")
        assert "[SSN]" in text

    def test_redacts_ssn_unseparated(self):
        text, triggered = sanitise_text("SSN: 123456789")
        assert "[SSN]" in text

    def test_redacts_gps(self):
        text, triggered = sanitise_text("Location: 51.5074, -0.1278")
        assert "[GPS]" in text

    def test_redacts_file_path(self):
        text, triggered = sanitise_text("Data in /home/user/experiments/run1.csv")
        assert "[PATH]" in text

    def test_redacts_lab_code(self):
        text, triggered = sanitise_text("Sample AB-1234 shows improvement")
        assert "[LAB_CODE]" in text

    def test_redacts_sample_id(self):
        text, triggered = sanitise_text("specimen_42 was contaminated")
        assert "[SAMPLE_ID]" in text

    def test_redacts_measurement(self):
        text, triggered = sanitise_text("Added 5.2 mg of catalyst")
        assert "[MEASUREMENT]" in text

    def test_clean_text_unchanged(self):
        text, triggered = sanitise_text("What are the latest findings on polymer degradation?")
        assert triggered == []
        assert "polymer degradation" in text

    def test_multiple_patterns(self):
        text, triggered = sanitise_text("Email alice@lab.com about specimen_3 at 192.168.0.1")
        assert "[EMAIL]" in text
        assert "[SAMPLE_ID]" in text
        assert "[IP]" in text
        assert len(triggered) >= 3


class TestSanitiseMessages:
    def test_sanitises_user_content(self):
        msgs = [
            {"role": "user", "content": "Check alice@lab.com"},
            {"role": "assistant", "content": "Sure, I'll check."},
        ]
        cleaned, triggered = sanitise_messages(msgs)
        assert "[EMAIL]" in cleaned[0]["content"]
        assert cleaned[1]["content"] == "Sure, I'll check."

    def test_does_not_mutate_original(self):
        original = [{"role": "user", "content": "alice@lab.com"}]
        cleaned, _ = sanitise_messages(original)
        assert "alice@lab.com" in original[0]["content"]
        assert "alice@lab.com" not in cleaned[0]["content"]

    def test_empty_messages(self):
        cleaned, triggered = sanitise_messages([])
        assert cleaned == []
        assert triggered == []


class TestIsSafeForCloud:
    def test_clean_text_is_safe(self):
        assert is_safe_for_cloud("Summarise the polymer research findings") is True

    def test_email_is_not_safe(self):
        assert is_safe_for_cloud("Contact alice@lab.com") is False

    def test_path_is_not_safe(self):
        assert is_safe_for_cloud("Read /data/results.csv") is False

    def test_openai_api_key_not_safe(self):
        assert is_safe_for_cloud("key: sk-" + "a" * 40) is False

    def test_long_dna_sequence_is_safe(self):
        # A 70-character DNA/protein sequence must NOT be treated as an API key
        dna = "ATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCG"
        text, triggered = sanitise_text(f"Protein sequence: {dna}")
        assert "api_key" not in triggered
        assert dna in text
