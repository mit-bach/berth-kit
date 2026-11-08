"""cmux_mcp.client against a stand-in cmux binary."""

from __future__ import annotations

import json
import os
import socket
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cmux_mcp import client
from cmux_mcp.client import CmuxClient, CmuxError

class FakeSocket:
    """A stand-in cmux socket: answers input_state as told, records each request line, drops pastes as told."""

    def __init__(self, input_state: dict, drops: int = 0) -> None:
        self.directory = Path(tempfile.mkdtemp(dir="/tmp"))
        self.path = str(self.directory / "s")
        self.input_state, self.drops, self.lines = input_state, drops, []
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(self.path)
        self.server.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            buf = b""
            while True:
                data = conn.recv(1 << 20)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    self.lines.append(line.decode())
                    request = json.loads(line)
                    if request["method"] == "terminal.paste" and self.drops > 0:
                        self.drops -= 1
                        conn.close()  # what cmux 0.65.2 does to a large request
                        break
                    result = self.input_state if request["method"] == "surface.input_state" else {}
                    conn.sendall((json.dumps({"id": request["id"], "ok": True, "result": result}) + "\n").encode())
                else:
                    continue
                break

    def requests(self) -> list[dict]:
        return [json.loads(line) for line in self.lines]


IDLE = {"terminal": True, "agent": True, "state": "empty", "blocks_typing": False, "waiting_on_human": False}


class PasteTest(unittest.TestCase):
    """2026-10-31: pastes go over cmux-mcp's own socket, method first, so cmux 0.65.2 never drops a big one."""

    def paste(self, fake: FakeSocket, text: str = "x" * 8000):
        with mock.patch.object(client.time, "sleep"):
            CmuxClient(fake.path).paste("ws", "sf", text)

    def test_a_large_paste_goes_whole_unsubmitted_with_method_before_params(self):
        fake = FakeSocket(IDLE)
        text = "line one\n" + "y" * 8000 + "\nlast line"
        self.paste(fake, text)
        methods = [item["method"] for item in fake.requests()]
        self.assertEqual(methods, ["surface.input_state", "terminal.paste"])
        sent = fake.requests()[1]["params"]
        self.assertEqual((sent["text"], sent["submit_key"]), (text, "none"))
        self.assertLess(fake.lines[1].index('"method"'), fake.lines[1].index('"params"'))

    def test_a_draft_or_a_dialog_is_never_pasted_over(self):
        for state, words in (({**IDLE, "state": "draft"}, "someone is typing"),
                             ({**IDLE, "waiting_on_human": True}, "question or dialog")):
            fake = FakeSocket(state)
            with self.assertRaises(CmuxError) as refused:
                self.paste(fake)
            self.assertIn(words, str(refused.exception))
            self.assertEqual([item["method"] for item in fake.requests()], ["surface.input_state"])

    def test_a_shell_prompt_takes_a_paste_whatever_it_holds(self):
        fake = FakeSocket({"terminal": True, "agent": False, "state": "unknown", "waiting_on_human": False})
        self.paste(fake)
        self.assertEqual(fake.requests()[-1]["method"], "terminal.paste")

    def test_a_dropped_paste_is_tried_again_and_arrives_once(self):
        fake = FakeSocket(IDLE, drops=2)
        self.paste(fake)
        pastes = [item for item in fake.requests() if item["method"] == "terminal.paste"]
        self.assertEqual(len(pastes), 3)  # two dropped, delivering nothing; the third answered


if __name__ == "__main__":
    unittest.main()
