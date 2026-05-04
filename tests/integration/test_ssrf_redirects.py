"""Tests for SSRF protection on URL ingestion.

``_validate_fetch_url`` is the first line of defence — it rejects loopback
and RFC-1918 ranges before any HTTP request is made.  ``extract_url_sync``
wraps it and *also* re-validates the final URL after redirects, blocking
open-redirect SSRF chains.
"""
from __future__ import annotations

import io

import pytest


@pytest.fixture
def ingest():
    from notebook import ingest as module
    return module


# ── Static URL validation ────────────────────────────────────────────────

class TestValidateFetchUrl:
    def test_rejects_localhost(self, ingest):
        with pytest.raises(ValueError, match="internal"):
            ingest._validate_fetch_url("http://localhost/admin")

    def test_rejects_loopback_ip(self, ingest):
        with pytest.raises(ValueError, match="internal"):
            ingest._validate_fetch_url("http://127.0.0.1/")

    def test_rejects_rfc1918_10(self, ingest):
        with pytest.raises(ValueError, match="internal"):
            ingest._validate_fetch_url("http://10.0.0.5/")

    def test_rejects_rfc1918_192_168(self, ingest):
        with pytest.raises(ValueError, match="internal"):
            ingest._validate_fetch_url("https://192.168.1.1/")

    def test_rejects_link_local(self, ingest):
        with pytest.raises(ValueError, match="internal"):
            ingest._validate_fetch_url("http://169.254.169.254/")

    def test_rejects_ipv6_loopback(self, ingest):
        with pytest.raises(ValueError, match="internal"):
            ingest._validate_fetch_url("http://[::1]/")

    def test_rejects_non_http_scheme(self, ingest):
        with pytest.raises(ValueError, match="HTTP"):
            ingest._validate_fetch_url("file:///etc/passwd")

    def test_accepts_public_https(self, ingest):
        # Should not raise.
        ingest._validate_fetch_url("https://example.com/article")


# ── Open-redirect SSRF (final URL re-validation) ─────────────────────────

class _FakeStreamResponse:
    """Minimal stand-in for ``httpx.Response`` used inside a streaming context."""

    def __init__(self, final_url: str, body: bytes = b"hello"):
        self.url = final_url
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self, _chunk_size):
        yield self._body


class _FakeHttpxClient:
    def __init__(self, *_, redirect_to: str, **__):
        self._redirect_to = redirect_to

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, _method, _url):
        return _FakeStreamResponse(self._redirect_to)


class TestRedirectRevalidation:
    def test_rejects_redirect_to_internal_host(self, ingest, monkeypatch):
        """A public URL that 30x-redirects to localhost must be rejected."""
        # Inject a fake httpx whose Client always reports ``url`` as 127.0.0.1.
        fake_httpx = type("fake_httpx", (), {})()

        def fake_client(*args, **kwargs):
            return _FakeHttpxClient(redirect_to="http://127.0.0.1/admin")

        fake_httpx.Client = fake_client  # type: ignore[attr-defined]

        # trafilatura is imported alongside httpx; provide a no-op stub.
        fake_trafilatura = type("fake_trafilatura", (), {})()
        fake_trafilatura.extract = staticmethod(lambda _: "")  # type: ignore[attr-defined]

        monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)
        monkeypatch.setitem(__import__("sys").modules, "trafilatura", fake_trafilatura)

        with pytest.raises(ValueError, match="redirected"):
            ingest.extract_url_sync("https://example.com/redirect-attack")

    def test_response_size_cap_enforced(self, ingest, monkeypatch):
        """Bodies larger than MAX_FETCH_BYTES must be rejected mid-stream."""

        class _OversizeResponse(_FakeStreamResponse):
            def iter_bytes(self, _chunk_size):
                # One chunk exactly exceeding the cap.
                yield b"x" * (ingest.MAX_FETCH_BYTES + 1)

        class _OversizeClient:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def stream(self, _method, _url):
                return _OversizeResponse("https://example.com/big")

        fake_httpx = type("fake_httpx", (), {})()
        fake_httpx.Client = lambda *_, **__: _OversizeClient()  # type: ignore[attr-defined]
        fake_trafilatura = type("fake_trafilatura", (), {})()
        fake_trafilatura.extract = staticmethod(lambda _: "")  # type: ignore[attr-defined]
        monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)
        monkeypatch.setitem(__import__("sys").modules, "trafilatura", fake_trafilatura)

        with pytest.raises(ValueError, match="too large"):
            ingest.extract_url_sync("https://example.com/big")


# Silence unused import warnings — ``io`` is reserved for future stub bodies.
_ = io
