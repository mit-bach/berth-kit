"""In-memory cmux. Tests never open the live socket."""

from __future__ import annotations

from berth.errors import BerthError
from berth.model import Group, Surface, Workspace


class FakeCmux:
    def __init__(self) -> None:
        self.workspaces: dict[str, Workspace] = {}
        self.closed: list[str] = []
        self.created: list[dict] = []
        self.selected: list[str] = []
        self.screens: dict[str, str] = {}
        self.panels: dict[str, list[Surface]] = {}
        self.groups: list[Group] = []
        self.fail_close = False
        self.fail_create = False
        self.fail_read = False
        self.fast_screen = True
        self.grouped: list[tuple[str, str]] = []
        self.keys: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        self.colors: list[tuple[str, str | None]] = []
        self.on_close = None
        self.window_id = "window-1"
        self._seq = 0

    # The cmux app process: (pid, start). None is "not running". Tests change it
    # to quit or restart cmux. ``running`` holds workspace ids with an agent.
    app = (100, "Mon Jan  1 00:00:00 2026")
    running: frozenset = frozenset()

    def app_process(self):
        return self.app

    def pane_has_agent(self, workspace) -> bool:
        return workspace.id in self.running

    def socket_up(self) -> bool:
        return True

    def list_workspaces(self) -> list[Workspace]:
        return list(self.workspaces.values())

    def list_groups(self) -> list[Group]:
        return list(self.groups)

    def surfaces(self, workspace_id: str) -> list[Surface]:
        return list(self.panels.get(workspace_id, []))

    def read_screen(self, workspace_id: str, *, lines: int = 120, scrollback: bool = True) -> str:
        if self.fail_read:
            raise BerthError(
                "CMUX_REJECTED",
                "Failed to read terminal text",
                {"workspace_id": workspace_id},
            )
        return self.screens.get(workspace_id, "")

    def close_workspace(self, workspace_id: str) -> None:
        if self.on_close is not None:
            self.on_close(workspace_id)
        if self.fail_close:
            raise BerthError("CMUX_REJECTED", "close refused", {"workspace_id": workspace_id})
        self.workspaces.pop(workspace_id, None)
        self.closed.append(workspace_id)

    def send_key(self, workspace_id: str, key: str) -> None:
        self.keys.append((workspace_id, key))

    def ensure_workspace_group(self, name: str, workspace_id: str) -> None:
        self.grouped.append((name, workspace_id))

    def select_workspace(self, workspace_id: str) -> None:
        if workspace_id not in self.workspaces:
            raise BerthError("CMUX_REJECTED", "missing workspace", {"workspace_id": workspace_id})
        self.selected.append(workspace_id)

    def rename_workspace(self, workspace_id: str, title: str) -> None:
        workspace = self.workspaces[workspace_id]
        self.workspaces[workspace_id] = Workspace(
            id=workspace.id,
            title=title,
            cwd=workspace.cwd,
            ref=workspace.ref,
            index=workspace.index,
            selected=workspace.selected,
            window_id=workspace.window_id,
            custom=True,
        )

    def set_workspace_color(self, workspace_id: str, hex_color: str | None) -> None:
        self.colors.append((workspace_id, hex_color))

    def set_status(self, workspace_id: str, key: str, value: str, *, icon=None, color=None, priority=None) -> None:
        self.statuses = getattr(self, "statuses", {})
        self.statuses[(workspace_id, key)] = {"value": value, "icon": icon, "color": color, "priority": priority}

    def notify(self, workspace_id: str, *, title: str, subtitle: str = "", body: str = "") -> None:
        self.notified = getattr(self, "notified", [])
        self.notified.append((workspace_id, title, subtitle, body))

    def clear_status(self, workspace_id: str, key: str) -> None:
        getattr(self, "statuses", {}).pop((workspace_id, key), None)

    def set_progress(self, workspace_id: str, value: float, label: str) -> None:
        self.progress = getattr(self, "progress", {})
        self.progress[workspace_id] = (value, label)

    def clear_progress(self, workspace_id: str) -> None:
        getattr(self, "progress", {}).pop(workspace_id, None)

    def paste_text(self, workspace_id: str, text: str, *, force: bool = False) -> None:
        self.pasted = getattr(self, "pasted", []) + [(workspace_id, text)]

    def terminals(self) -> dict[str, list[str]]:
        return {}

    def pin_workspace(self, workspace_id: str, pinned: bool) -> None:
        from dataclasses import replace

        self.pins = getattr(self, "pins", []) + [(workspace_id, pinned)]
        self.workspaces[workspace_id] = replace(self.workspaces[workspace_id], pinned=pinned)

    def reorder_workspaces(self, workspace_ids: list[str]) -> None:
        self.reorders = getattr(self, "reorders", []) + [list(workspace_ids)]
        for index, workspace_id in enumerate(workspace_ids):
            item = self.workspaces[workspace_id]
            self.workspaces[workspace_id] = Workspace(**{**item.__dict__, "index": index})

    def send_text(self, workspace_id: str, text: str) -> None:
        self.sent.append((workspace_id, text))

    def create_workspace(self, *, title: str, cwd: str, command: str | None, focus: bool, window_id=None) -> Workspace:
        if self.fail_create:
            raise BerthError("CMUX_REJECTED", "create refused", {})
        self._seq += 1
        workspace = Workspace(
            id=f"created-{self._seq}",
            title=title,
            cwd=cwd,
            ref=f"workspace:{self._seq}",
            index=self._seq,
            selected=focus,
            window_id=window_id or self.window_id,
        )
        self.workspaces[workspace.id] = workspace
        self.created.append({"title": title, "cwd": cwd, "command": command, "focus": focus, "id": workspace.id})
        return workspace
