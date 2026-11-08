"""Live proof against the running cmux. Creates one disposable workspace and closes only that one."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = Path("/tmp/cmux-mcp-proof/payload.txt")
KEEP = ("workspace:38", "workspace:39")
GROK = (
    "bash -lc 'export PATH=\"$HOME/.grok/bin:$PATH\"; "
    "exec grok --always-approve --no-subagents --reasoning-effort low'"
)


class Mcp:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self.next_id = 1

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        while True:
            message = self._recv()
            if message.get("id") == request_id:
                if "error" in message:
                    raise SystemExit(f"rpc error {method}: {message['error']}")
                return message["result"]

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        text = result["content"][0]["text"]
        if result.get("isError"):
            raise SystemExit(f"{name} failed: {text}")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return {"raw": text, "data": parsed, "arguments": arguments}

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin
        data = json.dumps(payload).encode()
        self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        assert self.proc.stdout
        headers: dict[bytes, bytes] = {}
        while True:
            line = self.proc.stdout.readline()
            if line == b"":
                err = b""
                if self.proc.stderr:
                    err = self.proc.stderr.read()
                raise SystemExit(f"server exited\n{err.decode('utf-8', 'replace')}")
            if line in (b"\r\n", b"\n"):
                break
            key, value = line.split(b":", 1)
            headers[key.strip().lower()] = value.strip()
        length = int(headers[b"content-length"])
        return json.loads(self.proc.stdout.read(length))


def refs(listed: dict) -> list[str]:
    return [item["ref"] for item in listed["data"]["workspaces"]]


def main() -> None:
    PAYLOAD.parent.mkdir(parents=True, exist_ok=True)
    file_text = (
        "CMUXMCP-PROOF-LINE-1 the server read this file from disk.\n"
        "CMUXMCP-PROOF-LINE-2 the tool arguments do not contain this sentence.\n"
        "CMUXMCP-PROOF-LINE-3 the bytes are in the prompt box and Enter was not pressed.\n"
    )
    PAYLOAD.write_text(file_text, encoding="utf-8")
    proc = subprocess.Popen(
        ["python3", "-m", "cmux_mcp"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    mcp = Mcp(proc)
    created_ref = None
    try:
        init = mcp.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "prove", "version": "0"},
            },
        )
        mcp.notify("notifications/initialized")
        tools = mcp.request("tools/list")
        names = [tool["name"] for tool in tools["tools"]]
        if len(names) != 9 or "input" not in names:
            raise SystemExit(f"unexpected tools {names}")

        status = mcp.call_tool("status", {})
        if status["data"].get("cmux") != "up":
            raise SystemExit(f"cmux is down: {status['raw']}")

        stale = mcp.call_tool("workspace_list", {})
        for item in stale["data"]["workspaces"]:
            if item["ref"] in KEEP:
                continue
            cwd = str(item.get("cwd") or "")
            if item.get("title") == "cmux-mcp-proof" and cwd.rstrip("/").endswith("cmux-mcp-proof"):
                mcp.call_tool("workspace_close", {"workspace": item["ref"]})

        before = mcp.call_tool("workspace_list", {})
        before_refs = refs(before)
        for keep in KEEP:
            if keep not in before_refs:
                raise SystemExit(f"missing protected workspace {keep}")

        created = mcp.call_tool(
            "workspace_create",
            {
                "cwd": str(PAYLOAD.parent),
                "command": GROK,
                "name": "cmux-mcp-proof",
                "focus": False,
            },
        )
        created_ref = created["data"]["workspace"]
        created_id = created["data"]["id"]
        surface = created["data"]["terminal"]
        if created_ref in KEEP or created_ref in before_refs:
            raise SystemExit(f"refusing to use {created_ref}")

        after_create = mcp.call_tool("workspace_list", {})
        selected = [item["ref"] for item in after_create["data"]["workspaces"] if item["selected"]]
        if created_ref in selected:
            raise SystemExit(f"create focused {created_ref}; focus was false")

        deadline = time.time() + 60
        screen = None
        while time.time() < deadline:
            screen = mcp.call_tool("screen", {"workspace": created_ref, "surface": surface})
            if "❯" in screen["data"]["text"] and "Grok" in screen["data"]["text"]:
                break
            time.sleep(1)
        else:
            raise SystemExit("prompt box did not appear")

        arguments = {
            "workspace": created_ref,
            "surface": surface,
            "target": "prompt",
            "path": str(PAYLOAD),
        }
        placed = mcp.call_tool("input", arguments)
        if file_text.strip() in json.dumps(arguments):
            raise SystemExit("tool arguments contained the file body")
        if "CMUXMCP-PROOF-LINE-1" in placed["raw"]:
            raise SystemExit("tool result contained the file body")
        if placed["data"].get("submitted") is not False:
            raise SystemExit(f"file placement submitted: {placed['raw']}")

        time.sleep(0.4)
        seen = mcp.call_tool("screen", {"workspace": created_ref, "surface": surface, "lines": 16})
        text = seen["data"]["text"]
        for line in file_text.splitlines():
            if line not in text:
                raise SystemExit(f"screen missing {line!r}\n{text}")
        if "Enter:send" not in text:
            raise SystemExit(f"prompt box did not stay unsent\n{text}")

        pane = mcp.call_tool("status", {"workspace": created_ref, "surface": surface})
        surfaces = mcp.call_tool("surface_list", {"workspace": created_ref})

        closed = mcp.call_tool("workspace_close", {"workspace": created_ref})
        created_ref = None
        final = mcp.call_tool("workspace_list", {})
        final_refs = refs(final)
        for keep in KEEP:
            if keep not in final_refs:
                raise SystemExit(f"protected workspace disappeared: {keep}")
        if any(item["id"] == created_id for item in final["data"]["workspaces"]):
            raise SystemExit("closed workspace id is still listed")

        proof = {
            "server": init["serverInfo"],
            "tools": names,
            "status": status["data"],
            "list_count": len(before_refs),
            "protected_present": list(KEEP),
            "create_arguments": created["arguments"],
            "create_result": created["data"],
            "still_selected": selected,
            "input_arguments": arguments,
            "input_result": placed["data"],
            "file_text": file_text,
            "screen": text,
            "pane": pane["data"],
            "surfaces": surfaces["data"],
            "close": closed["data"],
            "final_has_38": "workspace:38" in final_refs,
            "final_has_39": "workspace:39" in final_refs,
        }
        out = PAYLOAD.parent / "proof-run.json"
        out.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(out)
    finally:
        if created_ref and created_ref not in KEEP:
            try:
                mcp.call_tool("workspace_close", {"workspace": created_ref})
            except Exception as exc:
                print(f"cleanup close failed: {exc}", file=sys.stderr)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
