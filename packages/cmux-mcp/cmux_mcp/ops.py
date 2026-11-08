"""Workspace, surface, screen, and input operations.

File placement reads a path on disk and sends those bytes to the prompt box.
The file body is never part of the tool arguments.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from cmux_mcp.client import CmuxClient, CmuxError, parse_workspace_ref

SHORT_TEXT_MAX = 200
FILE_MAX_BYTES = 512 * 1024
SEND_CHUNK_CHARS = 8000

PROTECTED_NOTE = "workspace_close requires the workspace ref or id"


def _title(workspace: dict[str, Any]) -> str:
    custom = workspace.get("custom_title")
    if isinstance(custom, str) and custom.strip():
        return custom
    title = workspace.get("title")
    if isinstance(title, str):
        return title
    return ""


PLACEMENTS = ("afterCurrent", "top", "end")
GROUP_ACTIONS = (
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
)


def _public_workspace(workspace: dict[str, Any], group_ref: str | None = None) -> dict[str, Any]:
    row = {
        "ref": workspace.get("ref"),
        "id": workspace.get("id"),
        "title": _title(workspace),
        "cwd": workspace.get("current_directory"),
        "selected": bool(workspace.get("selected")),
    }
    if group_ref:
        row["group_ref"] = group_ref
    return row


def _public_group(group: dict[str, Any]) -> dict[str, Any]:
    members = group.get("member_workspace_refs")
    if not isinstance(members, list):
        members = []
    return {
        "ref": group.get("ref"),
        "name": group.get("name"),
        "anchor_workspace_ref": group.get("anchor_workspace_ref"),
        "member_workspace_refs": [item for item in members if isinstance(item, str)],
        "member_count": group.get("member_count"),
        "is_collapsed": bool(group.get("is_collapsed")),
        "is_pinned": bool(group.get("is_pinned")),
    }


def _match_workspace(workspaces: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [item for item in workspaces if name == item.get("ref") or name == item.get("id")]
    if not matches:
        raise CmuxError(f"no workspace matches {name!r}")
    if len(matches) != 1:
        raise CmuxError(f"workspace name {name!r} is ambiguous")
    return matches[0]


class CmuxOps:
    def __init__(self, client: CmuxClient) -> None:
        self.client = client

    def status(self, workspace: str | None = None, surface: str | None = None) -> dict[str, Any]:
        up = self.client.ping()
        body: dict[str, Any] = {"cmux": "up" if up else "down", "socket": self.client.path}
        if not up or not workspace:
            return body
        resolved = self._workspace(workspace)
        surfaces = self._surfaces(resolved)
        if surface:
            chosen = self._match_surface(surfaces, surface)
            body["pane"] = self._pane_alive(resolved, chosen)
        else:
            body["panes"] = [self._pane_alive(resolved, item) for item in surfaces]
        return body

    def workspace_list(self) -> dict[str, Any]:
        rows, window_ref = self._workspace_rows()
        groups, group_window = self._group_rows()
        if window_ref is None:
            window_ref = group_window
        by_member: dict[str, str] = {}
        public_groups = []
        for group in groups:
            public = _public_group(group)
            public_groups.append(public)
            ref = public.get("ref")
            if not isinstance(ref, str):
                continue
            for member in public["member_workspace_refs"]:
                by_member.setdefault(member, ref)
        workspaces = []
        for row in rows:
            group_ref = by_member.get(row.get("ref"))
            workspaces.append(_public_workspace(row, group_ref if isinstance(group_ref, str) else None))
        return {
            "window_ref": window_ref,
            "groups": public_groups,
            "workspaces": workspaces,
        }

    def workspace_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cwd = arguments.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip():
            raise CmuxError("workspace_create requires cwd")
        directory = str(Path(cwd).expanduser())
        if not Path(directory).is_dir():
            raise CmuxError(f"cwd is not a directory: {directory}")
        command = _optional_str(arguments.get("command"), "command")
        if command is not None and not command.strip():
            raise CmuxError("command is empty")
        name = _optional_str(arguments.get("name"), "name")
        description = _optional_str(arguments.get("description"), "description")
        group = _optional_str(arguments.get("group"), "group")
        group_placement = _placement(arguments.get("group_placement"), "group_placement")
        group_reference = _optional_str(arguments.get("group_reference"), "group_reference")
        window = _optional_str(arguments.get("window"), "window")
        layout = _optional_str(arguments.get("layout"), "layout")
        env = _env_entries(arguments.get("env"))
        env_file = _string_list(arguments.get("env_file"), "env_file")
        focus = _optional_bool(arguments, "focus")
        if (group_placement or group_reference) and not group:
            raise CmuxError("group_placement and group_reference require group")
        args = ["workspace", "create", "--cwd", directory]
        if command:
            args.extend(["--command", command])
        if name:
            args.extend(["--name", name])
        if description:
            args.extend(["--description", description])
        if focus is not None:
            args.extend(["--focus", "true" if focus else "false"])
        if group:
            args.extend(["--group", group])
        if group_placement:
            args.extend(["--group-placement", group_placement])
        if group_reference:
            args.extend(["--group-reference", group_reference])
        if window:
            args.extend(["--window", window])
        if layout:
            args.extend(["--layout", layout])
        for entry in env or []:
            args.extend(["--env", entry])
        for path in env_file or []:
            args.extend(["--env-file", path])
        output = self.client.cli(args)
        ref = parse_workspace_ref(output)
        created = self._await_workspace(ref)
        terminal = self._await_terminal(created)
        listed = self.workspace_list()
        joined = next(
            (item for item in listed["workspaces"] if item.get("ref") == created.get("ref")),
            None,
        )
        result = {
            "workspace": created.get("ref"),
            "id": created.get("id"),
            "title": _title(created),
            "cwd": directory,
            "terminal": None if terminal is None else terminal.get("ref"),
            "surface": None if terminal is None else terminal.get("id"),
        }
        if focus is not None:
            result["focused"] = focus
        if joined and joined.get("group_ref"):
            result["group_ref"] = joined["group_ref"]
        return result

    def workspace_rename(self, workspace: str, title: str) -> dict[str, Any]:
        resolved = self._require_named(workspace)
        if not title or not title.strip():
            raise CmuxError("workspace_rename requires title")
        self.client.cli(["rename-workspace", "--workspace", str(resolved["id"]), title])
        return {"workspace": resolved.get("ref"), "id": resolved.get("id"), "title": title}

    def workspace_focus(self, workspace: str) -> dict[str, Any]:
        resolved = self._require_named(workspace)
        self.client.cli(["select-workspace", "--workspace", str(resolved["id"])])
        return {"workspace": resolved.get("ref"), "id": resolved.get("id"), "focused": True}

    def workspace_close(self, workspace: str) -> dict[str, Any]:
        resolved = self._require_named(workspace)
        identity = str(resolved["id"])
        ref = str(resolved["ref"])
        rows, _window = self._workspace_rows()
        again = _match_workspace(rows, identity)
        if again.get("id") != identity or again.get("ref") != ref:
            raise CmuxError(f"refusing to close {ref}; its identity changed")
        self.client.call("workspace.close", {"workspace_id": identity})
        deadline = time.time() + 3
        while True:
            remaining, _window = self._workspace_rows()
            if not any(item.get("id") == identity for item in remaining):
                return {"closed": ref, "id": identity}
            if time.time() >= deadline:
                raise CmuxError(f"close of {ref} did not remove workspace {identity}")
            time.sleep(0.2)

    def surface_list(self, workspace: str) -> dict[str, Any]:
        resolved = self._require_named(workspace)
        surfaces = self._surfaces(resolved)
        rows = [_public_surface(item) for item in surfaces]
        terminal = _selected_terminal(surfaces)
        return {
            "workspace": resolved.get("ref"),
            "terminal": None if terminal is None else terminal.get("ref"),
            "surfaces": rows,
        }

    def screen(self, workspace: str, surface: str | None = None, lines: int | None = None) -> dict[str, Any]:
        resolved = self._require_named(workspace)
        surfaces = self._surfaces(resolved)
        chosen = self._match_surface(surfaces, surface) if surface else _selected_terminal(surfaces)
        if chosen is None:
            raise CmuxError(f"no terminal surface in {resolved.get('ref')}")
        result = self.client.call(
            "surface.read_text",
            {"workspace_id": resolved["id"], "surface_id": chosen["id"]},
        )
        text = _screen_text(result)
        if lines is not None:
            if lines < 1:
                raise CmuxError("lines must be at least 1")
            text = "\n".join(text.splitlines()[-lines:])
        return {
            "workspace": resolved.get("ref"),
            "surface": chosen.get("ref"),
            "text": text,
        }

    def input_bytes(
        self,
        workspace: str,
        target: str,
        text: str | None = None,
        path: str | None = None,
        key: str | None = None,
        submit: bool = False,
        surface: str | None = None,
    ) -> dict[str, Any]:
        if target not in ("prompt", "terminal"):
            raise CmuxError("target must be prompt or terminal")
        supplied = [name for name, value in (("text", text), ("path", path), ("key", key)) if value is not None]
        if len(supplied) != 1:
            raise CmuxError("pass exactly one of text, path, or key")
        text = _optional_str(text, "text")
        path = _optional_str(path, "path")
        key = _optional_str(key, "key")
        if path is not None:
            if target != "prompt":
                raise CmuxError("path places a file in the prompt box; target must be prompt")
            if submit:
                raise CmuxError("file placement does not press Enter")
            payload, digest, byte_count = _read_prompt_file(path)
        elif key is not None:
            if submit:
                raise CmuxError("pass the key itself; submit is only for a short line")
            if not key.strip():
                raise CmuxError("key is empty")
            payload = None
            digest = None
            byte_count = None
        else:
            assert text is not None
            _check_short_text(text)
            payload = text
            digest = None
            byte_count = len(text.encode("utf-8"))

        resolved = self._require_named(workspace)
        surfaces = self._surfaces(resolved)
        chosen = self._match_surface(surfaces, surface) if surface else _selected_terminal(surfaces)
        if chosen is None:
            raise CmuxError(f"no terminal surface in {resolved.get('ref')}")

        if key is not None:
            self.client.send_key(str(resolved["id"]), str(chosen["id"]), key)
            return {
                "target": target,
                "workspace": resolved.get("ref"),
                "surface": chosen.get("ref"),
                "key": key,
                "submitted": False,
            }

        assert payload is not None
        if path is not None:
            self._paste(str(resolved["id"]), str(chosen["id"]), payload)
        else:
            self._send_text(str(resolved["id"]), str(chosen["id"]), payload)
        submitted = False
        if submit:
            self.client.send_key(str(resolved["id"]), str(chosen["id"]), "enter")
            submitted = True
        body: dict[str, Any] = {
            "target": target,
            "workspace": resolved.get("ref"),
            "surface": chosen.get("ref"),
            "submitted": submitted,
            "bytes": byte_count,
        }
        if path is not None:
            body["path"] = str(Path(path).expanduser())
            body["sha256"] = digest
        else:
            body["text_bytes"] = byte_count
        return body

    def _paste(self, workspace_id: str, surface_id: str, payload: str) -> None:
        """A prompt file goes in as one paste, not as keystrokes.

        Typed into a pty, a 4.5 KB prompt lost two stretches from its middle
        in a busy Claude Code pane: the terminal holds about 1 KB of unread
        input and macOS drops the rest (2026-10-27). Smaller typed writes
        keep the bytes but each newline submits. A paste keeps both.
        A cmux without ``paste`` falls back to typing.
        """
        try:
            self.client.paste(workspace_id, surface_id, payload)
        except CmuxError as exc:
            if "unknown" not in str(exc).lower():
                raise
            self._send_text(workspace_id, surface_id, payload)

    def _send_text(self, workspace_id: str, surface_id: str, payload: str) -> None:
        if len(payload) <= SEND_CHUNK_CHARS:
            self.client.send_text(workspace_id, surface_id, payload)
            return
        start = 0
        while start < len(payload):
            end = min(len(payload), start + SEND_CHUNK_CHARS)
            if end < len(payload):
                newline = payload.rfind("\n", start, end)
                if newline > start:
                    end = newline + 1
            self.client.send_text(workspace_id, surface_id, payload[start:end])
            start = end

    def group_action(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        if action not in GROUP_ACTIONS:
            raise CmuxError(
                "action must be list, create, add, remove, new_workspace, rename, collapse, expand, focus, ungroup, or delete"
            )
        if arguments.get("close_members") is True and action != "delete":
            raise CmuxError("close_members is only valid for delete")
        if arguments.get("remove_generated_anchor") is True and action != "ungroup":
            raise CmuxError("remove_generated_anchor is only valid for ungroup")
        if action == "list":
            groups, window_ref = self._group_rows()
            return {"window_ref": window_ref, "groups": [_public_group(group) for group in groups]}
        if action == "create":
            return self._group_create(arguments)
        group = _optional_str(arguments.get("group"), "group")
        workspace = _optional_str(arguments.get("workspace"), "workspace")
        name = _optional_str(arguments.get("name"), "name")
        placement = _placement(arguments.get("placement"), "placement")
        if action in ("add", "rename", "collapse", "expand", "focus", "ungroup", "delete", "new_workspace"):
            if not group:
                raise CmuxError(f"{action} requires group")
        if action == "add" and not workspace:
            raise CmuxError("add requires workspace")
        if action == "remove" and not workspace:
            raise CmuxError("remove requires workspace")
        if action == "rename" and not name:
            raise CmuxError("rename requires name")
        args = ["workspace-group", _group_cli_action(str(action))]
        if action == "new_workspace":
            args.append(str(group))
            if placement:
                args.extend(["--placement", placement])
        elif action == "add":
            args.extend(["--group", str(group), "--workspace", str(workspace)])
        elif action == "remove":
            args.extend(["--workspace", str(workspace)])
        elif action == "rename":
            args.extend([str(group), "--name", str(name)])
        elif action == "delete":
            args.append(str(group))
            if arguments.get("close_members") is True:
                args.append("--close-workspaces")
        elif action == "ungroup":
            args.append(str(group))
            if arguments.get("remove_generated_anchor") is True:
                args.append("--remove-generated-anchor")
        else:
            args.append(str(group))
        args.append("--json")
        payload = self.client.json_cli(args)
        return {"action": action, "result": _json_object(payload)}

    def _group_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = _optional_str(arguments.get("name"), "name")
        if not name:
            raise CmuxError("create requires name")
        cwd = _optional_str(arguments.get("cwd"), "cwd")
        sources = _string_list(arguments.get("from"), "from")
        idempotency_key = _optional_str(arguments.get("idempotency_key"), "idempotency_key")
        external_id = _optional_str(arguments.get("external_id"), "external_id")
        if idempotency_key and external_id:
            raise CmuxError("pass idempotency_key or external_id, not both")
        before, _window = self._group_rows()
        before_refs = {group.get("ref") for group in before}
        args = ["workspace-group", "create", "--json", "--name", name]
        if cwd:
            args.extend(["--cwd", str(Path(cwd).expanduser())])
        if sources:
            args.extend(["--from", ",".join(sources)])
        if idempotency_key:
            args.extend(["--idempotency-key", idempotency_key])
        if external_id:
            args.extend(["--external-id", external_id])
        self.client.json_cli(args)
        created = self._await_group(before_refs, name)
        return {"action": "create", "group": _public_group(created)}

    def _await_group(self, before_refs: set[Any], name: str) -> dict[str, Any]:
        deadline = time.time() + 5
        while True:
            groups, _window = self._group_rows()
            fresh = [
                group
                for group in groups
                if group.get("ref") not in before_refs and group.get("name") == name
            ]
            if len(fresh) == 1:
                return fresh[0]
            if len(fresh) > 1:
                raise CmuxError(f"more than one new group is named {name!r}")
            if time.time() >= deadline:
                raise CmuxError(f"group {name!r} did not appear")
            time.sleep(0.2)

    def _group_rows(self) -> tuple[list[dict[str, Any]], str | None]:
        payload = self.client.json_cli(["workspace-group", "list", "--json"])
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)], None
        if not isinstance(payload, dict):
            raise CmuxError("workspace-group list returned an unexpected payload")
        rows = payload.get("groups")
        if not isinstance(rows, list):
            raise CmuxError("workspace-group list returned no groups")
        window = payload.get("window_ref")
        return [row for row in rows if isinstance(row, dict)], window if isinstance(window, str) else None

    def _await_workspace(self, ref: str) -> dict[str, Any]:
        deadline = time.time() + 5
        last = CmuxError(f"no workspace matches {ref!r}")
        while True:
            try:
                rows, _window = self._workspace_rows()
                return _match_workspace(rows, ref)
            except CmuxError as exc:
                last = exc
                if time.time() >= deadline:
                    raise last
                time.sleep(0.2)

    def _await_terminal(self, workspace: dict[str, Any]) -> dict[str, Any] | None:
        deadline = time.time() + 5
        while True:
            terminal = _selected_terminal(self._surfaces(workspace))
            if terminal is not None or time.time() >= deadline:
                return terminal
            time.sleep(0.2)

    def _workspace_rows(self) -> tuple[list[dict[str, Any]], str | None]:
        result = self.client.call("workspace.list", {})
        rows = result.get("workspaces")
        if not isinstance(rows, list):
            raise CmuxError("workspace.list returned no workspaces")
        window = result.get("window_ref")
        return [row for row in rows if isinstance(row, dict)], window if isinstance(window, str) else None

    def _workspace(self, name: str) -> dict[str, Any]:
        rows, _window = self._workspace_rows()
        return _match_workspace(rows, name)

    def _require_named(self, workspace: str | None) -> dict[str, Any]:
        if workspace is None or not str(workspace).strip():
            raise CmuxError(PROTECTED_NOTE)
        name = str(workspace).strip()
        if name.isdigit():
            raise CmuxError("name the workspace ref or id, not a bare number")
        return self._workspace(name)

    def _surfaces(self, workspace: dict[str, Any]) -> list[dict[str, Any]]:
        result = self.client.call("surface.list", {"workspace_id": workspace["id"]})
        rows = result.get("surfaces")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _match_surface(self, surfaces: list[dict[str, Any]], name: str) -> dict[str, Any]:
        matches = [item for item in surfaces if name == item.get("ref") or name == item.get("id")]
        if len(matches) != 1:
            raise CmuxError(f"no surface matches {name!r} in this workspace")
        return matches[0]

    def _pane_alive(self, workspace: dict[str, Any], surface: dict[str, Any]) -> dict[str, Any]:
        alive = surface.get("socket_binding") == "bound"
        tty = None
        ready = None
        try:
            debug = self.client.call("debug.terminals", {})
        except CmuxError:
            debug = {}
        terminals = debug.get("terminals")
        if isinstance(terminals, list):
            for item in terminals:
                if not isinstance(item, dict):
                    continue
                if item.get("surface_id") != surface.get("id") and item.get("surface_ref") != surface.get("ref"):
                    continue
                ready = bool(item.get("runtime_surface_ready")) and not bool(item.get("teardown_requested"))
                tty = item.get("tty")
                alive = ready
                break
        return {
            "workspace": workspace.get("ref"),
            "surface": surface.get("ref"),
            "type": surface.get("type"),
            "terminal": surface.get("type") == "terminal",
            "alive": bool(alive),
            "runtime_ready": ready,
            "tty": tty,
        }


def _public_surface(surface: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": surface.get("ref"),
        "id": surface.get("id"),
        "type": surface.get("type"),
        "terminal": surface.get("type") == "terminal",
        "title": surface.get("title"),
        "pane_ref": surface.get("pane_ref"),
        "selected": bool(surface.get("selected_in_pane") or surface.get("focused")),
    }


def _selected_terminal(surfaces: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminals = [item for item in surfaces if item.get("type") == "terminal"]
    if not terminals:
        return None
    for item in terminals:
        if item.get("selected_in_pane") or item.get("focused"):
            return item
    return terminals[0]


def _screen_text(result: dict[str, Any]) -> str:
    text = result.get("text")
    if isinstance(text, str) and text:
        return text
    encoded = result.get("base64")
    if isinstance(encoded, str) and encoded:
        import base64

        return base64.b64decode(encoded).decode("utf-8", "replace")
    if isinstance(text, str):
        return text
    return ""


def _group_cli_action(action: str) -> str:
    if action == "new_workspace":
        return "new-workspace"
    return action


def _json_object(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {
            key: value
            for key, value in payload.items()
            if key not in ("latest_conversation_message", "latest_submitted_message", "text")
        }
    return {"value": payload}


def _placement(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if value not in PLACEMENTS:
        raise CmuxError(f"{name} must be afterCurrent, top, or end")
    return str(value)


def _optional_bool(arguments: dict[str, Any], name: str) -> bool | None:
    if name not in arguments or arguments[name] is None:
        return None
    value = arguments[name]
    if not isinstance(value, bool):
        raise CmuxError(f"{name} must be a boolean")
    return value


def _env_entries(value: Any) -> list[str] | None:
    if value is None:
        return None
    entries = _string_list(value, "env")
    if entries is None:
        return None
    for entry in entries:
        key, separator, _rest = entry.partition("=")
        if not separator or not key or any(char.isspace() for char in key) or "\n" in entry or "\x00" in entry:
            raise CmuxError(f"env entry must be KEY=VALUE: {entry!r}")
    return entries


def _string_list(value: Any, name: str) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise CmuxError(f"{name} must be a list of strings")
    return value


def _optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CmuxError(f"{name} must be a string")
    return value


def _check_short_text(text: str) -> None:
    if text == "":
        raise CmuxError("text is empty; use path to place a file")
    if "\n" in text or "\r" in text:
        raise CmuxError("text must be one short line; put a longer prompt in a file and pass path")
    if len(text.encode("utf-8")) > SHORT_TEXT_MAX:
        raise CmuxError(
            f"text is longer than {SHORT_TEXT_MAX} bytes; put it in a file and pass path"
        )


def _read_prompt_file(path: str) -> tuple[str, str, int]:
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        raise CmuxError("path must be absolute")
    if not expanded.is_file():
        raise CmuxError(f"path is not a file: {expanded}")
    data = expanded.read_bytes()
    if len(data) > FILE_MAX_BYTES:
        raise CmuxError(f"file is larger than {FILE_MAX_BYTES} bytes")
    if b"\x00" in data:
        raise CmuxError("file is not UTF-8 text")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CmuxError("file is not UTF-8 text") from exc
    digest = hashlib.sha256(data).hexdigest()
    return text, digest, len(data)
