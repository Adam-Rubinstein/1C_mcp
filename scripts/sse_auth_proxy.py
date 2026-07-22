"""Reverse proxy: Bearer auth in front of an upstream SSE MCP (e.g. platform JAR)."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route


UPSTREAM = os.environ.get("UPSTREAM_URL", "http://127.0.0.1:8760").rstrip("/")
TOKEN = os.environ.get("MCP_TOKEN", "")
LISTEN_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("MCP_PORT", "8860"))


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/"):
            return await call_next(request)
        if TOKEN:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {TOKEN}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def health(_request: Request):
    return JSONResponse({"ok": True, "upstream": UPSTREAM})


async def proxy(request: Request):
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    url = f"{UPSTREAM}{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "authorization")}
    body = await request.body()
    async with httpx.AsyncClient(timeout=None) as client:
        req = client.build_request(request.method, url, headers=headers, content=body)
        upstream = await client.send(req, stream=True)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=dict(upstream.headers),
            background=None,
        )


app = Starlette(routes=[Route("/health", health), Route("/{path:path}", proxy, methods=["GET", "POST", "OPTIONS"])])
app.add_middleware(BearerAuthMiddleware)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)


if __name__ == "__main__":
    main()
