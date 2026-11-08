"""One shared MCP server for every session, over localhost HTTP (2026-11-06, S1 of the memory initiative).

Each Claude session ran its own ``berth mcp`` (and, where placed, ``python -m cmux_mcp``): 130 processes, about
2 GB of footprint. ``berth mcp --http`` serves both from one process, at ``/berth`` and ``/cmux-mcp``, with Claude
Code's ``http`` MCP type. Plan: ``docs/SHARED-MCP-SERVER-PLAN.md``.

- **Stateless.** Each POST carries one JSON-RPC message (or a batch) and gets JSON back; no server push, no MCP
  session id. A restart breaks nothing beyond the requests in flight.
- **Who calls.** A stdio server knew its session from its own ``CMUX_WORKSPACE_ID``. Here the registration sends
  it with every request: ``"headers": {"X-Berth-Workspace": "${CMUX_WORKSPACE_ID}"}`` (Claude Code fills it in;
  tested 2026-11-06). Empty means a session outside cmux: ``me`` refuses, as it does over stdio.
- **Safety.** Localhost is reachable from web pages in a browser. Refused: any request with an ``Origin`` header,
  any without ``X-Berth-Workspace`` (a page can't add it cross-site), and any ``Host`` but this port on 127.0.0.1 or
  localhost. A local process gains nothing it lacks: it can already run the ``berth`` CLI.
- **cmux-mcp** is served when its package imports (its home puts it on ``PYTHONPATH``, as its stdio registration
  does); otherwise ``/cmux-mcp`` answers 404.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

HEADER = "X-Berth-Workspace"
DEFAULT_PORT = 4792  # 4791 is the tailnet offer (berth/offer.py)
MAX_BODY = 4 * 1024 * 1024

Respond = Callable[[str, dict], "dict | None"]


def berth_mount(db_path: Path | str | None = None, cmux_factory=None) -> Respond:
    """Berth's own tools: one ``Api`` per request, so each knows its caller."""
    from berth.mcp import Api, handle

    def respond(workspace: str, message: dict) -> dict | None:
        api = Api(db_path, cmux=cmux_factory() if cmux_factory else None, caller_workspace=workspace)
        return handle(api, message)

    return respond


def cmux_mount() -> Respond | None:
    """cmux-mcp's tools, when its package is importable; the caller is set per request (its CALLER)."""
    try:
        from cmux_mcp import berth_api, server
        from cmux_mcp.client import CmuxClient
        from cmux_mcp.ops import CmuxOps
    except ImportError:
        return None
    if not hasattr(server, "respond") or not hasattr(berth_api, "CALLER"):
        return None  # an older cmux-mcp that can only serve its own session over stdio

    def respond(workspace: str, message: dict) -> dict | None:
        token = berth_api.CALLER.set(workspace)
        try:
            return server.respond(CmuxOps(CmuxClient()), message)
        finally:
            berth_api.CALLER.reset(token)

    return respond


def make_server(port: int = DEFAULT_PORT, *, mounts: dict[str, Respond] | None = None, host: str = "127.0.0.1",
                db_path: Path | str | None = None) -> ThreadingHTTPServer:
    """The server, not yet serving. ``mounts`` maps a path to its responder (tests pass their own)."""
    if mounts is None:
        mounts = {"/berth": berth_mount(db_path)}
        cmux = cmux_mount()
        if cmux is not None:
            mounts["/cmux-mcp"] = cmux
    routes = dict(mounts)  # Claude Code's first call, server/discover, gets "method not found" and moves on

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: Any = None) -> None:
            raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if raw:
                self.wfile.write(raw)

        def _refused(self) -> str | None:
            if self.headers.get("Origin") is not None:
                return "requests from a web page are refused"
            allowed = {f"127.0.0.1:{self.server.server_address[1]}", f"localhost:{self.server.server_address[1]}"}
            if (self.headers.get("Host") or "") not in allowed:
                return "wrong Host"
            if self.headers.get(HEADER) is None:
                return f"{HEADER} is required"
            return None

        def do_POST(self) -> None:  # noqa: N802
            why = self._refused()
            if why:
                self._send(403, {"error": why})
                return
            respond = routes.get(self.path.split("?", 1)[0].rstrip("/") or "/")
            if respond is None:
                self._send(404, {"error": f"no MCP server at {self.path}", "servers": sorted(routes)})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                self._send(400, {"error": "a JSON-RPC message is required"})
                return
            try:
                message = json.loads(self.rfile.read(length))
            except ValueError:
                self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                return
            workspace = (self.headers.get(HEADER) or "").strip()
            if isinstance(message, list):
                answers = [answer for item in message if isinstance(item, dict)
                           for answer in [respond(workspace, item)] if answer is not None]
                self._send(200, answers) if answers else self._send(202)
                return
            if not isinstance(message, dict):
                self._send(400, {"error": "a JSON-RPC message is an object"})
                return
            answer = respond(workspace, message)
            self._send(202) if answer is None else self._send(200, answer)

        def do_GET(self) -> None:  # noqa: N802
            # No server-to-client stream: every answer comes with its request.
            self._send(405, {"error": "this server pushes nothing; POST JSON-RPC"})

        def do_DELETE(self) -> None:  # noqa: N802
            self._send(405, {"error": "no MCP sessions to end"})

        def log_message(self, fmt: str, *args: object) -> None:
            return

    class Server(ThreadingHTTPServer):
        request_queue_size = 128  # every session may connect at once (after a restart); the default 5 resets them
        daemon_threads = True

    server = Server((host, port), Handler)
    return server


def serve(port: int = DEFAULT_PORT, *, db_path: Path | str | None = None) -> int:
    """``berth mcp --http``: serve until stopped."""
    from berth.db import default_catalog_path
    from berth.health import register

    path = Path(db_path) if db_path else default_catalog_path()
    register(path.parent, "mcp-http")
    server = make_server(port, db_path=path)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    """For tests: serve on a daemon thread."""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def check(port: int = DEFAULT_PORT, *, timeout: float = 2.0) -> dict:
    """The health check: a ping to each server path. ``{"berth": "up"|"down", "cmux-mcp": ..., "port"}``."""
    import urllib.error
    import urllib.request

    out: dict = {"port": port}
    for name in ("berth", "cmux-mcp"):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/{name}", method="POST",
                                         data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode(),
                                         headers={"Content-Type": "application/json", HEADER: ""})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read() or b"{}")
            out[name] = "up" if "result" in body else "error"
        except urllib.error.HTTPError as exc:
            out[name] = "not served" if exc.code == 404 else f"http {exc.code}"
        except (OSError, ValueError):
            out[name] = "down"
    return out
