"""Each session's orchestrator, for the reorientation hook after a compaction (2026-11-04).

Agent Behavior's eval found that after a compaction most agents never told
their orchestrator where they were (17 of 54 prompted, 15 of 56 automatic).
Settings Agent's ``SessionStart`` hook (matcher ``compact``) runs
``berth orchestrator me --hook``, which names the orchestrator in four lines
of context (Agent Behavior's text, ``HOOK_TEXT``).

A folder names its orchestrator (``folder.orchestrator``, a Berth id), set by
the root's orchestrator with the MCP's ``folder`` ``orchestrator`` action or
``berth orchestrate <folder> <session|none>``. A session's orchestrator is the
one named by the nearest folder up its tree, not counting the session itself.
A root orchestrator (the orchestrator of a top-level folder) has no orchestrator: it gets its peers, every other
top-level folder's orchestrator, and the hook tells it to send each its compaction report (2026-11-06, Agent
Behavior: since 11-05 there are three roots, and the hook had named one peer as "your orchestrator").

``none`` on a folder is an answer, not a blank (2026-11-04, STUDIO Orch): its
sessions report to Dominik, not to an orchestrator, so the walk stops there
and the hook prints nothing (STUDIO/BERTH). ``inherit`` clears a folder, so
its sessions take the orchestrator from further up.
"""

from __future__ import annotations

from berth.sqlite_schema import schema_step

import json
from pathlib import Path

from berth.errors import BerthError

NONE = "none"  # folder.orchestrator: no orchestrator here, and none from above either

# Agent Behavior's text (proposals/2026-11-04-reorientation-hook.md); changes to it go to Agent Behavior.
HOOK_TEXT = (
    "You were just compacted. Re-read your brief (the CLAUDE.md where you work) before you go on.\n"
    "Your orchestrator is {title} (Berth {id}). If you're an orchestrator, send it your compaction report.\n"
    "If you're a standing agent with a brief, send it one message: what you're in the middle of, and what\n"
    "you need. A one-job fork or worker sends nothing (ROOTS.md, \"Every agent reorients with its orchestrator "
    "after a compaction\")."
)


def ensure(conn) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(folder)")}
    if columns and "orchestrator" not in columns:
        schema_step(conn, "ALTER TABLE folder ADD COLUMN orchestrator TEXT")


def _folders(db) -> dict[str, dict]:
    ensure(db.conn)
    rows = db.conn.execute("SELECT id, parent_id, name, sort, default_cwd, orchestrator FROM folder").fetchall()
    folders = {row["id"]: dict(row) for row in rows}
    for folder in folders.values():
        names, seen, current = [], set(), folder
        while current is not None and current["id"] not in seen:
            seen.add(current["id"])
            names.append(current["name"])
            current = folders.get(current["parent_id"])
        folder["path"] = "/".join(reversed(names))
    return folders


def find_folder(db, text: str) -> dict:
    """A folder by its path, or by a last name only it has."""
    wanted = text.strip().strip("/")
    folders = _folders(db)
    exact = [item for item in folders.values() if item["path"].casefold() == wanted.casefold()]
    named = [item for item in folders.values() if item["name"].casefold() == wanted.casefold()]
    hits = exact or named
    if len(hits) != 1:
        raise BerthError("NOT_FOUND" if not hits else "AMBIGUOUS", f"no single folder is {wanted!r}",
                         {"matches": [item["path"] for item in hits][:10]})
    return hits[0]


def set_orchestrator(db, folder_text: str, berth_id: str | None, *, by: str = "") -> dict:
    """Name a folder's orchestrator; ``NONE`` stops the walk there; None (inherit) clears it."""
    folder = find_folder(db, folder_text)
    title = None
    if berth_id and berth_id != NONE:
        row = db.require_berth(berth_id)
        berth_id, title = row["id"], row["title"]
    with db.immediate():
        db.conn.execute("UPDATE folder SET orchestrator = ? WHERE id = ?", (berth_id or None, folder["id"]))
        db.insert_event(berth_id if berth_id not in (None, NONE) else None, "orchestrator",
                        {"folder": folder["path"], "by": by, "set": berth_id or "inherit"})
    if berth_id == NONE:
        shown = NONE
    else:
        shown = {"id": berth_id, "title": title} if berth_id else "inherit"
    return {"folder": folder["path"], "orchestrator": shown}


ROOT_TEXT = (
    "You were just compacted. You're a root orchestrator: re-read your instructions, then send your compaction "
    "report to every other root's orchestrator: {peers} (ROOTS.md, \"Peers keep each other whole across "
    "compaction\")."
)


def _brief(db, berth_id: str, *, peer: bool = False, folder: str = "") -> dict | None:
    row = db.get_berth(berth_id)
    if row is None or row["state"] == "deleted":
        return None
    out = {"id": row["id"], "title": row["title"], "folder": folder}
    if peer:
        out["peer"] = True
    return out


def _tops(folders: dict[str, dict]) -> list[dict]:
    return sorted((item for item in folders.values() if not item["parent_id"]), key=lambda item: (item["sort"], item["name"]))


def peers_of(db, berth_id: str) -> dict | None:
    """For a root orchestrator: ``{"root": True, "peers": [{folder, id, title}, ...]}``, every other top-level
    folder's orchestrator, in the folders' order. None for any other session."""
    tops = _tops(_folders(db))
    if not any(top.get("orchestrator") == berth_id for top in tops):
        return None
    peers, seen = [], {berth_id}
    for top in tops:
        named = top.get("orchestrator")
        if named in (None, NONE) or named in seen:
            continue
        seen.add(named)
        found = _brief(db, named, folder=top["path"])
        if found is not None:
            peers.append({"folder": found["folder"], "id": found["id"], "title": found["title"]})
    return {"root": True, "peers": peers}


def of_folder(db, folder_id: str | None, *, besides: str | None = None) -> dict | None:
    """The orchestrator named by this folder or the nearest folder above it, other than ``besides``."""
    if besides is not None:
        root = peers_of(db, besides)
        if root is not None:
            return root  # a root orchestrator: its peers, not an orchestrator
    folders = _folders(db)
    seen: set[str] = set()
    current = folders.get(folder_id) if folder_id else None
    while current is not None and current["id"] not in seen:
        seen.add(current["id"])
        named = current.get("orchestrator")
        if named == NONE:
            return None  # its sessions report to Dominik: no orchestrator, and no peer
        if named and named != besides:
            found = _brief(db, named, folder=current["path"])
            if found is not None:
                return found
        current = folders.get(current["parent_id"])
    return None


def of_session(db, berth_id: str) -> dict | None:
    row = db.require_berth(berth_id)
    return of_folder(db, row["folder_id"], besides=row["id"])


def _folder_for_cwd(db, cwd: str) -> str | None:
    """The deepest folder whose default directory holds ``cwd``: where Berth would file a new session there."""
    try:
        target = Path(cwd).expanduser().resolve()
    except OSError:
        return None
    best, depth = None, -1
    for folder in _folders(db).values():
        default = folder.get("default_cwd")
        if not default:
            continue
        try:
            root = Path(default).expanduser().resolve()
        except OSError:
            continue
        if (target == root or root in target.parents) and len(root.parts) > depth:
            best, depth = folder["id"], len(root.parts)
    return best


def for_hook(db, *, session_id: str | None, workspace: str | None, cwd: str | None) -> dict | None:
    """The orchestrator for the session the hook runs in: found by its session id, its workspace, or its directory.

    A session id the hook was given decides alone (2026-11-04, Settings Agent): one Berth does not know answers
    nothing, never the workspace or directory of the shell it runs in, which can be another session's.
    """
    row = None
    if session_id:
        row = db.conn.execute("SELECT * FROM berth WHERE agent_session_id = ? AND state != 'deleted' "
                              "ORDER BY updated_at DESC LIMIT 1", (session_id,)).fetchone()
        return of_folder(db, row["folder_id"], besides=row["id"]) if row is not None else None
    if workspace:
        row = db.conn.execute("SELECT * FROM berth WHERE lower(cmux_workspace_id) = lower(?) AND state = 'live'",
                              (workspace,)).fetchone()
    if row is not None:
        return of_folder(db, row["folder_id"], besides=row["id"])
    folder = _folder_for_cwd(db, cwd) if cwd else None
    return of_folder(db, folder) if folder else None


def hook_json(found: dict | None) -> str:
    """The SessionStart hook's output, or "" for a session with no orchestrator (or a root with no peers)."""
    if not found:
        return ""
    if found.get("root"):
        if not found["peers"]:
            return ""
        listed = ", ".join(f"{peer['title']} (Berth {peer['id'][:8]})" for peer in found["peers"])
        text = ROOT_TEXT.format(peers=listed)
    else:
        text = HOOK_TEXT.format(title=found["title"], id=found["id"][:8])
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}})
