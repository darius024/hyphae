"""Reusable ASGI middleware for the Hyphae web backend."""

from __future__ import annotations

import logging
import re
import time
import threading
from collections import defaultdict
from typing import Sequence

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_req_log = logging.getLogger("hyphae.requests")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter.

    Parameters
    ----------
    app : ASGI app
    global_rpm : int
        Maximum requests per minute for any single IP (default 120).
    strict_paths : sequence of str
        URL path prefixes that get a tighter limit.
    strict_rpm : int
        Maximum requests per minute for *strict_paths* (default 10).
    cleanup_interval : int
        Seconds between stale-entry garbage collection (default 120).
    """

    def __init__(
        self,
        app,
        *,
        global_rpm: int = 120,
        strict_paths: Sequence[str] = ("/api/auth/login", "/api/auth/signup"),
        strict_rpm: int = 10,
        cleanup_interval: int = 120,
    ):
        super().__init__(app)
        self._global_rpm = global_rpm
        self._strict_paths = tuple(strict_paths)
        self._strict_rpm = strict_rpm
        self._cleanup_interval = cleanup_interval

        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    # Only trust X-Forwarded-For when the direct peer is a known private/loopback
    # address, meaning a real reverse proxy is sitting in front.  Requests
    # arriving directly from public IPs must use the TCP peer address so that
    # clients cannot spoof an arbitrary IP to bypass per-IP rate limiting.
    _PRIVATE_IP_RE = re.compile(
        r'^(127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|::1$|localhost$)',
        re.IGNORECASE,
    )

    def _client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else ""
        if peer and self._PRIVATE_IP_RE.match(peer):
            # Behind a trusted reverse proxy — use the first forwarded address.
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return peer or "unknown"

    def _prune(self, timestamps: list[float], cutoff: float) -> list[float]:
        """Remove entries older than *cutoff* (in-place for efficiency)."""
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)
        return timestamps

    def _maybe_cleanup(self):
        """Periodically purge stale IPs to prevent unbounded memory growth."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        cutoff = now - 60
        stale = [k for k, v in self._hits.items() if not v or v[-1] < cutoff]
        for k in stale:
            del self._hits[k]

    async def dispatch(self, request: Request, call_next):
        ip = self._client_ip(request)
        path = request.url.path
        now = time.monotonic()
        cutoff = now - 60

        is_strict = path.startswith(self._strict_paths)
        limit = self._strict_rpm if is_strict else self._global_rpm
        key = f"{ip}:{path}" if is_strict else ip

        with self._lock:
            self._maybe_cleanup()
            timestamps = self._hits[key]
            self._prune(timestamps, cutoff)

            if len(timestamps) >= limit:
                retry_after = int(timestamps[0] + 60 - now) + 1
                return JSONResponse(
                    {"detail": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": str(max(retry_after, 1))},
                )
            timestamps.append(now)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - len(timestamps)))
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log method, path, status code, and duration for every request.

    Static asset requests are skipped to keep logs focused on API traffic.

    Parameters
    ----------
    app : ASGI app
    skip_prefixes : tuple of str
        Path prefixes to skip logging (default: static assets + favicon).
    """

    def __init__(self, app, *, skip_prefixes: tuple[str, ...] = ("/static/", "/favicon.ico")):
        super().__init__(app)
        self._skip = skip_prefixes

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(self._skip):
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        _req_log.info(
            "%s %s %d %.1fms",
            request.method,
            path,
            response.status_code,
            duration_ms,
        )

        response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"
        return response
