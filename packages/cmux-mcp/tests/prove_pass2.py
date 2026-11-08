"""Pass 2 proof: a new sidebar group, one workspace in it, file placement, then removal."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = Path("/tmp/cmux-mcp-proof/payload.txt")
KEEP_WORKSPACES = ("workspace:38", "workspace:39")
KEEP_GROUPS = ("School OS", "UNI-Classes", "Fog of war", "Chrome Dev")
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
            raise SystemExit(f"{name} failed: {text}\narguments: {json.dumps(arguments)}")
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


def group_signature(groups: list[dict]) -> dict:
    signed = {}
    for group in groups:
        if group.get("name") not in KEEP_GROUPS:
            continue
        signed[group["name"]] = {
            "ref": group.get("ref"),
            "anchor_workspace_ref": group.get("anchor_workspace_ref"),
            "member_workspace_refs": list(group.get("member_workspace_refs") or []),
            "member_count": group.get("member_count"),
            "is_collapsed": group.get("is_collapsed"),
            "is_pinned": group.get("is_pinned"),
        }
    return signed


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
    created_workspace = None
    anchor_ref = None
    group_ref = None
    before_refs: set[str] = set()
    try:
        init = mcp.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "prove-pass2", "version": "0"},
            },
        )
        mcp.notify("notifications/initialized")
        before = mcp.call_tool("workspace_list", {})
        before_refs = {item["ref"] for item in before["data"]["workspaces"]}
        for keep in KEEP_WORKSPACES:
            if keep not in before_refs:
                raise SystemExit(f"missing protected workspace {keep}")
        before_groups = group_signature(before["data"]["groups"])
        if set(before_groups) != set(KEEP_GROUPS):
            raise SystemExit(f"protected groups changed before the proof: {sorted(before_groups)}")

        created_group = mcp.call_tool("group", {"action": "create", "name": "cmux-mcp-proof"})
        group = created_group["data"]["group"]
        group_ref = group["ref"]
        anchor_ref = group["anchor_workspace_ref"]
        members = list(group["member_workspace_refs"])
        if any(member in before_refs for member in members):
            raise SystemExit(f"group create captured an existing workspace: {members}")
        if anchor_ref in before_refs:
            raise SystemExit(f"anchor {anchor_ref} already existed")

        created = mcp.call_tool(
            "workspace_create",
            {
                "cwd": str(PAYLOAD.parent),
                "command": GROK,
                "name": "cmux-mcp-proof",
                "focus": False,
                "group": group_ref,
            },
        )
        created_workspace = created["data"]["workspace"]
        created_id = created["data"]["id"]
        surface = created["data"]["terminal"]
        if created_workspace in KEEP_WORKSPACES or created_workspace in before_refs:
            raise SystemExit(f"refusing to use {created_workspace}")
        if created_workspace == anchor_ref:
            raise SystemExit("create returned the group anchor")
        if created["data"].get("focused") is not False:
            raise SystemExit("workspace was focused")

        listed = mcp.call_tool("workspace_list", {})
        if group_signature(listed["data"]["groups"]) != before_groups:
            raise SystemExit("protected groups changed after create")
        proof_workspace = next(
            item for item in listed["data"]["workspaces"] if item["ref"] == created_workspace
        )
        proof_group = next(item for item in listed["data"]["groups"] if item["ref"] == group_ref)
        if proof_workspace.get("group_ref") != group_ref:
            raise SystemExit(f"group_ref missing: {json.dumps(proof_workspace)}")
        if created_workspace not in proof_group["member_workspace_refs"]:
            raise SystemExit("proof workspace is not a group member")
        if any(ref in before_refs for ref in proof_group["member_workspace_refs"]):
            raise SystemExit("group members include a pre-existing workspace")
        selected = [item["ref"] for item in listed["data"]["workspaces"] if item.get("selected")]
        if created_workspace in selected:
            raise SystemExit("proof workspace is selected")

        deadline = time.time() + 60
        while time.time() < deadline:
            screen = mcp.call_tool("screen", {"workspace": created_workspace, "surface": surface})
            if "❯" in screen["data"]["text"] and "Grok" in screen["data"]["text"]:
                break
            time.sleep(1)
        else:
            raise SystemExit("prompt box did not appear")

        arguments = {
            "workspace": created_workspace,
            "surface": surface,
            "target": "prompt",
            "path": str(PAYLOAD),
        }
        placed = mcp.call_tool("input", arguments)
        if "CMUXMCP-PROOF-LINE-1" in json.dumps(arguments) or "CMUXMCP-PROOF-LINE-1" in placed["raw"]:
            raise SystemExit("file body leaked into the tool call or result")
        if placed["data"].get("submitted") is not False:
            raise SystemExit(f"file placement submitted: {placed['raw']}")
        time.sleep(0.4)
        seen = mcp.call_tool(
            "screen",
            {"workspace": created_workspace, "surface": surface, "lines": 16},
        )
        text = seen["data"]["text"]
        for line in file_text.splitlines():
            if line not in text:
                raise SystemExit(f"screen missing {line!r}\n{text}")
        if "Enter:send" not in text:
            raise SystemExit(f"prompt box did not stay unsent\n{text}")

        mcp.call_tool("workspace_close", {"workspace": created_workspace})
        created_workspace = None
        after_close = mcp.call_tool("workspace_list", {})
        if any(item["id"] == created_id for item in after_close["data"]["workspaces"]):
            raise SystemExit("proof workspace id is still listed")
        still = next((item for item in after_close["data"]["groups"] if item["ref"] == group_ref), None)
        if still is None:
            raise SystemExit("group disappeared when the proof workspace closed")
        extra = [ref for ref in still["member_workspace_refs"] if ref != anchor_ref]
        if extra:
            raise SystemExit(f"group still has members other than the anchor: {extra}")

        mcp.call_tool(
            "group",
            {"action": "ungroup", "group": group_ref, "remove_generated_anchor": True},
        )
        group_ref = None
        final = None
        final_refs: set[str] = set()
        deadline = time.time() + 5
        while True:
            final = mcp.call_tool("workspace_list", {})
            final_refs = {item["ref"] for item in final["data"]["workspaces"]}
            group_gone = not any(item.get("name") == "cmux-mcp-proof" for item in final["data"]["groups"])
            if group_gone and anchor_ref not in final_refs:
                break
            if time.time() >= deadline:
                break
            time.sleep(0.25)
        final_ids = {item["id"] for item in final["data"]["workspaces"]}
        for keep in KEEP_WORKSPACES:
            if keep not in final_refs:
                raise SystemExit(f"protected workspace disappeared: {keep}")
        if created_id in final_ids:
            raise SystemExit("closed workspace id came back")
        if any(item.get("name") == "cmux-mcp-proof" for item in final["data"]["groups"]):
            raise SystemExit("proof group still exists")
        if anchor_ref in final_refs:
            raise SystemExit(f"generated anchor {anchor_ref} is still open")
        if group_signature(final["data"]["groups"]) != before_groups:
            raise SystemExit("protected groups differ after cleanup")

        proof = {
            "server": init["serverInfo"],
            "protected_group_names": list(KEEP_GROUPS),
            "create_arguments": created["arguments"],
            "workspace": proof_workspace,
            "group": proof_group,
            "input_arguments": arguments,
            "input_result": placed["data"],
            "file_text": file_text,
            "screen": text,
            "selected_during_proof": selected,
            "final_has_38": "workspace:38" in final_refs,
            "final_has_39": "workspace:39" in final_refs,
        }
        out = PAYLOAD.parent / "proof-pass2.json"
        out.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(out)
    finally:
        try:
            if created_workspace and created_workspace not in KEEP_WORKSPACES and created_workspace != anchor_ref:
                mcp.call_tool("workspace_close", {"workspace": created_workspace})
            if group_ref:
                current = mcp.call_tool("workspace_list", {})
                match = next((item for item in current["data"]["groups"] if item["ref"] == group_ref), None)
                if match is not None:
                    members = list(match["member_workspace_refs"])
                    foreign = [ref for ref in members if ref in before_refs or ref in KEEP_WORKSPACES]
                    anchor_only = anchor_ref is not None and set(members) <= {anchor_ref}
                    if foreign:
                        print(f"refusing to dissolve {group_ref}; foreign members {foreign}", file=sys.stderr)
                    elif anchor_only:
                        mcp.call_tool(
                            "group",
                            {
                                "action": "ungroup",
                                "group": group_ref,
                                "remove_generated_anchor": True,
                            },
                        )
                    else:
                        print(
                            f"refusing to remove the anchor while {group_ref} still has {members}",
                            file=sys.stderr,
                        )
        except Exception as exc:
            print(f"cleanup failed: {exc}", file=sys.stderr)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
