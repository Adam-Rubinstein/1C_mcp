"""Run FastMCP with stdio or SSE + optional Bearer token."""

from __future__ import annotations

import os
from typing import Callable

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        if auth != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def make_mcp(name: str) -> FastMCP:
    return FastMCP(name)


def run_mcp(mcp: FastMCP, *, default_port: int = 8760) -> None:
    mode = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if mode in ("stdio", "std"):
        mcp.run(transport="stdio")
        return

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", str(default_port)))
    token = os.environ.get("MCP_TOKEN", "")

    # FastMCP SSE app
    app = mcp.sse_app()
    if token:
        app.add_middleware(BearerAuthMiddleware, token=token)

    import uvicorn

    # health endpoint
    async def health(_request):
        return JSONResponse({"ok": True, "server": mcp.name})

    try:
        from starlette.routing import Route

        app.routes.insert(0, Route("/health", health))
    except Exception:
        pass

    uvicorn.run(app, host=host, port=port, log_level="info")
