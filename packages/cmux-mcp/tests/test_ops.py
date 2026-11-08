"""Rules that must hold before any bytes reach cmux."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from cmux_mcp.client import CmuxError
from cmux_mcp.ops import CmuxOps, SHORT_TEXT_MAX


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.workspaces = [
            {
                "ref": "workspace:7",
                "id": "WS-7",
                "title": "proof",
                "custom_title": "proof",
                "current_directory": "/tmp/proof",
                "selected": False,
            }
        ]
        self.surfaces = [
            {
                "ref": "surface:7",
                "id": "SF-7",
                "type": "terminal",
                "title": "grok",
                "pane_ref": "pane:7",
                "selected_in_pane": True,
                "focused": True,
                "socket_binding": "bound",
            }
        ]
        self.path = "/tmp/cmux.sock"
        self.lists = 0
        self.swap_ref_on_second_list = False
        self.groups: list[dict] = []

    def ping(self) -> bool:
        return True

    def call(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if method == "workspace.list":
            self.lists += 1
            rows = [dict(item) for item in self.workspaces]
            if self.swap_ref_on_second_list and self.lists >= 2 and rows:
                rows[0]["ref"] = "workspace:99"
            return {"workspaces": rows}
        if method == "surface.list":
            return {"surfaces": self.surfaces}
        if method == "workspace.close":
            self.workspaces = [item for item in self.workspaces if item["id"] != params["workspace_id"]]
            return {}
        if method == "surface.read_text":
            return {"text": "│ ❯ hello\n"}
        if method == "debug.terminals":
            return {"terminals": []}
        return {}

    def send_text(self, workspace_id: str, surface_id: str, text: str) -> dict:
        self.calls.append(("send_text", {"workspace_id": workspace_id, "surface_id": surface_id, "text": text}))
        return {}

    def send_key(self, workspace_id: str, surface_id: str, key: str) -> dict:
        self.calls.append(("send_key", key))
        return {}

    def paste(self, workspace_id: str, surface_id: str, text: str) -> None:
        self.calls.append(("paste", {"workspace_id": workspace_id, "surface_id": surface_id, "text": text}))

    def cli(self, args: list[str]) -> str:
        self.calls.append(("cli", list(args)))
        return "OK workspace:7\n"

    def json_cli(self, args: list[str]) -> dict:
        self.calls.append(("json_cli", list(args)))
        if args[:3] == ["workspace-group", "list", "--json"]:
            return {"window_ref": "window:1", "groups": self.groups}
        if args[:3] == ["workspace-group", "create", "--json"]:
            name = args[args.index("--name") + 1]
            group = {
                "ref": "workspace_group:5",
                "name": name,
                "anchor_workspace_ref": "workspace:8",
                "member_workspace_refs": ["workspace:8"],
                "member_count": 1,
                "is_collapsed": False,
                "is_pinned": False,
            }
            self.groups.append(group)
            return {"ok": True}
        return {}


class OpsTests(unittest.TestCase):
    def test_file_placement_reads_disk_and_does_not_submit(self) -> None:
        client = FakeClient()
        ops = CmuxOps(client)
        body = "CMUXMCP-PROOF-LINE-1\nCMUXMCP-PROOF-LINE-2 stays in the box\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(body)
            path = handle.name
        result = ops.input_bytes("workspace:7", "prompt", path=path)
        self.assertFalse(result["submitted"])
        self.assertEqual(result["path"], path)
        self.assertNotIn("CMUXMCP-PROOF-LINE-1", json.dumps({k: v for k, v in result.items() if k != "path"}))
        pasted = [item for item in client.calls if item[0] == "paste"]
        self.assertEqual(pasted[0][1]["text"], body)
        self.assertFalse(any(item[0] in {"send_key", "send_text"} for item in client.calls))

    def test_a_long_file_is_one_paste_and_an_old_cmux_types_it(self) -> None:
        from cmux_mcp.client import CmuxError

        body = "".join(f"line {index:04d} ⑂ {'x' * (index % 90)}\n" for index in range(300))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(body)
            path = handle.name
        client = FakeClient()
        CmuxOps(client).input_bytes("workspace:7", "prompt", path=path)
        self.assertEqual([item[1]["text"] for item in client.calls if item[0] == "paste"], [body])

        class OldCmux(FakeClient):
            def paste(self, workspace_id, surface_id, text):
                raise CmuxError("Unknown command: paste")

        old = OldCmux()
        CmuxOps(old).input_bytes("workspace:7", "prompt", path=path)
        self.assertEqual("".join(item[1]["text"] for item in old.calls if item[0] == "send_text"), body)

    def test_file_body_cannot_ride_along_as_text(self) -> None:
        client = FakeClient()
        ops = CmuxOps(client)
        bulky = "x" * (SHORT_TEXT_MAX + 5)
        with self.assertRaises(CmuxError):
            ops.input_bytes("workspace:7", "prompt", text=bulky)
        with self.assertRaises(CmuxError):
            ops.input_bytes("workspace:7", "prompt", text="one\ntwo")
        self.assertFalse(any(item[0] == "send_text" for item in client.calls))

    def test_path_refuses_submit_and_terminal_target(self) -> None:
        client = FakeClient()
        ops = CmuxOps(client)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("hello\n")
            path = handle.name
        with self.assertRaises(CmuxError):
            ops.input_bytes("workspace:7", "prompt", path=path, submit=True)
        with self.assertRaises(CmuxError):
            ops.input_bytes("workspace:7", "terminal", path=path)
        self.assertFalse(any(item[0] == "send_text" for item in client.calls))

    def test_short_line_can_submit(self) -> None:
        client = FakeClient()
        ops = CmuxOps(client)
        result = ops.input_bytes("workspace:7", "terminal", text="pwd", submit=True)
        self.assertTrue(result["submitted"])
        self.assertEqual([item for item in client.calls if item[0] == "send_key"], [("send_key", "enter")])

    def test_close_requires_a_named_workspace_and_rechecks_identity(self) -> None:
        client = FakeClient()
        ops = CmuxOps(client)
        with self.assertRaises(CmuxError):
            ops.workspace_close("")
        with self.assertRaises(CmuxError):
            ops.workspace_close("41")
        client.swap_ref_on_second_list = True
        with self.assertRaises(CmuxError):
            ops.workspace_close("workspace:7")
        self.assertFalse(any(item[0] == "workspace.close" for item in client.calls))

    def test_close_uses_the_uuid(self) -> None:
        client = FakeClient()
        ops = CmuxOps(client)
        result = ops.workspace_close("workspace:7")
        self.assertEqual(result["id"], "WS-7")
        self.assertIn(("workspace.close", {"workspace_id": "WS-7"}), client.calls)
        self.assertEqual(client.workspaces, [])

    def test_workspace_list_joins_group_and_strips_chat(self) -> None:
        client = FakeClient()
        client.workspaces[0]["latest_conversation_message"] = "SECRET CHAT"
        client.workspaces[0]["latest_submitted_message"] = "SECRET SUBMIT"
        client.groups = [
            {
                "ref": "workspace_group:1",
                "name": "Example",
                "anchor_workspace_ref": "workspace:7",
                "member_workspace_refs": ["workspace:7"],
                "member_count": 1,
                "is_collapsed": False,
                "is_pinned": False,
            }
        ]
        listed = CmuxOps(client).workspace_list()
        encoded = json.dumps(listed)
        self.assertNotIn("SECRET", encoded)
        self.assertEqual(listed["workspaces"][0]["group_ref"], "workspace_group:1")
        self.assertEqual(listed["groups"][0]["name"], "Example")
        self.assertEqual(listed["groups"][0]["member_count"], 1)

    def test_workspace_create_omits_unset_flags(self) -> None:
        client = FakeClient()
        directory = tempfile.mkdtemp()
        CmuxOps(client).workspace_create(
            {"cwd": directory, "group": "workspace_group:1", "focus": False}
        )
        args = next(item[1] for item in client.calls if item[0] == "cli")
        self.assertIn("--group", args)
        self.assertNotIn("--command", args)
        self.assertNotIn("--group-placement", args)
        self.assertNotIn("--description", args)
        self.assertNotIn("--env", args)
        self.assertEqual(args[args.index("--focus") + 1], "false")

    def test_group_create_does_not_capture_workspaces(self) -> None:
        client = FakeClient()
        created = CmuxOps(client).group_action({"action": "create", "name": "cmux-mcp-proof"})
        args = next(item[1] for item in client.calls if item[0] == "json_cli" and "create" in item[1])
        self.assertNotIn("--from", args)
        self.assertEqual(created["group"]["name"], "cmux-mcp-proof")
        self.assertEqual(created["group"]["member_workspace_refs"], ["workspace:8"])

    def test_group_delete_leaves_members_unless_asked(self) -> None:
        client = FakeClient()
        ops = CmuxOps(client)
        ops.group_action({"action": "delete", "group": "workspace_group:5"})
        plain = next(item[1] for item in client.calls if item[0] == "json_cli" and "delete" in item[1])
        self.assertNotIn("--close-workspaces", plain)
        ops.group_action(
            {"action": "delete", "group": "workspace_group:5", "close_members": True}
        )
        closing = [item[1] for item in client.calls if item[0] == "json_cli" and "delete" in item[1]][-1]
        self.assertIn("--close-workspaces", closing)
        ungroup = ops.group_action(
            {
                "action": "ungroup",
                "group": "workspace_group:5",
                "remove_generated_anchor": True,
            }
        )
        args = [item[1] for item in client.calls if item[0] == "json_cli" and "ungroup" in item[1]][-1]
        self.assertIn("--remove-generated-anchor", args)
        self.assertEqual(ungroup["action"], "ungroup")

    def test_relative_path_is_refused(self) -> None:
        ops = CmuxOps(FakeClient())
        with self.assertRaises(CmuxError):
            ops.input_bytes("workspace:7", "prompt", path="payload.txt")


class ServerFramingTests(unittest.TestCase):
    def test_newline_initialize_answers_immediately(self) -> None:
        import subprocess

        root = Path(__file__).resolve().parents[1]
        proc = subprocess.Popen(
            [sys.executable, "-m", "cmux_mcp"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin and proc.stdout
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        try:
            proc.stdin.write((json.dumps(payload) + "\n").encode())
            proc.stdin.flush()
            line = proc.stdout.readline()
            self.assertTrue(line.startswith(b"{"), line)
            message = json.loads(line)
            self.assertEqual(message["result"]["serverInfo"]["name"], "cmux-mcp")
            self.assertNotIn(b"Content-Length", line)
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_server_starts_and_lists_the_berth_tools(self) -> None:
        import subprocess

        root = Path(__file__).resolve().parents[1]
        proc = subprocess.Popen(
            [sys.executable, "-m", "cmux_mcp"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin and proc.stdout

        def send(payload: dict) -> None:
            data = json.dumps(payload).encode()
            proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
            proc.stdin.flush()

        def recv() -> dict:
            headers = {}
            while True:
                line = proc.stdout.readline()
                if line in (b"\r\n", b"\n"):
                    break
                key, value = line.split(b":", 1)
                headers[key.strip().lower()] = value.strip()
            length = int(headers[b"content-length"])
            return json.loads(proc.stdout.read(length))

        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                }
            )
            init = recv()
            self.assertEqual(init["result"]["serverInfo"]["name"], "cmux-mcp")
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            listed = recv()
            names = [tool["name"] for tool in listed["result"]["tools"]]
            self.assertEqual(
                names,
                [
                    "status",
                    "check",
                    "folders",
                    "list",
                    "get",
                    "mkdir",
                    "rmdir",
                    "move_folder",
                    "rename_folder",
                    "move",
                    "rename",
                    "create",
                    "play",
                    "fork",
                    "pause",
                    "cycle",
                    "sleep",
                    "wake",
                    "park_folder",
                    "restore",
                    "delete",
                    "trash",
                    "untrash",
                    "focus",
                    "screen",
                    "input",
                    "excerpt",
                    "peers",
                    "offer",
                    "preview",
                    "merge",
                ],
            )
            by_name = {tool["name"]: tool for tool in listed["result"]["tools"]}
            path_schema = by_name["input"]["inputSchema"]["properties"]["path"]
            self.assertNotIn("workspace_create", by_name)
            self.assertNotIn("group", by_name)
            self.assertIn("Absolute", path_schema["description"])
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "workspace_close", "arguments": {}},
                }
            )
            refused = recv()
            self.assertTrue(refused["result"]["isError"])
            self.assertIn("workspace", refused["result"]["content"][0]["text"])
        finally:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
