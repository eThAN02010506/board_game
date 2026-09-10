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
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SENSITIVE_POST_PATHS = (
    ("session-join", re.compile(r"^/sessions/join$")),
    ("seat-claim", re.compile(r"^/session-seats/claim$")),
    ("seat-recover", re.compile(r"^/session-seats/[^/]+/recover$")),
    ("kp-recover", re.compile(r"^/campaigns/[^/]+/sessions/recover-kp$")),
    ("map-generate", re.compile(r"^/campaigns/[^/]+/maps/generate$")),
    ("map-image-generate", re.compile(r"^/maps/[^/]+/image-assets/generate$")),
    ("kp-turn", re.compile(r"^/kp/turn$")),
    ("kp-turn", re.compile(r"^/campaigns/[^/]+/actions/settle$")),
    ("auto-kp-retry", re.compile(r"^/auto-kp/jobs/[^/]+/retry$")),
    ("director-help", re.compile(r"^/module-runs/[^/]+/director/help$")),
    ("model-discover", re.compile(r"^/model-settings/discover$")),
    ("image-model-discover", re.compile(r"^/image-model-settings/discover$")),
    ("player-profile-write", re.compile(r"^/player-profiles$")),
    ("player-profile-write", re.compile(r"^/investigators(?:/preview)?$")),
    (
        "player-profile-write",
        re.compile(r"^/investigators/[^/]+/revisions$"),
    ),
)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
JSON_MEDIA_TYPES = {"application/json", "application/ld+json", "application/problem+json"}
BODY_METHODS = {"POST", "PUT", "PATCH"}


class RequestBodyLimitMiddleware:
    """Bound JSON bodies while reading ASGI chunks, independent of Content-Length."""

    def __init__(self, app: ASGIApp, *, max_json_bytes: int):
        self.app = app
        self.max_json_bytes = max_json_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in BODY_METHODS:
            await self.app(scope, receive, send)
            return

        headers = {
            key.lower(): value
            for key, value in scope.get("headers", ())
        }
        media_type = (
            headers.get(b"content-type", b"")
            .decode("latin-1")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if media_type not in JSON_MEDIA_TYPES and not media_type.endswith("+json"):
            await self.app(scope, receive, send)
            return

        declared_size = headers.get(b"content-length")
        if declared_size is not None:
            try:
                if int(declared_size) > self.max_json_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send, detail="Invalid Content-Length")
                return

        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            total += len(message.get("body", b""))
            if total > self.max_json_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            # Once the buffered body has been replayed, keep delegating to the
            # server receive channel. Long-running handlers must still be able
            # to observe a later ``http.disconnect`` from the client.
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        detail: str = "JSON request body is too large",
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": detail, "code": "request_body_too_large"},
        )
        await response(scope, receive, send)


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
                bucket_name
                for bucket_name, pattern in SENSITIVE_POST_PATHS
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
