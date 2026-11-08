"""The watcher starts almost no processes now: cmux over its socket, one ps a second, pid checks by signal (2026-11-03)."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from berth import proc
from berth.cmux import Cmux
from berth.place import pid_alive


class FakeSocket:
    """A stand-in for cmux's socket: answers the three requests the watcher sends each tick."""

    def __init__(self, answers: dict) -> None:
        self.path = Path(tempfile.mkdtemp(dir="/tmp")) / "s"
        self.answers, self.requests = answers, []
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(self.path))
        self.server.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            line = b""
            while not line.endswith(b"\n"):
                chunk = conn.recv(1 << 16)
                if not chunk:
                    break
                line += chunk
            request = json.loads(line)
            self.requests.append((request["method"], request["params"], line.decode().index('"method"') <
                                  line.decode().index('"params"')))
            answer = self.answers.get(request["method"])
            reply = {"id": request["id"], "ok": answer is not None,
                     **({"result": answer} if answer is not None else {"error": {"message": "unknown method"}})}
            conn.sendall((json.dumps(reply) + "\n").encode())
            conn.close()


WORKSPACES = {"window_id": "win-1", "workspaces": [{"id": "WS-1", "ref": "workspace:1", "title": "One", "index": 0,
                                                     "current_directory": "/tmp", "selected": True}]}
TREE = {"windows": [{"workspaces": [{"id": "WS-1", "panes": [{"surfaces": [{"tty": "/dev/ttys001"}]}]}]}]}


@pytest.fixture
def no_processes(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"started a process: {args[0] if args else kwargs}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setenv("BERTH_CMUX_SOCKET", "on")


def test_the_watchers_reads_go_over_the_socket_without_starting_a_process(no_processes):
    fake = FakeSocket({"workspace.list": WORKSPACES, "system.tree": TREE,
                       "surface.read_text": {"base64": base64.b64encode("❯ hello".encode()).decode()}})
    cmux = Cmux(binary="/nonexistent/cmux", socket=fake.path)
    [workspace] = cmux.list_workspaces()
    assert (workspace.id, workspace.title, workspace.selected) == ("WS-1", "One", True)
    assert cmux.read_screen("WS-1", lines=40) == "❯ hello\n"  # as the CLI printed it
    assert cmux.terminals() == {"ws-1": ["/dev/ttys001"]}
    assert [(method, params) for method, params, _ in fake.requests] == [
        ("workspace.list", {}), ("surface.read_text", {"workspace_id": "WS-1", "lines": 40, "scrollback": True}),
        ("system.tree", {"all_windows": False})]
    assert all(first for _, _, first in fake.requests)  # method before params: cmux #21222


def test_a_socket_that_refuses_falls_back_to_the_command_line(monkeypatch):
    fake = FakeSocket({})  # answers every request with an error
    calls = []

    def run(self, args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(WORKSPACES), stderr="")

    monkeypatch.setattr(Cmux, "_run", run)
    monkeypatch.setenv("BERTH_CMUX_SOCKET", "on")
    assert [item.id for item in Cmux(socket=fake.path).list_workspaces()] == ["WS-1"]
    assert calls == [["workspace", "list", "--json", "--id-format", "both"]]


def test_one_ps_a_second_answers_every_terminal_and_command(monkeypatch):
    proc.forget()
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "comm=" in args[-1]:
            out = ("  101 Ss+  ttys001  /usr/bin/login\n  102 S+   ttys001  /Users/you/.local/bin/claude\n"
                   "  103 S    ttys001  /bin/zsh\n  104 S+   ??       /usr/sbin/cfprefsd\n")
        else:
            out = "  101 login -pf you\n  102 claude --name Desk\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert proc.foreground("/dev/ttys001") == ["/usr/bin/login", "/Users/you/.local/bin/claude"]
    assert proc.foreground("ttys001") == proc.foreground("ttys001")
    assert proc.command(102) == "claude --name Desk" and proc.command("101") == "login -pf you"
    assert proc.command(999) == ""
    assert len(calls) == 2  # one table of each kind, however many questions
    proc.forget()


def test_a_pid_is_checked_with_a_signal(no_processes):
    assert pid_alive(os.getpid()) is True
    assert pid_alive(1) is True  # another user's process: not permitted, but there
    assert pid_alive(2 ** 22) is False and pid_alive(None) is False and pid_alive(0) is False


def test_the_command_line_says_who_asked_for_a_cycle(catalog, capsys, monkeypatch):
    """2026-11-03: 39 cycles at 15:06 said only "berth cycle"."""
    from berth.cli import main
    from berth.deferred import pending
    from tests.fakes import FakeCmux
    from tests.test_state import _insert

    monkeypatch.setattr("berth.cli.Cmux", FakeCmux)
    _insert(catalog, id="steward", title="Accounts Agent", state="live", cmux_workspace_id="WS-ST")
    _insert(catalog, id="target", title="Target", state="live", cmux_workspace_id="WS-T", kind="other-agent",
            agent_name="claude")
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-st")
    assert main(["--catalog", str(catalog.path), "cycle", "--json", "target", "--when", "idle"]) == 0
    assert pending(catalog, "target")[0]["requested_by"] == "Accounts Agent (berth cycle)"
    monkeypatch.delenv("CMUX_WORKSPACE_ID")
    assert main(["--catalog", str(catalog.path), "cycle", "--json", "target", "--when", "idle"]) == 0
    assert pending(catalog, "target")[0]["requested_by"].startswith(f"berth cycle from pid {os.getppid()}: ")


def test_watcher_errors_are_dated():
    import io
    import re

    from berth.health import log_error

    stream = io.StringIO()
    try:
        raise RuntimeError("database is locked")
    except RuntimeError:
        log_error(stream, "names failed")
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ berth watch: names failed\n", stream.getvalue())
    assert "database is locked" in stream.getvalue()
