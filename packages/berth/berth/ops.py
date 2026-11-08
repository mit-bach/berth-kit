"""Catalog commands that are not the pause/play state machine."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from berth.adopt import adopt_known
from berth.catalog import CatalogError, move_folder, remove_folder, rename_folder
from berth.db import Catalog, public_berth, public_event
from berth.errors import BerthError, QueryAmbiguous
from berth.hooks import load_hooks
from berth.model import INBOX_NAME as INBOX
from berth.place import pid_alive
from berth.state import pause_berth, play_berth, reconcile
from berth.timeutil import utc_now


def resolve_query(db: Catalog, query: str) -> dict:
    text = query.strip()
    if not text:
        raise BerthError("NOT_FOUND", "empty query", {})
    exact = db.get_berth(text)
    if exact is not None:
        return public_berth(exact)
    if len(text) >= 4 and all(ch in "0123456789abcdef-" for ch in text.lower()):
        # The first 4+ characters of an id, as the MCP takes them (2026-11-03).
        prefixed = [row for row in db.all_berths() if row["id"].lower().startswith(text.lower())]
        if len(prefixed) == 1:
            return public_berth(prefixed[0])
    titled = [public_berth(row) for row in db.all_berths() if row["title"] == text]
    if len(titled) == 1:
        return titled[0]
    if len(titled) > 1:
        raise QueryAmbiguous(_match_brief(titled))
    needle = text.lower()
    substring = []
    for row in db.all_berths():
        record = public_berth(row)
        path = folder_path(db, record["folder_id"])
        haystack = f"{record['title']}\n{path}".lower()
        if needle in haystack:
            record["folder_path"] = path
            substring.append(record)
    if len(substring) == 1:
        return substring[0]
    if len(substring) > 1:
        raise QueryAmbiguous(_match_brief(substring))
    raise BerthError("NOT_FOUND", f"no berth matches {query}", {"query": query})


def resolve_workspace_env(db: Catalog, workspace_id: str | None) -> dict:
    if not workspace_id:
        raise BerthError(
            "NOT_FOUND",
            "pass a berth id, or run pause inside the workspace",
            {},
        )
    row = db.berth_by_workspace(workspace_id)
    if row is None:
        raise BerthError(
            "NOT_FOUND",
            f"no berth for workspace {workspace_id}",
            {"workspace_id": workspace_id},
        )
    return public_berth(row)


def _match_brief(records: list[dict]) -> list[dict]:
    return [
        {
            "id": record["id"],
            "title": record["title"],
            "state": record["state"],
            "folder_path": record.get("folder_path") or "",
        }
        for record in records
    ]


def folder_path(db: Catalog, folder_id: str) -> str:
    parts = []
    seen = set()
    current = folder_id
    while current and current not in seen:
        seen.add(current)
        row = db.folder_by_id(current)
        if row is None:
            break
        parts.append(row["name"])
        current = row["parent_id"]
    return "/".join(reversed(parts))


def find_folder_path(db: Catalog, path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return None
    parent = None
    current_id = None
    for part in parts:
        match = None
        for row in db.child_folders(parent):
            if row["name"] == part:
                match = row
                break
        if match is None:
            return None
        current_id = match["id"]
        parent = current_id
    return current_id


def mkdir_path(db: Catalog, path: str, *, inbox_children: bool = False) -> dict:
    """Make ``a/b/c``, including every missing parent. An existing path is returned as is.

    Inbox is "not in a folder", so it holds no folders (the screen's rule).
    ``inbox_children`` is only for a merge from an older catalog that has them.
    """
    parts = [part.strip() for part in path.split("/") if part.strip()]
    if not parts:
        raise BerthError("NOT_FOUND", "folder path is empty", {})
    if parts[0] == INBOX and len(parts) > 1 and not inbox_children:
        raise BerthError(
            "BAD_ARGUMENT",
            "Inbox holds no folders; make it at the top level or inside another folder",
            {"path": path},
        )
    parent = None
    created = []
    for part in parts:
        existing = None
        for row in db.child_folders(parent):
            if row["name"] == part:
                existing = row
                break
        if existing is not None:
            parent = existing["id"]
            continue
        folder_id = str(uuid.uuid4())
        siblings = db.child_folders(parent)
        sort = 1 + max((row["sort"] for row in siblings), default=-1)
        with db.immediate():
            again = None
            for row in db.child_folders(parent):
                if row["name"] == part:
                    again = row
                    break
            if again is not None:
                parent = again["id"]
                continue
            db.conn.execute(
                """
                INSERT INTO folder (id, parent_id, name, sort, profile_id, default_cwd, note, archived)
                VALUES (?, ?, ?, ?, NULL, NULL, '', 0)
                """,
                (folder_id, parent, part, sort),
            )
        parent = folder_id
        created.append(part)
    return {"id": parent, "path": "/".join(parts), "created": created}


def move_berth(db: Catalog, berth_id: str, folder: str) -> dict:
    row = db.require_berth(berth_id)
    folder_id = find_folder_path(db, folder)
    if folder_id is None:
        raise BerthError("NOT_FOUND", f"no folder {folder}", {"folder": folder})
    with db.immediate():
        db.update_berth(berth_id, folder_id=folder_id)
        db.insert_event(berth_id, "move", {"folder_id": folder_id, "path": folder})
    record = public_berth(db.require_berth(row["id"]))
    record["folder_path"] = folder_path(db, folder_id)
    return record


def set_note(db: Catalog, berth_id: str, text: str) -> dict:
    db.require_berth(berth_id)
    with db.immediate():
        db.update_berth(berth_id, note=text)
    return public_berth(db.require_berth(berth_id))


def set_protect(db: Catalog, berth_id: str, protect: bool) -> dict:
    db.require_berth(berth_id)
    with db.immediate():
        db.update_berth(berth_id, protect=1 if protect else 0)
    return public_berth(db.require_berth(berth_id))




def delete_berth(db: Catalog, cmux, berth_id: str, *, caller_workspace_id: str | None, force: bool = False) -> dict:
    """Remove one catalog row. A live row's workspace is closed first.

    A ``kind=berth`` row is a Berth screen, and the caller workspace is the
    pane this command runs in: both are refused unless ``force``, which the
    operator gives after a warning (2026-10-30, his words: "I should never be
    in a case where it's impossible to delete something from Berth, even if
    it's a safety check"). Deleting your own pane trashes the row first and
    closes the workspace last, since closing it ends the process doing the
    delete. Grok session files under ``~/.grok/sessions`` are not touched.
    """
    row = db.require_berth(berth_id)
    if is_screen(row) and not force:
        raise BerthError(
            "PROTECTED",
            f"{row['title']} is a Berth screen; force deletes it anyway",
            {"berth_id": berth_id, "kind": "berth"},
        )
    workspace_id = row["cmux_workspace_id"]
    own = bool(caller_workspace_id and workspace_id and workspace_id.lower() == caller_workspace_id.lower())
    if own and not force:
        raise BerthError(
            "PROTECTED",
            "refusing to delete the caller workspace; force deletes it anyway",
            {"berth_id": berth_id, "workspace_id": workspace_id},
        )
    from berth.screens import claimed, release

    if claimed(row):
        release(db, berth_id)  # a screen's claim goes; the trash keeps what the row really is
    live = row["state"] != "parked" and workspace_id
    if live:
        _keep_draft(db, cmux, row)
        if not own:
            cmux.close_workspace(workspace_id)
    removed = {
        "id": row["id"],
        "title": row["title"],
        "kind": row["kind"],
        "state": "deleted",
        "agent_session_id": row["agent_session_id"],
    }
    from berth import machine

    machine.delete(db, berth_id, reason="user", **({"detail": {"forced": True}} if force else {}))
    if live and own:
        cmux.close_workspace(workspace_id)  # last: this ends the process that asked
    return removed


def _keep_draft(db: Catalog, cmux, row) -> None:
    """Save what is typed in the prompt box onto the row before it goes to the trash (berth/activity.py)."""
    from berth.activity import observe, program_states

    activity = observe(cmux, row, programs=program_states(db.path.parent))
    if activity.draft is None:
        return
    data = json.loads(row["attachments"] or "{}")
    data["draft"] = {**activity.draft.as_dict(), "at": utc_now()}
    with db.immediate():
        db.update_berth(row["id"], attachments=json.dumps(data, sort_keys=True))
        db.insert_event(row["id"], "draft_saved", activity.draft.as_dict())


def _sidebar_title(cmux, workspace_id: str) -> str:
    try:
        workspaces = cmux.list_workspaces()
    except Exception:
        return ""
    for item in workspaces:
        if item.id.lower() == workspace_id.lower():
            return " ".join((item.title or "").split())
    return ""


def _drop_sent_claim(db: Catalog, berth_id: str) -> None:
    """The typed ``/rename`` did not land. The next tick may type it."""
    row = db.get_berth(berth_id)
    if row is None:
        return
    try:
        data = json.loads(row["attachments"] or "{}")
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict) or "name_sent" not in data:
        return
    data.pop("name_sent", None)
    with db.immediate():
        db.update_berth(berth_id, attachments=json.dumps(data, sort_keys=True))


def rename_berth(db: Catalog, cmux, berth_id: str, title: str) -> dict:
    """One name for the catalog, the live workspace, and the agent session.

    A live Grok, Claude, Codex, Antigravity, or Cursor session is renamed by
    typing ``/rename``. A parked Grok session is written into ``summary.json``.
    A parked Claude, Cursor, Codex, or Antigravity session is written into that
    agent's title store when the session file already exists. A shell is not
    typed into.
    """
    from berth.identity import (
        accepts_session_rename,
        attachments_with_lock,
        with_rename_on_open,
        current_session_title,
        discover_agent_sessions,
        note_workspace_renamed,
        send_session_rename,
        write_agy_title,
        write_claude_title,
        write_codex_title,
        write_cursor_title,
        write_session_title,
    )

    discover_agent_sessions(db)
    row = db.require_berth(berth_id)
    safe = " ".join(title.split())
    if not safe:
        return public_berth(row)
    agent = accepts_session_rename(row["kind"], row["agent_name"])
    session_id = row["agent_session_id"] or ""
    cwd = row["cwd"] or ""
    workspace_id = row["cmux_workspace_id"] or ""
    live = row["state"] == "live" and bool(workspace_id)
    claim_sent = agent and safe != "--auto" and live
    fields: dict = {"title": safe}
    if agent and safe != "--auto":
        seen, _manual = current_session_title(row["kind"], row["agent_name"], session_id, cwd)
        hold = _sidebar_title(cmux, workspace_id) if live else ""
        if not live and session_id:
            agent_name = (row["agent_name"] or "").casefold()
            if row["kind"] == "grok":
                write_session_title(session_id, cwd, safe)
            elif agent_name == "claude":
                write_claude_title(session_id, cwd, safe)
            elif agent_name == "cursor":
                write_cursor_title(session_id, cwd, safe)
            elif agent_name == "codex":
                write_codex_title(session_id, cwd, safe)
            elif agent_name == "antigravity":
                write_agy_title(session_id, cwd, safe)
        fields["attachments"] = attachments_with_lock(
            row["attachments"],
            safe,
            seen=seen,
            hold=hold,
            sent=safe if claim_sent else None,
        )
        # A closed session is renamed in its files above, but an agent that
        # opens does not always take that name (Claude keeps the one it had).
        # So it is also typed once the session is open and its prompt is ready.
        fields["attachments"] = with_rename_on_open(fields["attachments"], None if live else safe)
        # Only a name a person gave counts: Claude's own ai-title is not the session's name, and a match
        # with it left "tooling-1e" unrenamed (2026-10-30). Claude names a session from its custom-title.
        claude_session = (row["agent_name"] or "").casefold() == "claude" and row["kind"] != "grok"
        already = (seen or "") == safe and (_manual or not claude_session)
    else:
        already = True
    with db.immediate():
        db.update_berth(berth_id, **fields)
        db.insert_event(berth_id, "rename", {"title": safe})
    if not live:
        return public_berth(db.require_berth(berth_id))
    try:
        cmux.rename_workspace(workspace_id, safe)
        note_workspace_renamed(workspace_id, safe)
        if claim_sent and not already:
            if not send_session_rename(cmux, workspace_id, session_id, safe, force=True, kind=row["kind"],
                                       agent=row["agent_name"]):
                _drop_sent_claim(db, berth_id)  # busy now: the watcher types it once the pane is idle
    except Exception:
        if claim_sent:
            _drop_sent_claim(db, berth_id)
        raise
    return public_berth(db.require_berth(berth_id))


def set_session(db: Catalog, berth_id: str, session_id: str) -> dict:
    """Paste a session id. The kind becomes grok. Play checks the files later."""
    row = db.require_berth(berth_id)
    argv = json.loads(row["recipe_argv"] or "[]")
    if "-r" not in argv and "--resume" not in argv:
        argv = ["grok", "-r", session_id]
    else:
        argv = ["grok" if not argv else argv[0], "-r", session_id]
    with db.immediate():
        db.update_berth(
            berth_id,
            kind="grok",
            agent_name="grok",
            agent_session_id=session_id,
            recipe_argv=json.dumps(argv),
            recipe_display=" ".join(argv),
        )
    return public_berth(db.require_berth(berth_id))


def new_berth(
    db: Catalog,
    cmux,
    *,
    folder: str,
    title: str,
    cwd: str | None,
    focus: bool,
    sessions_root: Path | None,
) -> dict:
    folder_id = find_folder_path(db, folder)
    if folder_id is None:
        raise BerthError("NOT_FOUND", f"no folder {folder}", {"folder": folder})
    folder_row = db.folder_by_id(folder_id)
    work_cwd = cwd or (folder_row["default_cwd"] if folder_row else None) or str(Path.home())
    stamp = utc_now()
    berth_id = str(uuid.uuid4())
    fields = {
        "id": berth_id,
        "folder_id": folder_id,
        "title": title,
        "kind": "grok",
        "state": "parked",
        "protect": 0,
        "cwd": work_cwd,
        "note": "",
        "recipe_argv": json.dumps(["grok"]),
        "recipe_display": "grok",
        "agent_name": "grok",
        "agent_session_id": None,
        "agent_version": None,
        "cmux_workspace_id": None,
        "cmux_surface_id": None,
        "cmux_pid": None,
        "attachments": json.dumps({"blank": True}),
        "excerpt": "",
        "excerpt_at": None,
        "created_at": stamp,
        "updated_at": stamp,
        "parked_at": stamp,
        "played_at": None,
    }
    from berth import machine

    fields["park_reason"] = "new"
    machine.insert(db, fields, event="import", log_kind="adopt", detail={"via": "new", "blank": True})
    return play_berth(db, cmux, berth_id, focus=focus, sessions_root=sessions_root)


def focus_berth(db: Catalog, cmux, berth_id: str) -> dict:
    row = db.require_berth(berth_id)
    if row["state"] != "live" or not row["cmux_workspace_id"]:
        raise BerthError(
            "NOT_LIVE",
            f"{row['title']} is {row['state']}",
            {"berth_id": berth_id, "state": row["state"]},
        )
    present = {item.id.lower() for item in cmux.list_workspaces()}
    if row["cmux_workspace_id"].lower() not in present:
        raise BerthError(
            "NOT_LIVE",
            f"{row['title']} has no workspace",
            {"berth_id": berth_id, "state": row["state"]},
        )
    cmux.select_workspace(row["cmux_workspace_id"])
    return public_berth(row)


def excerpt_of(db: Catalog, berth_id: str) -> dict:
    row = db.require_berth(berth_id)
    return {
        "id": row["id"],
        "title": row["title"],
        "excerpt": row["excerpt"],
        "excerpt_at": row["excerpt_at"],
    }


def log_of(db: Catalog, berth_id: str) -> dict:
    db.require_berth(berth_id)
    return {"id": berth_id, "events": [public_event(row) for row in db.events_for(berth_id)]}


def tree(db: Catalog, *, include_archived: bool = False) -> dict:
    def node(folder_row) -> dict:
        children = []
        for child in db.child_folders(folder_row["id"]):
            if child["archived"] and not include_archived:
                continue
            children.append(node(child))
        berths = []
        for row in db.berths_in(folder_row["id"]):
            record = public_berth(row)
            berths.append(
                {
                    "id": record["id"],
                    "title": record["title"],
                    "state": record["state"],
                    "kind": record["kind"],
                    "protect": record["protect"],
                    "note": record["note"],
                    "excerpt": record["excerpt"],
                    "cwd": record["cwd"],
                    "folder_path": folder_path(db, folder_row["id"]),
                }
            )
        return {
            "id": folder_row["id"],
            "name": folder_row["name"],
            "path": folder_path(db, folder_row["id"]),
            "archived": bool(folder_row["archived"]),
            "children": children,
            "berths": berths,
        }

    roots = []
    for row in db.child_folders(None):
        if row["archived"] and not include_archived:
            continue
        roots.append(node(row))
    return {"folders": roots}


def counts(db: Catalog) -> dict[str, int]:
    tallies = {"live": 0, "parked": 0, "playing": 0, "pausing": 0}
    for row in db.all_berths():
        if row["state"] in tallies:
            tallies[row["state"]] += 1
    return tallies


def rmdir_folder(db: Catalog, path: str) -> dict:
    """Delete one folder. Berths move up. Nothing is closed.

    A root folder's berths go to Inbox. Child folders move up one level.
    Inbox cannot be deleted.
    """
    folder_id = find_folder_path(db, path)
    label = path
    if folder_id is None:
        row = db.folder_by_id(path)
        if row is not None:
            folder_id = row["id"]
            label = folder_path(db, folder_id)
    if folder_id is None:
        raise BerthError("NOT_FOUND", f"no folder {path}", {"folder": path})
    try:
        return remove_folder(db.path, folder_id, datetime.now(timezone.utc))
    except CatalogError as exc:
        text = str(exc)
        if text == "Inbox stays":
            raise BerthError("FOLDER_NONEMPTY", "Inbox stays", {"folder": label}) from exc
        raise BerthError("NOT_FOUND", text, {"folder": label}) from exc


def require_folder(db: Catalog, path: str) -> tuple[str, str]:
    """A folder id and its path, from a path or an id."""
    text = (path or "").strip().strip("/")
    folder_id = find_folder_path(db, text) if text else None
    if folder_id is None and text:
        row = db.folder_by_id(text)
        if row is not None:
            folder_id = row["id"]
    if folder_id is None:
        raise BerthError("NOT_FOUND", f"no folder {path}", {"folder": path})
    return folder_id, folder_path(db, folder_id)


def _folder_error(exc: CatalogError, details: dict) -> BerthError:
    text = str(exc)
    if text == "Inbox stays":
        return BerthError("PROTECTED", "Inbox stays where it is", details)
    if text.startswith("folder exists"):
        return BerthError("FOLDER_EXISTS", text, details)
    if text == "no folder":
        return BerthError("NOT_FOUND", text, details)
    return BerthError("BAD_ARGUMENT", text, details)


def move_folder_path(db: Catalog, path: str, into: str | None) -> dict:
    """Put a folder, with its sessions and subfolders, inside ``into``.

    An empty ``into`` (or ``/``, or Inbox) is the top level. Nothing closes;
    live workspaces keep running and only change folder. Same rules as ``m``
    on a folder in the screen: Inbox does not move, a folder cannot go inside
    itself, and a name already used at the destination is refused.
    """
    folder_id, label = require_folder(db, path)
    target = (into or "").strip().strip("/")
    parent_id = None if not target else require_folder(db, target)[0]
    try:
        moved = move_folder(db.path, folder_id, parent_id, datetime.now(timezone.utc))
    except CatalogError as exc:
        raise _folder_error(exc, {"folder": label, "into": into or ""}) from exc
    return {"id": folder_id, "from": label, "path": moved}


def rename_folder_path(db: Catalog, path: str, name: str) -> dict:
    """Rename one folder in place. Its sessions and subfolders stay in it."""
    folder_id, label = require_folder(db, path)
    if label == INBOX:
        raise BerthError("PROTECTED", "Inbox keeps its name", {"folder": label})
    try:
        rename_folder(db.path, folder_id, name, datetime.now(timezone.utc))
    except CatalogError as exc:
        raise _folder_error(exc, {"folder": label, "name": name}) from exc
    return {"id": folder_id, "from": label, "path": folder_path(db, folder_id)}


def archive_folder(db: Catalog, path: str) -> dict:
    folder_id = find_folder_path(db, path)
    if folder_id is None:
        raise BerthError("NOT_FOUND", f"no folder {path}", {"folder": path})
    if _subtree_has_live(db, folder_id):
        raise BerthError(
            "FOLDER_LIVE",
            f"{path} still has a live berth",
            {"folder": path},
        )
    with db.immediate():
        _archive_subtree(db, folder_id)
    return {"archived": path}


def unarchive_folder(db: Catalog, path: str) -> dict:
    """Show an archived folder again, with everything under it and the folders above it."""
    folder_id = find_folder_path(db, path)
    if folder_id is None:
        raise BerthError("NOT_FOUND", f"no folder {path}", {"folder": path})
    with db.immediate():
        stack = [folder_id]
        while stack:
            current = stack.pop()
            db.conn.execute("UPDATE folder SET archived = 0 WHERE id = ?", (current,))
            stack.extend(child["id"] for child in db.child_folders(current))
        parent = db.conn.execute("SELECT parent_id FROM folder WHERE id = ?", (folder_id,)).fetchone()
        while parent is not None and parent["parent_id"]:
            db.conn.execute("UPDATE folder SET archived = 0 WHERE id = ?", (parent["parent_id"],))
            parent = db.conn.execute("SELECT parent_id FROM folder WHERE id = ?", (parent["parent_id"],)).fetchone()
    return {"unarchived": path}


def _subtree_has_live(db: Catalog, folder_id: str) -> bool:
    for row in db.berths_in(folder_id):
        if row["state"] in ("live", "playing", "pausing"):
            return True
    for child in db.child_folders(folder_id):
        if _subtree_has_live(db, child["id"]):
            return True
    return False


def _archive_subtree(db: Catalog, folder_id: str) -> None:
    db.conn.execute("UPDATE folder SET archived = 1 WHERE id = ?", (folder_id,))
    for child in db.child_folders(folder_id):
        _archive_subtree(db, child["id"])


def pause_folder(
    db: Catalog,
    cmux,
    path: str,
    *,
    force: bool,
    confirm: bool,
    caller_workspace_id: str | None,
    hooks: dict,
) -> dict:
    """Pause live berths in one folder. The ! mark does not skip a row.

    The caller workspace stays up. Without ``--confirm`` the list is returned
    and nothing is closed.
    """
    folder_id = find_folder_path(db, path)
    if folder_id is None:
        raise BerthError("NOT_FOUND", f"no folder {path}", {"folder": path})
    planned = []
    skipped = []
    for row in _berths_under(db, folder_id):
        record = public_berth(row)
        if row["state"] != "live":
            continue
        if is_screen(row):
            skipped.append({"id": row["id"], "title": row["title"], "reason": "berth screen"})
            continue
        if (
            caller_workspace_id
            and row["cmux_workspace_id"]
            and row["cmux_workspace_id"].lower() == caller_workspace_id.lower()
        ):
            skipped.append({"id": row["id"], "title": row["title"], "reason": "caller"})
            continue
        planned.append(record)
    if not confirm:
        raise BerthError(
            "KIND_UNSAFE",
            f"pause of {path} needs --confirm. Nothing was closed.",
            {"folder": path, "berths": [{"id": item["id"], "title": item["title"]} for item in planned], "skipped": skipped},
        )
    paused = []
    left = list(skipped)
    for record in planned:
        hook = None
        workspace_id = record.get("cmux_workspace_id")
        if workspace_id:
            hook = hooks.get(workspace_id.lower())
        try:
            result = pause_berth(
                db,
                cmux,
                record["id"],
                force=force,
                confirm=True,
                hook=hook,
                caller_workspace_id=caller_workspace_id,
                pid_alive_fn=pid_alive,
            )
            if result["state"] == "parked":
                paused.append({"id": result["id"], "title": result["title"], "state": result["state"]})
            else:
                left.append({"id": result["id"], "title": result["title"], "reason": result["state"]})
        except BerthError as exc:
            left.append({"id": record["id"], "title": record["title"], "reason": exc.code})
    return {"folder": path, "paused": paused, "left": left}


def is_screen(row) -> bool:
    from berth.observer import is_screen as screen

    return screen(row)


def _berths_under(db: Catalog, folder_id: str) -> list:
    rows = list(db.berths_in(folder_id))
    for child in db.child_folders(folder_id):
        rows.extend(_berths_under(db, child["id"]))
    return rows


def run_reconcile(db: Catalog, cmux, *, adopt_missing: bool) -> dict:
    from berth.model import HookRecord

    workspaces = cmux.list_workspaces()
    present = {item.id.lower(): item for item in workspaces}
    hooks = load_hooks()
    hook_map: dict[str, HookRecord] = hooks
    incoming = None
    if adopt_missing:
        known = set()
        for row in db.all_berths():
            if row["cmux_workspace_id"]:
                known.add(row["cmux_workspace_id"].lower())
        missing_ids = {item.id.lower() for item in workspaces if item.id.lower() not in known}
        if missing_ids:
            # Build records through adopt_known, which inserts them.
            # Reconcile's own insert path is used when the caller passes fields.
            # Here adopt_known is the one implementation of "the same way adopt works".
            created = adopt_known(db, cmux, hooks, only_ids=missing_ids)
            return {
                **reconcile(db, present=present, hooks=hook_map),
                "adopted": [item["id"] for item in created],
            }
    return reconcile(db, present=present, hooks=hook_map, adopt_missing=incoming)


def format_bytes(size: int | None) -> str | None:
    if size is None:
        return None
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.0f} MB"
    return f"{size} B"


def human_tree(document: dict) -> str:
    lines = []

    def walk(folder: dict, depth: int) -> None:
        pad = "  " * depth
        lines.append(f"{pad}{folder['name']}")
        for berth in folder["berths"]:
            mark = " protected" if berth["protect"] else ""
            excerpt = (berth["excerpt"] or "").strip().splitlines()
            hint = ""
            if excerpt:
                hint = "  " + excerpt[-1][:60]
            lines.append(
                f"{pad}  {berth['title']}  {berth['state']}  {berth['kind']}{mark}{hint}"
            )
        for child in folder["children"]:
            walk(child, depth + 1)

    for folder in document["folders"]:
        walk(folder, 0)
    return "\n".join(lines) if lines else "(empty)"


def human_status(document: dict) -> str:
    from berth.memory import mac_line, sessions_line

    found = document.get("memory") or {}
    rss_text = " · ".join(part for part in (mac_line(found.get("mac") or {}, found.get("at")),
                                            sessions_line(found.get("sessions") or {}))
                          if part) or "n/a (the watcher measures it every 30 s)"
    return "\n".join(
        [
            f"live {document['live']}",
            f"parked {document['parked']}",
            f"workspaces {document.get('workspaces') if document.get('workspaces') is not None else 'n/a'}",
            f"watcher {document['watcher']}",
            f"memory {rss_text}",
            f"shared MCP server {document.get('mcp_http') or 'n/a'}",
        ]
        + _health_lines(document.get("health") or {})
    )


def _health_lines(health: dict) -> list[str]:
    """Failing watcher jobs, and processes on old code with what to restart (berth/health.py)."""
    lines = []
    if health.get("watcher_ticking") is False:
        lines.append("watcher not ticking: no health written for 90 s")
    for name, item in sorted((health.get("failing_jobs") or {}).items()):
        lines.append(f"failing job {name}: {item.get('error')} ({item.get('failures')} ticks)")
    for item in health.get("old_code") or []:
        who = item.get("session") or item.get("kind")
        lines.append(f"old code: {who} (pid {item.get('pid')}): {item.get('fix')}")
    return lines


FORK_MARK = "⑂"


def fork_agent(kind: str | None, agent_name: str | None) -> str | None:
    """``claude`` or ``grok`` when Berth can fork the row, else None."""
    if kind == "grok":
        return "grok"
    if kind == "other-agent" and (agent_name or "").casefold() == "claude":
        return "claude"
    return None


def fork_berth(db: Catalog, cmux, berth_id: str, *, focus: bool = True, title: str | None = None,
               cwd: str | None = None, open_pane: bool = True) -> dict:
    """Fork a Claude or Grok session into a new workspace in the same Berth folder.

    ``cwd`` forks a Claude conversation into another working directory: the
    fork opens there and its transcript is written under that directory's
    project (tested 2026-10-29). Without it the fork works in the source's
    directory. A Grok fork always lands in its parent's directory, so ``cwd``
    is refused for Grok (``berth/workdir.py``).

    Berth picks the fork's session id first and hands it to the agent, so the
    new row knows its own conversation before the pane opens:

    * Claude: ``claude -r <id> --fork-session --session-id <new> -n <title>``.
    * Grok: ``grok -r <id> --fork-session -s <new>`` plus the flags the source
      plays with. Grok has no name flag; Berth types ``/rename`` once the
      fork is on screen.

    The agent copies the whole conversation under the new id, so the original
    keeps going untouched. The new row is filed next to the original, titled
    ``<title> ⑂``, and remembers ``fork_of``. A later play resumes the fork,
    or forks the parent again under the same id if the fork never wrote its
    conversation.
    """
    from berth import claude, machine
    from berth.identity import attachments_with_lock, grok_sessions_root, grok_title, send_session_rename
    from berth.place import grok_fork_argv, session_on_disk

    row = db.require_berth(berth_id)
    agent = fork_agent(row["kind"], row["agent_name"])
    if agent is None:
        raise BerthError(
            "KIND_UNSAFE",
            f"{row['title']} is a {row['kind']} row; only Claude and Grok sessions fork",
            {"berth_id": berth_id, "kind": row["kind"]},
        )
    session_id = row["agent_session_id"]
    name = " ".join((title or f"{row['title']} {FORK_MARK}").split())
    from berth.workdir import GROK_REFUSAL, check_directory, chosen

    if cwd is not None:
        if agent == "grok":
            raise BerthError("KIND_UNSAFE", f"{row['title']}: {GROK_REFUSAL}", {"berth_id": berth_id, "kind": "grok"})
        cwd = check_directory(cwd)
    fork_id = str(uuid.uuid4())
    if agent == "claude":
        info = claude.transcript(session_id, row["cwd"] or "")
        if info is None or not info.has_messages:
            raise BerthError(
                "SESSION_MISSING",
                f"{row['title']}: Claude has no conversation {session_id or '(none)'} to fork",
                {"berth_id": berth_id, "session_id": session_id},
            )
        # The directory asked for; else one set on purpose and not yet used; else where Claude last worked.
        cwd = cwd or chosen(row) or info.cwd or row["cwd"]
        argv = [claude.claude_binary(), "-r", session_id, "--fork-session", "--session-id", fork_id, "-n", name]
        kind, agent_name = "other-agent", "claude"
        attachments = {"fork_of": session_id}
        from berth.configdir import of as config_dir_of

        if config_dir_of(row):
            attachments["config_dir"] = config_dir_of(row)  # the same account as its source
    else:
        if not session_id or not session_on_disk(grok_sessions_root(), session_id):
            raise BerthError(
                "SESSION_MISSING",
                f"{row['title']}: Grok has no conversation {session_id or '(none)'} to fork",
                {"berth_id": berth_id, "session_id": session_id},
            )
        cwd = row["cwd"]
        argv = grok_fork_argv(json.loads(row["recipe_argv"] or "[]"), session_id, fork_id)
        kind, agent_name = "grok", "grok"
        seen, _manual = grok_title(session_id, cwd or "")
        # The name is typed below, after Grok is on screen; the watcher must not type it first.
        attachments = json.loads(attachments_with_lock("{}", name, seen=seen, sent=name))
        attachments["fork_of"] = session_id
    stamp = utc_now()
    new_id = str(uuid.uuid4())
    fields = {
        "id": new_id, "folder_id": row["folder_id"], "title": name, "kind": kind, "state": "parked",
        "protect": 0, "cwd": cwd, "note": "", "recipe_argv": json.dumps(argv),
        "recipe_display": " ".join(argv), "agent_name": agent_name, "agent_session_id": fork_id,
        "agent_version": row["agent_version"], "cmux_workspace_id": None, "cmux_surface_id": None, "cmux_pid": None,
        "attachments": json.dumps(attachments, sort_keys=True), "excerpt": "", "excerpt_at": None,
        "created_at": stamp, "updated_at": stamp, "parked_at": stamp, "played_at": None,
        "park_reason": "fork", "last_used_at": stamp,
    }
    machine.insert(
        db, fields, event="import", log_kind="fork",
        detail={"fork_of": session_id, "from": berth_id, "session_id": fork_id, "agent": agent},
    )
    if not open_pane:
        # berth/compact.py compacts the fork headlessly first; its play then resumes the fork's own conversation.
        from berth.db import public_berth

        return public_berth(db.require_berth(new_id))
    record = play_berth(db, cmux, new_id, focus=focus)
    workspace_id = record.get("cmux_workspace_id")
    if agent == "grok" and workspace_id and _grok_composer(cmux, workspace_id):
        try:
            send_session_rename(cmux, workspace_id, fork_id, name, force=True)
        except BerthError:
            pass
    return record


def _grok_composer(cmux, workspace_id: str) -> bool:
    """True once Grok's prompt box is on screen, so ``/rename`` cannot land in a dialog.

    A key typed into Grok's directory-trust dialog can answer it (``n`` is
    "No, quit"). Without the prompt box in about 15 seconds, nothing is typed
    and the fork keeps its parent's session title.
    """
    import time

    if getattr(cmux, "fast_screen", False):
        return True
    for _ in range(30):
        try:
            text = cmux.read_screen(workspace_id, lines=40, scrollback=False).lower()
        except BerthError:
            text = ""
        if "do you trust" not in text and ("shift+tab" in text or "shortcuts" in text):
            return True
        time.sleep(0.5)
    return False


def cycle_berth(
    db: Catalog,
    cmux,
    berth_id: str,
    *,
    pid_alive_fn,
    hook=None,
    caller_workspace_id: str | None = None,
    confirm: bool = False,
    focus: bool = False,
    sessions_root: Path | None = None,
) -> dict:
    """Turn one session off and on again: pause it, then play the same session.

    The pause saves the recipe and the last screen and closes the workspace.
    The play opens a new workspace in the same folder and resumes the same
    conversation (``claude -r``, ``grok -r`` with its flags), exactly as ``p``
    then Enter would. A process reruns its command, so it needs ``confirm``.
    A row that is already parked (a cycle whose play failed, tried again) is
    only turned on. The Berth screen and the caller's own workspace are refused.
    """
    row = db.require_berth(berth_id)
    if is_screen(row):
        raise BerthError("PROTECTED", f"{row['title']} is the Berth screen", {"berth_id": berth_id})
    if row["state"] in ("playing", "pausing"):
        raise BerthError("IN_PROGRESS", f"{row['title']} is {row['state']}", {"berth_id": berth_id})
    if row["state"] == "live":
        pause_berth(
            db,
            cmux,
            berth_id,
            force=confirm,
            confirm=confirm,
            hook=hook,
            caller_workspace_id=caller_workspace_id,
            pid_alive_fn=pid_alive_fn,
        )
        with db.immediate():
            db.insert_event(berth_id, "cycle", {"phase": "off", "workspace_id": row["cmux_workspace_id"]})
    record = play_berth(db, cmux, berth_id, focus=focus, run_command=confirm, sessions_root=sessions_root)
    with db.immediate():
        db.insert_event(berth_id, "cycle", {"phase": "on", "workspace_id": record["cmux_workspace_id"]})
    return record


def sleep_berth(
    db: Catalog,
    cmux,
    berth_id: str,
    *,
    pid_alive_fn,
    hook=None,
    caller_workspace_id: str | None = None,
    confirm: bool = False,
) -> dict:
    """Put a session to sleep: closed like a pause, but woken by ``wake_all`` (W).

    A live row is closed with its recipe and screen saved. A paused row just
    becomes a sleeping one. The Berth screen and the caller's workspace stay.
    """
    row = db.require_berth(berth_id)
    if is_screen(row):
        raise BerthError("PROTECTED", f"{row['title']} is the Berth screen", {"berth_id": berth_id})
    return pause_berth(
        db, cmux, berth_id, force=confirm, confirm=confirm, hook=hook,
        caller_workspace_id=caller_workspace_id, pid_alive_fn=pid_alive_fn, reason="sleep",
    )


def idle_berth(
    db: Catalog,
    cmux,
    berth_id: str,
    *,
    pid_alive_fn,
    hook=None,
    caller_workspace_id: str | None = None,
    confirm: bool = False,
) -> dict:
    """Make a session idle (2026-11-05): closed to free its memory, coming back soon, shown with the live ones.

    A live row is closed with its recipe and screen saved, as for sleep. A closed row just becomes idle.
    """
    row = db.require_berth(berth_id)
    if is_screen(row):
        raise BerthError("PROTECTED", f"{row['title']} is the Berth screen", {"berth_id": berth_id})
    return pause_berth(
        db, cmux, berth_id, force=confirm, confirm=confirm, hook=hook,
        caller_workspace_id=caller_workspace_id, pid_alive_fn=pid_alive_fn, reason="idle",
    )


def sleeping_ids(db: Catalog) -> list[str]:
    """Every sleeping row, most recently used first. Process rows are left out: they rerun only by hand."""
    from berth.model import SLEEP_REASONS

    marks = ",".join("?" for _ in SLEEP_REASONS)
    rows = db.conn.execute(
        f"SELECT * FROM berth WHERE state = 'parked' AND park_reason IN ({marks}) AND kind != 'process' "
        "ORDER BY COALESCE(last_used_at, parked_at, created_at) DESC",
        sorted(SLEEP_REASONS),
    ).fetchall()
    return [row["id"] for row in rows if not is_screen(row)]


def wake_all(db: Catalog, cmux, *, ids: list[str] | None = None) -> list[dict]:
    """Open every sleeping session again (W), one at a time, without taking focus."""
    from berth.model import is_sleeping

    results = []
    for berth_id in ids if ids is not None else sleeping_ids(db):
        row = db.get_berth(berth_id)
        if row is None or not is_sleeping(row["state"], row["park_reason"]):
            continue
        try:
            play_berth(db, cmux, berth_id, focus=False)
        except BerthError as exc:
            results.append({"id": berth_id, "title": row["title"], "result": "failed", "code": exc.code,
                            "message": exc.message})
            continue
        results.append({"id": berth_id, "title": row["title"], "result": "woken"})
    return results
