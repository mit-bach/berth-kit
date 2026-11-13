"""Adopt one workspace into the catalog. Adopt closes nothing.

A second adopt of the same cmux workspace UUID returns the existing row.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable

from berth import machine
from berth.db import Catalog, public_berth
from berth.errors import BerthError
from berth.hooks import hook_for
from berth.model import INBOX_NAME, Folder, Group, HookRecord, Surface, Workspace
from berth.place import (
    choose_folder_id,
    grok_resume_argv,
    group_for_workspace,
    normalize_cwd,
    other_agent_resume_argv,
    recipe_from_command,
    surface_command,
    surface_looks_like_agent,
    version_from_executable,
)
from berth.timeutil import utc_now

BERTH_SCREEN_TITLE = "Berth"


def folders_from_rows(rows) -> list[Folder]:
    return [
        Folder(
            id=row["id"],
            parent_id=row["parent_id"],
            name=row["name"],
            sort=row["sort"],
            profile_id=row["profile_id"],
            default_cwd=row["default_cwd"],
            note=row["note"] or "",
            archived=row["archived"],
        )
        for row in rows
    ]


def build_record(
    *,
    workspace: Workspace,
    hook: HookRecord | None,
    surfaces: list[Surface],
    groups: list[Group],
    folders: list[Folder],
    inbox_id: str,
    now: str | None = None,
) -> dict:
    stamp = now or utc_now()
    cwd = normalize_cwd(workspace.cwd or (hook.cwd if hook else None) or "/")
    title = workspace.title or "untitled"
    group_name = group_for_workspace(workspace, groups)
    folder_id = choose_folder_id(folders, cwd=cwd, group_name=group_name) or inbox_id
    protect = 1 if workspace.selected or title == BERTH_SCREEN_TITLE else 0
    kind = "shell"
    argv: list[str] = []
    display = "shell"
    agent_name = None
    session_id = None
    surface_id = None
    pid = None
    version = None
    note = ""
    if hook is not None and hook.agent_name == "grok" and hook.session_id:
        kind = "grok"
        argv = grok_resume_argv(hook)
        display = " ".join(argv)
        agent_name = "grok"
        session_id = hook.session_id
        surface_id = hook.surface_id
        pid = hook.pid
        version = version_from_executable(hook.executable)
        cwd = normalize_cwd(hook.cwd or cwd)
    elif hook is not None and hook.session_id:
        kind = "other-agent"
        argv = other_agent_resume_argv(hook)
        display = " ".join(argv)
        agent_name = hook.agent_name
        session_id = hook.session_id
        surface_id = hook.surface_id
        pid = hook.pid
        version = version_from_executable(hook.executable)
        cwd = normalize_cwd(hook.cwd or cwd)
    else:
        command = surface_command(surfaces)
        if surface_looks_like_agent(surfaces) and not command:
            kind, argv, display = "shell", [], "shell"
            note = "no session id"
        else:
            kind, argv, display = recipe_from_command(command)
            if surface_looks_like_agent(surfaces):
                kind, argv, display = "shell", [], "shell"
                note = "no session id"
        if title == BERTH_SCREEN_TITLE and kind in {"shell", "process"}:
            kind, argv, display = "berth", ["berth"], "berth"
        for surface in surfaces:
            if surface.cwd:
                cwd = normalize_cwd(surface.cwd)
                break
    return {
        "id": str(uuid.uuid4()),
        "folder_id": folder_id,
        "title": title,
        "kind": kind,
        "state": "live",
        "protect": protect,
        "pinned": 1 if workspace.pinned else 0,
        "cwd": cwd,
        "note": note,
        "recipe_argv": json.dumps(argv),
        "recipe_display": display,
        "agent_name": agent_name,
        "agent_session_id": session_id,
        "agent_version": version,
        "cmux_workspace_id": workspace.id,
        "cmux_surface_id": surface_id,
        "cmux_pid": pid,
        # The account folder its Claude really runs on (berth/configdir.py ``of_process``), so a reopen keeps it.
        "attachments": json.dumps({"config_dir": found_dir}) if agent_name == "claude" and (found_dir := _config_dir_of(pid))
        else "{}",
        "excerpt": "",
        "excerpt_at": None,
        "created_at": stamp,
        "updated_at": stamp,
        "parked_at": None,
        "played_at": stamp,
    }


def _config_dir_of(pid) -> str | None:
    from berth.configdir import of_process

    try:
        return of_process(pid)
    except Exception:  # noqa: BLE001 - an unreadable process is filed on the default, as before
        return None


def adopt_workspace(
    db: Catalog,
    workspace: Workspace,
    *,
    hook: HookRecord | None,
    surfaces: list[Surface],
    groups: list[Group],
    now: str | None = None,
    cmux=None,
) -> dict:
    """Insert a live berth, or return the row that already has this workspace UUID.

    One Grok session id is one berth. A parked row with that id becomes live
    and takes the new workspace. A second row is not inserted.
    """
    returned = _returned_workspace(db, workspace)
    if returned is not None:
        return returned
    claimed = _claim_session(db, workspace, hook, now)
    if claimed is not None:
        _paint_live(db, cmux, claimed)
        return claimed
    existing = db.berth_by_workspace(workspace.id)
    if existing is not None:
        return public_berth(existing)
    claim = _playing_claim(db, workspace.title)
    if claim is not None:
        with db.immediate():
            again = db.berth_by_workspace(workspace.id)
            if again is not None:
                return public_berth(again)
            row = db.get_berth(claim)
            if row is not None and row["state"] == "playing" and not row["cmux_workspace_id"]:
                machine.transition(
                    db,
                    claim,
                    "play_bind",
                    workspace_id=workspace.id,
                    log_kind="adopt",
                    detail={"workspace_id": workspace.id, "via": "play", "title": workspace.title},
                )
                record = public_berth(db.require_berth(claim))
                _paint_live(db, cmux, record)
                return record
    # The group folder has to exist before the rule can choose it.
    ensure_group_folders(db, groups)
    folders = folders_from_rows(db.all_folders())
    inbox_id = db.ensure_inbox()
    fields = build_record(
        workspace=workspace,
        hook=hook,
        surfaces=surfaces,
        groups=groups,
        folders=folders,
        inbox_id=inbox_id,
        now=now,
    )
    with db.immediate():
        again = db.berth_by_workspace(workspace.id)
        if again is not None:
            return public_berth(again)
        machine.insert(
            db,
            fields,
            event="adopt",
            log_kind="adopt",
            detail={
                "workspace_id": workspace.id,
                "kind": fields["kind"],
                "title": fields["title"],
                "folder_id": fields["folder_id"],
            },
        )
    record = public_berth(db.require_berth(fields["id"]))
    _paint_live(db, cmux, record)
    return record


def _returned_workspace(db: Catalog, workspace: Workspace) -> dict | None:
    """A UUID a parked row last held is that row, never a new one.

    A pane cmux restored after a quit is left for the observer, which closes
    it. Any other parked row with that UUID is relinked here.
    """
    from berth.observer import is_screen, last_link

    row = last_link(db, workspace.id)
    if row is None:
        return None
    if row["park_reason"] == "cmux_quit" and not is_screen(row):
        return public_berth(row)
    if row["park_reason"] == "cmux_quit":
        return None
    machine.transition(
        db,
        row["id"],
        "relink",
        workspace_id=workspace.id,
        log_kind="restore",
        detail={"action": "reappeared", "was": row["park_reason"], "via": "adopt"},
    )
    return public_berth(db.require_berth(row["id"]))


def adopt_known(
    db: Catalog,
    cmux,
    hooks: dict[str, HookRecord],
    *,
    only_ids: set[str] | None = None,
) -> list[dict]:
    """Adopt workspaces from a live cmux. Closes nothing.

    ``only_ids`` limits the pass to those workspace UUIDs. The full pass is
    what ``berth adopt`` does. The watcher passes one id.
    """
    workspaces = cmux.list_workspaces()
    groups = _safe_groups(cmux)
    ensure_group_folders(db, groups)
    adopted = []
    wanted = {item.lower() for item in only_ids} if only_ids is not None else None
    for workspace in workspaces:
        if wanted is not None and workspace.id.lower() not in wanted:
            continue
        surfaces = _safe_surfaces(cmux, workspace.id)
        record = adopt_workspace(
            db,
            workspace,
            hook=hook_for(hooks, workspace.id),
            surfaces=surfaces,
            groups=groups,
            cmux=cmux,
        )
        adopted.append(record)
    for berth_id in refile_inbox_members(db, workspaces, groups):
        _paint_berth(db, cmux, berth_id)
    return adopted


def adopt_appearing(
    db: Catalog,
    cmux,
    hooks_loader: Callable[[], dict[str, HookRecord]],
    workspace_id: str,
    *,
    wait_s: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] | None = None,
) -> dict | None:
    """Wait briefly so a hook file can land, then adopt that workspace only."""
    clock = monotonic or time.monotonic
    deadline = clock() + wait_s
    while True:
        hooks = hooks_loader()
        workspace = _find(cmux, workspace_id)
        if workspace is not None:
            surfaces = _safe_surfaces(cmux, workspace.id)
            hook = hook_for(hooks, workspace.id)
            ready = hook is not None or _decisive(surfaces)
            if ready or clock() >= deadline:
                groups = _safe_groups(cmux)
                return adopt_workspace(
                    db,
                    workspace,
                    hook=hook,
                    surfaces=surfaces,
                    groups=groups,
                    cmux=cmux,
                )
        if clock() >= deadline:
            return None
        sleep(0.2)


def _paint_live(db: Catalog, cmux, record: dict) -> None:
    """Best effort. A color failure does not undo the adopt."""
    if cmux is None or record.get("state") != "live":
        return
    try:
        from berth.color import paint_workspace

        paint_workspace(db, cmux, record.get("cmux_workspace_id"), record.get("folder_id"))
    except Exception:
        return


def _paint_berth(db: Catalog, cmux, berth_id: str) -> None:
    if cmux is None:
        return
    try:
        from berth.color import paint_berth

        paint_berth(db, cmux, berth_id)
    except Exception:
        return


def _decisive(surfaces: list[Surface]) -> bool:
    if surface_looks_like_agent(surfaces):
        return False
    return surface_command(surfaces) is not None


def _find(cmux, workspace_id: str) -> Workspace | None:
    for workspace in cmux.list_workspaces():
        if workspace.id.lower() == workspace_id.lower():
            return workspace
    return None


def merge_duplicate_sessions(db: Catalog, open_ids: set[str] | None = None) -> list[dict]:
    """Keep the oldest row for each Grok session id and delete the others.

    The keeper takes the workspace id of the live row. The session id stays.
    Nothing is closed. ``open_ids`` limits which workspace ids count as live.
    """
    found = db.conn.execute(
        """
        SELECT agent_session_id AS session_id FROM berth
        WHERE agent_session_id IS NOT NULL AND agent_session_id != ''
        GROUP BY agent_session_id COLLATE NOCASE
        HAVING count(*) > 1
        """
    ).fetchall()
    reports = []
    for item in found:
        report = _collapse_session(db, item["session_id"], None, None, open_ids)
        if report is not None:
            reports.append(report)
    return reports


def _claim_session(db: Catalog, workspace: Workspace, hook: HookRecord | None, now: str | None) -> dict | None:
    """One session id is one berth, for Grok and for Claude."""
    if hook is None or not hook.session_id:
        return None
    rows = _rows_for_session(db, hook.session_id)
    if not rows:
        return None
    report = _collapse_session(db, hook.session_id, workspace, hook, None, now)
    if report is None:
        return None
    return public_berth(db.require_berth(report["keeper_id"]))


def _collapse_session(
    db: Catalog,
    session_id: str,
    workspace: Workspace | None,
    hook: HookRecord | None,
    open_ids: set[str] | None,
    now: str | None = None,
) -> dict | None:
    rows = _rows_for_session(db, session_id)
    if workspace is None and len(rows) < 2:
        return None
    if not rows:
        return None
    keeper = min(rows, key=lambda row: (row["created_at"] or "", row["id"]))
    target = workspace.id if workspace is not None else _live_workspace_id(rows, open_ids)
    stamp = now or utc_now()
    with db.immediate():
        current = _rows_for_session(db, session_id)
        if not current:
            return None
        keeper = min(current, key=lambda row: (row["created_at"] or "", row["id"]))
        if target is None:
            target = _live_workspace_id(current, open_ids)
        already = (
            target
            and (keeper["cmux_workspace_id"] or "").lower() == target.lower()
            and keeper["state"] == "live"
        )
        if target and not already:
            fields = {"played_at": stamp}
            if hook is not None:
                if hook.surface_id:
                    fields["cmux_surface_id"] = hook.surface_id
                if hook.pid is not None:
                    fields["cmux_pid"] = hook.pid
                if hook.agent_name != "grok":
                    argv = other_agent_resume_argv(hook)
                    fields["kind"] = "other-agent"
                    fields["agent_name"] = hook.agent_name
                    fields["agent_session_id"] = hook.session_id
                    fields["recipe_argv"] = json.dumps(argv)
                    fields["recipe_display"] = " ".join(argv)
                    fields["note"] = ""
                    from berth.workdir import chosen

                    if hook.cwd and chosen(keeper) is None:
                        fields["cwd"] = normalize_cwd(hook.cwd)
            event = "retarget" if keeper["state"] == "live" else "relink"
            machine.transition(
                db,
                keeper["id"],
                event,
                workspace_id=target,
                fields=fields,
                log_kind="adopt",
                detail={"workspace_id": target, "via": "session", "session_id": session_id},
                now=stamp,
            )
        dropped = []
        for row in current:
            if row["id"] == keeper["id"]:
                continue
            _drop_berth(db, row["id"])
            dropped.append(row["id"])
    kept = db.require_berth(keeper["id"])
    return {
        "keeper_id": keeper["id"],
        "session_id": kept["agent_session_id"],
        "workspace_id": kept["cmux_workspace_id"],
        "dropped": dropped,
    }


def _rows_for_session(db: Catalog, session_id: str) -> list:
    return list(
        db.conn.execute(
            """
            SELECT * FROM berth
            WHERE agent_session_id = ? COLLATE NOCASE
            ORDER BY created_at, id
            """,
            (session_id,),
        ).fetchall()
    )


def _live_workspace_id(rows, open_ids: set[str] | None) -> str | None:
    candidates = []
    for row in rows:
        workspace_id = row["cmux_workspace_id"]
        if not workspace_id or row["state"] not in {"live", "playing"}:
            continue
        if open_ids is not None and workspace_id.lower() not in {item.lower() for item in open_ids}:
            continue
        candidates.append(row)
    if not candidates:
        return None
    winner = max(candidates, key=lambda row: (row["updated_at"] or "", row["id"]))
    return winner["cmux_workspace_id"]


def _drop_berth(db: Catalog, berth_id: str) -> None:
    machine.delete(db, berth_id, reason="merged")


def _playing_claim(db: Catalog, title: str) -> str | None:
    """One in-flight play with this title has not stored a workspace id yet."""
    matches = [
        row["id"]
        for row in db.all_berths()
        if row["state"] == "playing" and row["title"] == title and not row["cmux_workspace_id"]
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def ensure_group_folders(db: Catalog, groups: list[Group]) -> list[str]:
    """Create a root folder for each group name that does not already have one.

    The match is the exact name, the same match ``choose_folder_id`` uses.
    An existing root, including Inbox, is left alone. This does not close
    a workspace and does not move a berth.
    """
    created: list[str] = []
    seen: set[str] = set()
    for group in groups:
        name = group.name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if _root_named(db, name) is not None:
            continue
        folder_id = str(uuid.uuid4())
        with db.immediate():
            if _root_named(db, name) is not None:
                continue
            siblings = db.child_folders(None)
            sort = 1 + max((int(row["sort"]) for row in siblings), default=-1)
            db.conn.execute(
                """
                INSERT INTO folder (
                  id, parent_id, name, sort, profile_id, default_cwd, note, archived
                ) VALUES (?, NULL, ?, ?, NULL, NULL, '', 0)
                """,
                (folder_id, name, sort),
            )
        created.append(name)
    return created


def refile_inbox_members(db: Catalog, workspaces: list[Workspace], groups: list[Group]) -> list[str]:
    """Move live Inbox rows whose workspace is in a group.

    A parked row stays. A row that is already in some other folder stays.
    A live row with no group stays in Inbox. Nothing is closed.
    """
    inbox_id = db.ensure_inbox()
    by_id = {workspace.id.lower(): workspace for workspace in workspaces}
    moved: list[str] = []
    for row in list(db.all_berths()):
        if row["state"] != "live" or row["folder_id"] != inbox_id:
            continue
        workspace_id = row["cmux_workspace_id"]
        if not workspace_id:
            continue
        workspace = by_id.get(str(workspace_id).lower())
        if workspace is None:
            continue
        name = group_for_workspace(workspace, groups)
        if not name:
            continue
        folder_id = _root_named(db, name)
        if folder_id is None or folder_id == inbox_id:
            continue
        with db.immediate():
            current = db.get_berth(row["id"])
            if current is None or current["state"] != "live" or current["folder_id"] != inbox_id:
                continue
            db.update_berth(row["id"], folder_id=folder_id)
            db.insert_event(
                row["id"],
                "move",
                {"folder_id": folder_id, "path": name, "via": "group"},
            )
        moved.append(row["id"])
    return moved


def _root_named(db: Catalog, name: str) -> str | None:
    for row in db.child_folders(None):
        if row["name"] == name:
            return str(row["id"])
    return None


def _safe_groups(cmux) -> list[Group]:
    try:
        return cmux.list_groups()
    except BerthError:
        return []


def _safe_surfaces(cmux, workspace_id: str) -> list[Surface]:
    try:
        return cmux.surfaces(workspace_id)
    except BerthError:
        return []


def inbox_name() -> str:
    return INBOX_NAME
