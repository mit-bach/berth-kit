"""cmux CLI client.

The socket that answered on this machine is ``~/.local/state/cmux/cmux.sock``.
The public doc still mentions ``/tmp/cmux.sock``. This client uses the socket
that exists, and honors ``CMUX_SOCKET_PATH`` when that is set.

Flags used here were read from ``cmux workspace``, ``cmux events``,
``cmux memory``, and ``cmux read-screen`` on 0.64.25. ``workspace create``
takes the same flags as ``new-workspace``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from berth.errors import BerthError
from berth.model import Group, Surface, Workspace

_SHELLS = frozenset({"login", "zsh", "bash", "sh", "fish", "dash", "tcsh", "csh", "ksh", "nu"})
# cmux 0.65.2 drops a paste of a few KB with this message and delivers nothing (Cmux.paste_text).
PASTE_DROPPED = "Socket closed before reply"
PASTE_ATTEMPTS = 8

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def default_socket() -> Path:
    override = os.environ.get("CMUX_SOCKET_PATH")
    if override:
        return Path(override).expanduser()
    state = Path.home() / ".local" / "state" / "cmux" / "cmux.sock"
    legacy = Path("/tmp/cmux.sock")
    if state.exists():
        return state
    if legacy.exists():
        return legacy
    return state


# Where cmux installs its CLI. A program with a bare PATH (a LaunchAgent, Slack hooks) still finds it
# (2026-10-30: `berth send` from an empty environment failed with "cmux binary not found").
INSTALLED = ("/Applications/cmux.app/Contents/Resources/bin/cmux", "/opt/homebrew/bin/cmux", "/usr/local/bin/cmux")


def _installed() -> str | None:
    return next((path for path in INSTALLED if Path(path).exists()), None)


class Cmux:
    def __init__(self, binary: str | None = None, socket: Path | None = None) -> None:
        self.binary = binary or shutil.which("cmux") or _installed() or "cmux"
        self.socket = socket or default_socket()
        self.window_id: str | None = None

    def socket_up(self) -> bool:
        return self.socket.exists()

    def app_process(self) -> tuple[int, str] | None:
        """The running cmux app as (pid, start time). None when it is not running.

        This is cmux's identity. The socket is not: cmux recreates its socket
        file while running (on every wake from sleep), and a sleeping cmux
        stops answering without having quit. Raises OSError when the process
        table cannot be read, so the caller can tell "gone" from "unknown".
        """
        result = subprocess.run(["ps", "-axo", "pid=,lstart=,command="], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise OSError("ps failed")
        found = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 6)
            if len(parts) < 7 or not parts[0].isdigit():
                continue
            if parts[6].split()[0].endswith(".app/Contents/MacOS/cmux"):
                found.append((int(parts[0]), " ".join(parts[1:6])))
        return min(found) if found else None

    def pane_has_agent(self, workspace: "Workspace") -> bool:
        """True when anything other than a shell runs in the workspace's panes."""
        from berth.claude import pane_pids, process_tree

        try:
            pids = pane_pids(process_tree(self.top_processes()), workspace.ref, workspace.id)
        except BerthError:
            return True  # cannot see inside: assume it is running something
        if not pids:
            return False
        result = subprocess.run(
            ["ps", "-o", "comm=", "-p", ",".join(str(pid) for pid in pids)],
            capture_output=True, text=True, check=False,
        )
        for name in result.stdout.split():
            base = name.rsplit("/", 1)[-1].lstrip("-")
            if base not in _SHELLS:
                return True
        return False

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CMUX_SOCKET_PATH"] = str(self.socket)
        return env

    def _run(self, args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        if shutil.which(self.binary) is None and not Path(self.binary).exists():
            raise BerthError("CMUX_DOWN", f"cmux binary not found ({self.binary})", {})
        if not self.socket_up():
            raise BerthError(
                "CMUX_DOWN",
                f"cmux socket is missing at {self.socket}",
                {"socket": str(self.socket)},
            )
        try:
            result = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                env=self._env(),
                check=False,
                input=stdin,
            )
        except OSError as exc:
            raise BerthError("CMUX_DOWN", str(exc), {"socket": str(self.socket)}) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "cmux rejected the command").strip()
            lowered = message.lower()
            if "no such file" in lowered and "sock" in lowered:
                raise BerthError("CMUX_DOWN", message, {"socket": str(self.socket)})
            raise BerthError(
                "CMUX_REJECTED",
                message,
                {"args": args, "stdout": result.stdout[-1000:], "stderr": result.stderr[-1000:]},
            )
        return result

    def top_processes(self) -> str:
        """TSV of processes cmux attributes to each surface."""
        result = self._run(["top", "--all", "--processes", "--flat", "--format", "tsv"])
        return result.stdout

    def call(self, method: str, params: dict, *, timeout: float = 10.0) -> dict:
        """One request over cmux's socket, as the cmux CLI sends it, without starting the CLI (2026-11-03).

        The watcher read every pane, listed workspaces, and read the tree each
        tick through the CLI: a new process for each, about 25 of the 60 it
        started a second (berth/proc.py). These requests are the CLI's own,
        read off the socket. ``method`` goes before ``params``: cmux 0.65.2
        drops a large request the other way round (cmux #21222).
        """
        import socket
        import uuid

        request_id = str(uuid.uuid4())
        payload = (json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(self.socket))
            sock.sendall(payload)
            buffer = b""
            while True:
                chunk = sock.recv(1 << 20)
                if not chunk:
                    raise BerthError("CMUX_DOWN", f"cmux closed the socket during {method}", {})
                buffer += chunk
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if message.get("id") != request_id:
                        continue
                    if message.get("ok") is False:
                        error = message.get("error") or {}
                        raise BerthError("CMUX_REJECTED", str(error.get("message") or f"{method} refused"),
                                         {"code": error.get("code")})
                    result = message.get("result")
                    return result if isinstance(result, dict) else {"value": result}
        except OSError as exc:
            raise BerthError("CMUX_DOWN", f"cmux socket: {exc}", {"socket": str(self.socket)}) from exc
        finally:
            sock.close()

    def _socket_first(self, method: str, params: dict) -> dict | None:
        """The socket's answer, or None to fall back to the CLI (cmux's own refusal, or a socket that is off)."""
        if os.environ.get("BERTH_CMUX_SOCKET", "on") == "off" or not self.socket_up():
            return None
        try:
            return self.call(method, params)
        except BerthError:
            return None

    def list_workspaces(self) -> list[Workspace]:
        document = self._socket_first("workspace.list", {})
        if document is None:
            result = self._run(["workspace", "list", "--json", "--id-format", "both"])
            try:
                document = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise BerthError("CMUX_REJECTED", "workspace list was not JSON", {}) from exc
        window_id = document.get("window_id") if isinstance(document, dict) else None
        if isinstance(window_id, str):
            self.window_id = window_id
        raw_items = document.get("workspaces") if isinstance(document, dict) else document
        if not isinstance(raw_items, list):
            raise BerthError("CMUX_REJECTED", "workspace list has no workspaces", {})
        workspaces = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            parsed = _workspace(item, window_id if isinstance(window_id, str) else None)
            if parsed is not None:
                workspaces.append(parsed)
        return workspaces

    def list_groups(self) -> list[Group]:
        result = self._run(["workspace-group", "list", "--json"])
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BerthError("CMUX_REJECTED", "group list was not JSON", {}) from exc
        raw = document.get("groups") if isinstance(document, dict) else document
        if not isinstance(raw, list):
            return []
        groups = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            members = item.get("member_workspace_refs") or []
            if not isinstance(members, list):
                members = []
            name = item.get("name")
            if not isinstance(name, str):
                continue
            ref = item.get("ref") if isinstance(item.get("ref"), str) else None
            anchor = item.get("anchor_workspace_ref")
            member_text = [str(member) for member in members]
            if isinstance(anchor, str) and anchor not in member_text:
                member_text.append(anchor)
            groups.append(Group(name=name, ref=ref, members=member_text))
        return groups

    def surfaces(self, workspace_id: str) -> list[Surface]:
        result = self._run(["list-panels", "--workspace", workspace_id, "--json"])
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BerthError("CMUX_REJECTED", "panel list was not JSON", {}) from exc
        raw = document.get("surfaces") if isinstance(document, dict) else None
        if not isinstance(raw, list):
            return []
        surfaces = []
        for item in raw:
            if isinstance(item, dict):
                surfaces.append(_surface(item))
        return surfaces

    def read_screen(self, workspace_id: str, *, lines: int = 120, scrollback: bool = True) -> str:
        params: dict = {"workspace_id": workspace_id}
        if scrollback:
            params.update({"lines": lines, "scrollback": True})
        answer = self._socket_first("surface.read_text", params) if workspace_id else None
        if answer is not None and isinstance(answer.get("base64"), str):
            import base64

            text = base64.b64decode(answer["base64"]).decode("utf-8", errors="replace")
            return text if text.endswith("\n") else text + "\n"  # the CLI prints one newline after it
        args = ["read-screen", "--workspace", workspace_id]
        if scrollback:
            # --lines implies scrollback. The visible viewport is the other call.
            args.extend(["--scrollback", "--lines", str(lines)])
        result = self._run(args)
        return result.stdout

    def close_workspace(self, workspace_id: str) -> None:
        """Close the workspace. Callers decide to close before they call.

        cmux 0.65 refuses a workspace with a running process and asks for
        ``--force``. cmux 0.64 closes it without asking. Retry with
        ``--force`` only on that refusal, so 0.64 never sees the flag.

        cmux also refuses to close a pinned workspace. Berth's pin lives in
        the catalog (berth/pins.py), so the workspace is unpinned and closed;
        the session's next workspace is pinned again when it opens.
        """
        for _ in range(3):
            try:
                self._run(["workspace", "close", workspace_id])
                return
            except BerthError as exc:
                message = str(exc.message)
                if "confirmation_required" in message:
                    self._run(["workspace", "close", workspace_id, "--force"])
                    return
                if "Pinned workspaces can't be closed" in message or "Unpin the workspace" in message:
                    self.pin_workspace(workspace_id, False)
                    continue
                raise
        self._run(["workspace", "close", workspace_id])

    def select_workspace(self, workspace_id: str) -> None:
        self._run(["workspace", "select", workspace_id])

    def set_workspace_color(self, workspace_id: str, hex_color: str | None) -> None:
        if hex_color:
            self._run(
                [
                    "workspace-action",
                    "--workspace",
                    workspace_id,
                    "--action",
                    "set-color",
                    "--color",
                    hex_color,
                ]
            )
            return
        self._run(
            ["workspace-action", "--workspace", workspace_id, "--action", "clear-color"]
        )

    def set_status(self, workspace_id: str, key: str, value: str, *, icon: str | None = None,
                   color: str | None = None, priority: int | None = None) -> None:
        """One keyed status line in the workspace's sidebar row (``cmux set-status``)."""
        args = ["set-status", key, value, "--workspace", workspace_id]
        if icon:
            args += ["--icon", icon]
        if color:
            args += ["--color", color]
        if priority is not None:
            args += ["--priority", str(priority)]
        self._run(args)

    def notify(self, workspace_id: str, *, title: str, subtitle: str = "", body: str = "") -> None:
        """A cmux notification on the workspace: a banner, the unread mark, and a line in its history."""
        self._run(["notify", "--workspace", workspace_id, "--title", title, "--subtitle", subtitle, "--body", body])

    def clear_status(self, workspace_id: str, key: str) -> None:
        self._run(["clear-status", key, "--workspace", workspace_id])

    def set_progress(self, workspace_id: str, value: float, label: str) -> None:
        """The workspace's progress: a value and a label custom sidebars can read (``progress.label``)."""
        self._run(["set-progress", f"{max(0.0, min(1.0, value)):.2f}", "--label", label, "--workspace", workspace_id])

    def clear_progress(self, workspace_id: str) -> None:
        self._run(["clear-progress", "--workspace", workspace_id])

    def paste_text(self, workspace_id: str, text: str, *, force: bool = False) -> None:
        """Put text in the pane's prompt box as one bracketed paste, as ⌘V does. Never submitted.

        cmux refuses when the prompt already holds a draft or a dialog is open,
        which is what a restore wants.

        cmux 0.65.2 drops a paste of more than about 4 KB on a quarter to a half
        of tries, answering "Socket closed before reply" and delivering nothing
        (measured 2026-10-30), so a dropped paste is tried again.
        """
        for attempt in range(PASTE_ATTEMPTS):
            try:
                self._run(["paste", *(["--force"] if force else []), "--workspace", workspace_id, "-"], stdin=text)
                return
            except BerthError as exc:
                if PASTE_DROPPED not in exc.message or attempt == PASTE_ATTEMPTS - 1:
                    raise
                time.sleep(0.2)

    def terminals(self) -> dict[str, list[str]]:
        """Each workspace id (lower case) and the ttys of its terminal surfaces."""
        document = self._socket_first("system.tree", {"all_windows": False})
        if document is None:
            result = self._run(["tree", "--json"])
            try:
                document = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise BerthError("CMUX_REJECTED", "tree was not JSON", {}) from exc
        found: dict[str, list[str]] = {}
        for window in document.get("windows") or []:
            for workspace in window.get("workspaces") or []:
                ttys = [surface.get("tty") for pane in workspace.get("panes") or []
                        for surface in pane.get("surfaces") or [] if surface.get("tty")]
                found[str(workspace.get("id") or "").lower()] = ttys
        return found

    def pin_workspace(self, workspace_id: str, pinned: bool) -> None:
        """cmux's own pin: a pinned workspace stays above the unpinned ones in the sidebar."""
        self._run(["workspace-action", "--workspace", workspace_id, "--action", "pin" if pinned else "unpin"])

    def send_text(self, workspace_id: str, text: str) -> None:
        """Type text into the terminal. A trailing newline is Enter."""
        self._run(["send", "--workspace", workspace_id, "--", text])

    def send_key(self, workspace_id: str, key: str) -> None:
        # Always name the workspace. The default is the caller's workspace.
        self._run(["send-key", "--workspace", workspace_id, "--", key])

    def ensure_workspace_group(self, name: str, workspace_id: str) -> None:
        """Put a workspace in the named group. Create the group if it is missing.

        ``create --from`` also inserts a generated anchor workspace. That anchor
        is one this call just created. Move the anchor onto the played workspace,
        then close only that generated workspace. Pre-existing workspaces stay.
        """
        for group in self.list_groups():
            if group.name == name:
                target = group.ref or name
                self._run(
                    ["workspace-group", "add", "--group", target, "--workspace", workspace_id]
                )
                return
        before = {item.id.lower() for item in self.list_workspaces()}
        self._run(["workspace-group", "create", "--name", name, "--from", workspace_id])
        if not self._workspace_in_group(name, workspace_id, wait_s=3.0):
            return
        for extra in self.list_workspaces():
            if extra.id.lower() in before or extra.id.lower() == workspace_id.lower():
                continue
            # Closing the generated anchor promotes the played workspace.
            self.close_workspace(extra.id)

    def _workspace_in_group(self, name: str, workspace_id: str, *, wait_s: float) -> bool:
        deadline = time.time() + wait_s
        while True:
            current = self.list_workspaces()
            played = next((item for item in current if item.id.lower() == workspace_id.lower()), None)
            keys = {workspace_id.lower()}
            if played is not None and played.ref:
                keys.add(played.ref.lower())
            for group in self.list_groups():
                if group.name != name:
                    continue
                if any(member.lower() in keys for member in group.members):
                    return True
            if time.time() >= deadline:
                return False
            time.sleep(0.2)

    def reorder_workspaces(self, workspace_ids: list[str]) -> None:
        """One atomic reorder. cmux keeps pinned workspaces ahead of the rest."""
        if workspace_ids:
            self._run(["reorder-workspaces", "--order", ",".join(workspace_ids)])

    def rename_workspace(self, workspace_id: str, title: str) -> None:
        self._run(["workspace", "rename", workspace_id, "--title", title])

    def create_workspace(
        self,
        *,
        title: str,
        cwd: str,
        command: str | None,
        focus: bool,
        window_id: str | None = None,
    ) -> Workspace:
        before = {item.id.lower() for item in self.list_workspaces()}
        args = [
            "workspace",
            "create",
            "--name",
            title,
            "--cwd",
            cwd,
            "--focus",
            "true" if focus else "false",
        ]
        target_window = window_id or self.window_id
        if target_window:
            args.extend(["--window", target_window])
        if command:
            args.extend(["--command", command])
        result = self._run(args)
        hinted = _uuid_in_text(result.stdout) or _uuid_in_text(result.stderr)
        found: Workspace | None = None
        for _ in range(15):
            current = self.list_workspaces()
            if hinted:
                for item in current:
                    if item.id.lower() == hinted.lower():
                        found = item
                        break
            if found is None:
                fresh = [item for item in current if item.id.lower() not in before]
                titled = [item for item in fresh if item.title == title]
                if len(titled) == 1:
                    found = titled[0]
                elif len(fresh) == 1:
                    found = fresh[0]
            if found is not None:
                return found
            time.sleep(0.1)
        raise BerthError(
            "CMUX_REJECTED",
            "workspace create did not return a workspace id",
            {"stdout": result.stdout[-500:], "title": title},
        )

    def memory(self) -> dict:
        result = self._run(["memory", "--all", "--json"])
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BerthError("CMUX_REJECTED", "memory output was not JSON", {}) from exc
        if not isinstance(document, dict):
            raise BerthError("CMUX_REJECTED", "memory output was not an object", {})
        return document

    def events_snapshot_seq(self) -> int | None:
        if not self.socket_up():
            raise BerthError("CMUX_DOWN", f"cmux socket is missing at {self.socket}", {})
        try:
            result = subprocess.run(
                [self.binary, "events", "--snapshot", "--no-heartbeat", "--timeout", "10"],
                capture_output=True,
                text=True,
                env=self._env(),
                check=False,
            )
        except OSError as exc:
            raise BerthError("CMUX_DOWN", str(exc), {}) from exc
        for line in result.stdout.splitlines():
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                continue
            resume = document.get("resume") if isinstance(document, dict) else None
            if isinstance(resume, dict) and isinstance(resume.get("latest_seq"), int):
                return int(resume["latest_seq"])
        return None


def child_rss_bytes(document: dict) -> int | None:
    diagnostic = document.get("memory_diagnostic")
    if isinstance(diagnostic, dict):
        children = diagnostic.get("children")
        if isinstance(children, dict) and isinstance(children.get("recursive_rss_bytes"), int):
            return int(children["recursive_rss_bytes"])
    context = document.get("resource_context")
    if isinstance(context, dict):
        descendants = context.get("descendants")
        if isinstance(descendants, dict) and isinstance(descendants.get("rss_bytes"), int):
            return int(descendants["rss_bytes"])
    return None


def _workspace(item: dict, window_id: str | None) -> Workspace | None:
    identifier = item.get("id")
    if not isinstance(identifier, str) or not identifier:
        return None
    title = item.get("custom_title") if item.get("has_custom_title") and item.get("custom_title") else item.get("title")
    if not isinstance(title, str) or not title:
        title = item.get("title") if isinstance(item.get("title"), str) else identifier
    cwd = item.get("current_directory") if isinstance(item.get("current_directory"), str) else "/"
    ref = item.get("ref") if isinstance(item.get("ref"), str) else None
    index = item.get("index") if isinstance(item.get("index"), int) else None
    return Workspace(
        id=identifier,
        title=title,
        cwd=cwd or "/",
        ref=ref,
        index=index,
        selected=bool(item.get("selected")),
        window_id=window_id,
        custom=bool(item.get("has_custom_title")),
        color=item.get("custom_color") if isinstance(item.get("custom_color"), str) else None,
        submitted_at=item.get("latest_submitted_at") if isinstance(item.get("latest_submitted_at"), str) else None,
        submitted_message=(item.get("latest_submitted_message")
                           if isinstance(item.get("latest_submitted_message"), str) else None),
        pinned=bool(item.get("pinned")),
    )


def _surface(item: dict) -> Surface:
    resume = item.get("resume_binding") if isinstance(item.get("resume_binding"), dict) else {}
    launch = resume.get("launch_command") if isinstance(resume.get("launch_command"), dict) else {}
    arguments = launch.get("arguments") if isinstance(launch.get("arguments"), list) else []
    initial = item.get("initial_command")
    command = resume.get("command")
    return Surface(
        ref=item.get("ref") if isinstance(item.get("ref"), str) else None,
        title=item.get("title") if isinstance(item.get("title"), str) else None,
        type=item.get("type") if isinstance(item.get("type"), str) else None,
        initial_command=initial if isinstance(initial, str) else None,
        cwd=(
            item.get("requested_working_directory")
            if isinstance(item.get("requested_working_directory"), str)
            else resume.get("cwd") if isinstance(resume.get("cwd"), str) else None
        ),
        resume_kind=resume.get("kind") if isinstance(resume.get("kind"), str) else None,
        resume_command=command if isinstance(command, str) else None,
        launch_arguments=[arg for arg in arguments if isinstance(arg, str)],
        launch_executable=(
            launch.get("executable_path") if isinstance(launch.get("executable_path"), str) else None
        ),
        launcher=launch.get("launcher") if isinstance(launch.get("launcher"), str) else None,
    )


def _uuid_in_text(text: str) -> str | None:
    match = UUID_RE.search(text or "")
    return match.group(0) if match else None
