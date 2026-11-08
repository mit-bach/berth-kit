"""cmux unix-socket client and the few CLI calls the socket create path does not document."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

REQUEST_TIMEOUT_S = 20.0
# cmux 0.65.2 drops a request over 4 KiB with this message when its method comes last, delivering nothing
# (CmuxClient.paste; the CLI's "Socket closed before reply", a closed connection on this client's socket).
PASTE_DROPPED = "Socket closed before reply"
PASTE_ATTEMPTS = 8


class CmuxError(Exception):
    def __init__(self, message: str, code: str = "cmux_error") -> None:
        super().__init__(message)
        self.code = code


def socket_path() -> str:
    env = os.environ.get("CMUX_SOCKET_PATH")
    if env and os.path.exists(env):
        return env
    state = Path.home() / ".local" / "state" / "cmux"
    last = state / "last-socket-path"
    if last.is_file():
        recorded = last.read_text(encoding="utf-8").strip()
        if recorded and os.path.exists(recorded):
            return recorded
    uid = os.getuid()
    candidates = [
        state / f"cmux-{uid}.sock",
        state / "cmux.sock",
        Path.home() / "Library" / "Application Support" / "cmux" / "cmux.sock",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    if env:
        return env
    return str(state / "cmux.sock")


def cmux_bin() -> str:
    candidates = [
        os.environ.get("CMUX_BUNDLED_CLI_PATH"),
        "/opt/homebrew/bin/cmux",
        "/Applications/cmux.app/Contents/Resources/bin/cmux",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("cmux")
    if found:
        return found
    raise CmuxError("cmux CLI not found")


class CmuxClient:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or socket_path()
        self._sock: socket.socket | None = None
        self._buf = b""

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._buf = b""

    def ping(self) -> bool:
        try:
            result = self.call("system.ping", {})
        except CmuxError:
            return False
        return result.get("pong") is True

    def call(self, method: str, params: dict[str, Any], timeout_s: float = REQUEST_TIMEOUT_S) -> dict[str, Any]:
        last_error: CmuxError | None = None
        for attempt in range(2):
            try:
                return self._call_once(method, params, timeout_s)
            except CmuxError as exc:
                last_error = exc
                self.close()
                if exc.code != "rate_limited" or attempt == 1:
                    if exc.code == "connection_error" and attempt == 0:
                        continue
                    raise
                time.sleep(0.2)
        assert last_error is not None
        raise last_error

    def _connect(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(REQUEST_TIMEOUT_S)
        try:
            sock.connect(self.path)
        except OSError as exc:
            sock.close()
            raise CmuxError(
                f"cmux socket unavailable at {self.path} ({exc})",
                "connection_error",
            ) from exc
        self._sock = sock
        return sock

    def _call_once(self, method: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        sock = self._connect()
        sock.settimeout(timeout_s)
        request_id = str(uuid.uuid4())
        payload = json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
        try:
            sock.sendall(payload.encode())
        except OSError as exc:
            raise CmuxError(f"socket write failed: {exc}", "connection_error") from exc
        while True:
            while b"\n" in self._buf:
                line, _, rest = self._buf.partition(b"\n")
                self._buf = rest
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != request_id:
                    continue
                if message.get("ok") is False:
                    error = message.get("error") or {}
                    code = str(error.get("code") or "cmux_error")
                    text = str(error.get("message") or "socket call failed")
                    raise CmuxError(text, code)
                result = message.get("result")
                if isinstance(result, dict):
                    return result
                return {"value": result}
            try:
                chunk = sock.recv(1 << 20)
            except socket.timeout as exc:
                raise CmuxError(f"{method} timed out", "timeout") from exc
            except OSError as exc:
                raise CmuxError(f"socket read failed: {exc}", "connection_error") from exc
            if not chunk:
                raise CmuxError("cmux socket closed", "connection_error")
            self._buf += chunk

    def send_text(self, workspace_id: str, surface_id: str, text: str) -> dict[str, Any]:
        return self.call(
            "surface.send_text",
            {"workspace_id": workspace_id, "surface_id": surface_id, "text": text},
        )

    def send_key(self, workspace_id: str, surface_id: str, key: str) -> dict[str, Any]:
        return self.call(
            "surface.send_key",
            {"workspace_id": workspace_id, "surface_id": surface_id, "key": key},
        )

    def paste(self, workspace_id: str, surface_id: str, text: str) -> None:
        """The Cmd+V path, never submitted, over this client's own socket.

        A program that turned on bracketed paste (Claude Code, Grok) gets the
        whole text as one paste, so newlines stay inside it and nothing is
        dropped. ``terminal.paste`` with ``submit_key`` ``none`` is exactly
        what ``cmux paste`` sends (2026-10-31, read off the socket).

        Until 2026-10-31 this ran the bundled ``cmux paste`` CLI. cmux 0.65.2
        drops a request over 4 KiB whose ``method`` comes after a large
        ``params`` ("Socket closed before reply", nothing delivered), and the
        CLI orders the keys at random, so a 6-8 KB prompt file failed one try
        in two or every time (cmux issue #21222; the fix, PR #21345, is in no
        stable release yet). ``_call_once`` writes ``method`` first. Dominik
        chose this route on 2026-10-30.

        The CLI's two checks come first, with its words: an agent prompt that
        holds text someone is typing, or a question or dialog waiting on a
        human, and nothing is sent. A dropped connection is still tried
        again, up to ``PASTE_ATTEMPTS`` times: a dropped paste delivers nothing.
        """
        where = {"workspace_id": workspace_id, "surface_id": surface_id}
        state = self.call("surface.input_state", dict(where))
        if state.get("waiting_on_human"):
            raise CmuxError(f"paste: {surface_id} is waiting on a question or dialog, so nothing was sent. "
                            "Retry once it is answered.", "busy")
        if state.get("agent") and state.get("state") not in (None, "", "empty"):
            raise CmuxError(f"paste: the agent prompt in {surface_id} holds text someone is typing, so nothing was "
                            "sent. Retry when the prompt is clear.", "busy")
        for attempt in range(PASTE_ATTEMPTS):
            try:
                self.call("terminal.paste", {**where, "text": text, "submit_key": "none"})
                return
            except CmuxError as exc:
                if exc.code != "connection_error" or attempt == PASTE_ATTEMPTS - 1:
                    raise
                time.sleep(0.2)

    def cli(self, args: list[str], stdin: str | None = None, *, socket_flag: bool = True) -> str:
        env = os.environ.copy()
        env["CMUX_QUIET"] = "1"
        env["CMUX_SOCKET_PATH"] = self.path
        socket = ["--socket", self.path] if socket_flag else []
        try:
            completed = subprocess.run(
                [cmux_bin(), *socket, *args],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                input=stdin,
            )
        except OSError as exc:
            raise CmuxError(f"failed to run cmux: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise CmuxError(detail or f"cmux exited {completed.returncode}")
        return completed.stdout

    def json_cli(self, args: list[str]) -> Any:
        return parse_cli_json(self.cli(args))


def parse_cli_json(output: str) -> Any:
    text = output.strip()
    if not text:
        return {}
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            return json.loads(text[index:])
        except json.JSONDecodeError:
            continue
    raise CmuxError(f"cmux did not return JSON: {text[:400]}")


_WORKSPACE_REF = re.compile(r"workspace:\d+")


def parse_workspace_ref(output: str) -> str:
    found = _WORKSPACE_REF.findall(output)
    if not found:
        raise CmuxError(f"workspace create did not return a workspace ref: {output.strip()}")
    return found[-1]
