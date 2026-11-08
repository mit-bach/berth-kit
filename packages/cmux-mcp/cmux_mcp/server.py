"""MCP stdio server. Fourteen Berth tools. File placement is input.path."""

from __future__ import annotations

import json
import sys
from typing import Any

from cmux_mcp import __version__
from cmux_mcp.berth_api import TOOLS as BERTH_TOOLS
from cmux_mcp.berth_api import ToolError, call as berth_call
from cmux_mcp.client import CmuxClient, CmuxError
from cmux_mcp.ops import CmuxOps

SERVER_NAME = "cmux-mcp"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def _bool(description: str) -> dict[str, str]:
    return {"type": "boolean", "description": description}


def _int(description: str) -> dict[str, str]:
    return {"type": "integer", "description": description}


_RETIRED: list[dict[str, Any]] = [
    {
        "name": "status",
        "description": "Report whether cmux is up, and whether a named pane is alive.",
        "inputSchema": _schema(
            {
                "workspace": _string("Workspace ref or id. Optional."),
                "surface": _string("Surface ref or id. Optional. Requires workspace."),
            },
            [],
        ),
    },
    {
        "name": "workspace_list",
        "description": "List workspaces and their sidebar groups.",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "workspace_create",
        "description": "Create a workspace in a working directory, optionally inside a group.",
        "inputSchema": _schema(
            {
                "cwd": _string("Working directory. Passed as --cwd."),
                "command": _string("Initial terminal command. Passed as --command when set."),
                "name": _string("Sidebar title. Passed as --name when set."),
                "description": _string("Workspace description. Passed as --description when set."),
                "focus": _bool("Passed as --focus when set. Omit to leave the CLI default."),
                "group": _string("workspace_group ref or id. Passed as --group when set."),
                "group_placement": {
                    "type": "string",
                    "enum": ["afterCurrent", "top", "end"],
                    "description": "Passed as --group-placement when set.",
                },
                "group_reference": _string("Workspace for afterCurrent. Passed as --group-reference when set."),
                "window": _string("Target window ref or id. Passed as --window when set."),
                "env": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "KEY=VALUE entries. Each is one --env.",
                },
                "env_file": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files of KEY=VALUE lines. Each is one --env-file.",
                },
                "layout": _string("Split layout JSON. Passed as --layout when set."),
            },
            ["cwd"],
        ),
    },
    {
        "name": "workspace_rename",
        "description": "Rename the workspace named by workspace.",
        "inputSchema": _schema(
            {
                "workspace": _string("Workspace ref or id."),
                "title": _string("New sidebar title."),
            },
            ["workspace", "title"],
        ),
    },
    {
        "name": "workspace_focus",
        "description": "Focus the workspace named by workspace.",
        "inputSchema": _schema(
            {"workspace": _string("Workspace ref or id.")},
            ["workspace"],
        ),
    },
    {
        "name": "workspace_close",
        "description": "Close the one workspace named by workspace.",
        "inputSchema": _schema(
            {"workspace": _string("Workspace ref or id to close.")},
            ["workspace"],
        ),
    },
    {
        "name": "surface_list",
        "description": "List surfaces in one workspace, including each pane_ref.",
        "inputSchema": _schema(
            {"workspace": _string("Workspace ref or id.")},
            ["workspace"],
        ),
    },
    {
        "name": "screen",
        "description": "Read the visible pane, including the prompt box.",
        "inputSchema": _schema(
            {
                "workspace": _string("Workspace ref or id."),
                "surface": _string("Surface ref or id. Defaults to the terminal."),
                "lines": _int("Return the last N lines of the visible pane."),
            },
            ["workspace"],
        ),
    },
    {
        "name": "input",
        "description": "Write the prompt box or the terminal from text, a file path, or one key.",
        "inputSchema": _schema(
            {
                "workspace": _string("Workspace ref or id."),
                "surface": _string("Surface ref or id. Defaults to the terminal."),
                "target": {
                    "type": "string",
                    "enum": ["prompt", "terminal"],
                    "description": "prompt is the prompt box. terminal is the terminal.",
                },
                "text": _string("One short line. Not a file body."),
                "path": _string("Absolute file path. The server reads it into the prompt box."),
                "key": _string("One key, such as enter, escape, backspace, or ctrl-c."),
                "submit": _bool("Press Enter after text. Refused when path is set."),
            },
            ["workspace", "target"],
        ),
    },
    {
        "name": "group",
        "description": "List or change one sidebar workspace group.",
        "inputSchema": _schema(
            {
                "action": {
                    "type": "string",
                    "enum": [
                        "list",
                        "create",
                        "add",
                        "remove",
                        "new_workspace",
                        "rename",
                        "collapse",
                        "expand",
                        "focus",
                        "ungroup",
                        "delete",
                    ],
                    "description": "workspace-group subcommand.",
                },
                "group": _string("workspace_group ref or id."),
                "name": _string("Group name for create or rename."),
                "workspace": _string("Workspace ref or id for add or remove."),
                "cwd": _string("Anchor directory for create. Passed as --cwd when set."),
                "from": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Existing workspaces to capture. Omit --from when this is omitted.",
                },
                "placement": {
                    "type": "string",
                    "enum": ["afterCurrent", "top", "end"],
                    "description": "new_workspace --placement.",
                },
                "idempotency_key": _string("create --idempotency-key."),
                "external_id": _string("create --external-id."),
                "close_members": _bool("delete --close-workspaces when true. Otherwise members stay open."),
                "remove_generated_anchor": _bool("ungroup --remove-generated-anchor when true."),
            },
            ["action"],
        ),
    },
]


TOOLS = BERTH_TOOLS


def dispatch(ops: CmuxOps, name: str, arguments: dict[str, Any]) -> Any:
    return berth_call(ops, name, arguments)


# Grok writes one JSON object per line. A Content-Length header never arrives, so a
# reader that waits for that header never answers initialize and the client hits
# its 30s startup timeout. Reply in whichever framing the client used.
_framing = "newline"


def _parse_object(body: bytes) -> dict[str, Any] | None:
    message = json.loads(body)
    if not isinstance(message, dict):
        return None
    return message


def _read_message() -> dict[str, Any] | None:
    global _framing
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(b"{") or stripped.startswith(b"["):
            _framing = "newline"
            return _parse_object(stripped)
        headers: dict[bytes, bytes] = {}
        current: bytes | None = line
        while current is not None:
            if current in (b"\r\n", b"\n"):
                break
            if b":" in current:
                key, value = current.split(b":", 1)
                headers[key.strip().lower()] = value.strip()
            current = sys.stdin.buffer.readline()
            if current == b"":
                return None
        length = int(headers.get(b"content-length", b"0"))
        if length <= 0:
            continue
        body = sys.stdin.buffer.read(length)
        if len(body) != length:
            return None
        _framing = "content-length"
        return _parse_object(body)


def _write_message(payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if _framing == "content-length":
        sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(data)
    else:
        sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def _result(request_id: Any, result: dict[str, Any]) -> None:
    _write_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str) -> None:
    _write_message(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )


def _tool_result(request_id: Any, payload: Any, is_error: bool = False) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False)
    _result(
        request_id,
        {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    )


def _tool_payload(payload: Any, is_error: bool = False) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def respond(ops: CmuxOps, message: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message in, its response out (None for a notification). No transport: stdio's serve and
    Berth's shared HTTP server (berth mcp --http, 2026-11-06) both call it."""
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None and isinstance(method, str) and method.startswith("notifications/"):
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}

    def result(body: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": body}

    try:
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1]
            return result({"protocolVersion": version, "capabilities": {"tools": {}},
                           "serverInfo": {"name": SERVER_NAME, "version": __version__}})
        if method == "ping":
            return result({})
        if method == "tools/list":
            return result({"tools": TOOLS})
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                payload = dispatch(ops, name, arguments)
            except ToolError as exc:
                return result(_tool_payload(exc.payload(), is_error=True))
            except TypeError as exc:
                return result(_tool_payload({"error": {"code": "INTERNAL", "message": str(exc), "details": {}}},
                                            is_error=True))
            except (CmuxError, ValueError, OSError) as exc:
                return result(_tool_payload(str(exc), is_error=True))
            return result(_tool_payload(payload))
        if method == "notifications/initialized":
            return None
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
    except Exception as exc:
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(exc)}}


def serve(ops: CmuxOps | None = None) -> None:
    bound = ops or CmuxOps(CmuxClient())
    while True:
        message = _read_message()
        if message is None:
            return
        response = respond(bound, message)
        if response is None:
            continue
        try:
            _write_message(response)
        except BrokenPipeError:
            return


def main() -> None:
    serve()
