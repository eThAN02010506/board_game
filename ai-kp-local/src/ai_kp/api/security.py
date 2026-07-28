"""Small dependency-free HTTP hardening middleware for local and LAN modes."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

SENSITIVE_POST_PATHS = (
    re.compile(r"^/sessions/join$"),
    re.compile(r"^/session-seats/claim$"),
    re.compile(r"^/session-seats/[^/]+/recover$"),
    re.compile(r"^/campaigns/[^/]+/sessions/recover-kp$"),
    re.compile(r"^/campaigns/[^/]+/maps/generate$"),
    re.compile(r"^/maps/[^/]+/image-assets/generate$"),
    re.compile(r"^/kp/turn$"),
    re.compile(r"^/model-settings/discover$"),
    re.compile(r"^/image-model-settings/discover$"),
)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach conservative headers without overriding route-specific policies."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied_id = request.headers.get("X-Request-ID", "")
        request_id = supplied_id if REQUEST_ID_RE.fullmatch(supplied_id) else uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request_id)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=()",
        )
        if not response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )
        return response


class SensitiveOperationRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client sliding-window guard for expensive and credential endpoints."""

    def __init__(
        self,
        app,
        *,
        requests: int,
        window_seconds: int,
    ):
        super().__init__(app)
        self.requests = requests
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        bucket = next(
            (
                f"sensitive-{index}"
                for index, pattern in enumerate(SENSITIVE_POST_PATHS)
                if pattern.fullmatch(request.url.path)
            ),
            None,
        )
        if request.method != "POST" or bucket is None:
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        key = f"{client}:{bucket}"
        now = time.monotonic()
        async with self._lock:
            attempts = self._attempts[key]
            cutoff = now - self.window_seconds
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self.requests:
                retry_after = max(1, int(self.window_seconds - (now - attempts[0])))
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many sensitive operations; retry later",
                        "code": "rate_limit_exceeded",
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            attempts.append(now)
        return await call_next(request)
