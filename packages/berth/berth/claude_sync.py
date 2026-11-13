"""Keep Claude rows on the conversations Claude says they are on.

Runs every watcher tick, before the hook-based retarget (which skips Claude
when Claude Code writes its own session records). See ``berth.claude``.

* A live row follows its pane. The pane's conversation is its interactive
  Claude process's conversation when that has a transcript; otherwise the
  background job whose name the pane is showing (an attached view).
* When a pane moves to another conversation (``/resume``, a fork, ``/clear``):
  - a row whose own id was never a real conversation is corrected in place;
  - a real conversation it leaves is parked ``replaced``, and the new one is
    relinked if a parked row holds it, or gets a new row.
* A background job no row holds gets a parked row with reason
  ``background``. Play stops the background copy and resumes it in a pane.
* A new row for a Claude conversation is filed with its family (rows whose
  transcripts start with the same message), not in Inbox.
"""

from __future__ import annotations

import json
import uuid

from berth import claude, machine
from berth.db import Catalog
from berth.model import INBOX_NAME, Workspace
from berth.timeutil import utc_now


def _resume(session_id: str) -> list[str]:
    return [claude.claude_binary(), "-r", session_id]


def _attach(job_id: str) -> list[str]:
    return [claude.claude_binary(), "attach", job_id]


def family_folder(db: Catalog, lineage: str | None, *, exclude: str | None = None) -> str | None:
    """The folder of the most recently used row in the same family, if any is filed."""
    if not lineage:
        return None
    inbox = db.ensure_inbox()
    best = None
    for row in db.conn.execute(
        "SELECT id, folder_id, cwd, agent_session_id, last_used_at FROM berth WHERE lower(agent_name) = 'claude' AND agent_session_id IS NOT NULL"
    ):
        if row["id"] == exclude or row["folder_id"] == inbox:
            continue
        info = claude.transcript(row["agent_session_id"], row["cwd"] or "")
        if info is None or info.lineage != lineage:
            continue
        if best is None or (row["last_used_at"] or "") > (best["last_used_at"] or ""):
            best = row
    return best["folder_id"] if best else None


def _titles(top_table: str) -> dict[str, str]:
    """Surface ref -> the title its terminal shows."""
    found = {}
    for line in top_table.splitlines():
        parts = line.split("\t")
        if len(parts) >= 7 and parts[3] == "surface":
            found[parts[4]] = parts[6]
    return found


def pane_session(tree, titles, workspace: Workspace, running) -> claude.Process | None:
    pids = claude.pane_pids(tree, workspace.ref, workspace.id)
    here = claude.pane_conversation(pids, running, tree)
    if here is not None and claude.resumable(here.session_id, here.cwd):
        return here
    shown = {titles.get(ref, "") for kind, ref in _descend(tree, workspace.ref) if kind == "surface"}
    shown.add(workspace.title)
    for job in claude.background_jobs(running):
        if job.name and job.name in shown:
            return job
    return here


def _descend(tree, root):
    stack = [root] if root else []
    seen = set()
    while stack:
        key = stack.pop()
        for kind, ref in tree.get(key, []):
            if ref in seen:
                continue
            seen.add(ref)
            yield kind, ref
            stack.append(ref)


def follow(db: Catalog, cmux, workspaces: list[Workspace]) -> list[dict]:
    running = claude.processes()
    _gone_notes(db)
    if not running:
        return _background(db, running)
    reader = getattr(cmux, "top_processes", None)
    if reader is None:
        return []
    try:
        table = reader()
    except Exception:
        return []
    tree = claude.process_tree(table)
    titles = _titles(table)
    by_id = {item.id.lower(): item for item in workspaces}
    actions = []
    rows = db.conn.execute(
        """
        SELECT * FROM berth WHERE state = 'live'
          AND (lower(agent_name) = 'claude' OR kind = 'shell')
        """
    ).fetchall()
    for row in rows:
        workspace = by_id.get((row["cmux_workspace_id"] or "").lower())
        if workspace is None:
            continue
        conv = pane_session(tree, titles, workspace, running)
        if conv is None or conv.session_id == row["agent_session_id"]:
            continue
        if not claude.resumable(conv.session_id, conv.cwd) and row["agent_session_id"]:
            continue
        actions.append(_move_pane(db, row, workspace, conv))
    actions += _background(db, running)
    return [item for item in actions if item]


def _move_pane(db: Catalog, row, workspace: Workspace, conv: claude.Process) -> dict | None:
    info = claude.transcript(conv.session_id, conv.cwd)
    cwd = (info.cwd if info and info.cwd else conv.cwd) or row["cwd"]
    argv = _resume(conv.session_id)
    holder = db.conn.execute(
        "SELECT * FROM berth WHERE agent_session_id = ? AND id != ?", (conv.session_id, row["id"])
    ).fetchone()
    if holder is not None and holder["state"] != "parked":
        return None
    keep_old = claude.resumable(row["agent_session_id"], row["cwd"] or "") and row["kind"] != "shell"
    stamp = utc_now()
    with db.immediate():
        if holder is not None:
            if keep_old:
                machine.transition(db, row["id"], "vanished", reason="replaced", log_kind="workspace_vanished",
                                   detail={"via": "claude_session", "to_session": conv.session_id})
            else:
                machine.delete(db, row["id"], reason="merged", detail={"into": holder["id"], "via": "claude_session"})
            folder = holder["folder_id"]
            if folder == db.ensure_inbox():
                folder = family_folder(db, info.lineage if info else None, exclude=holder["id"]) or folder
            machine.transition(
                db, holder["id"], "relink", workspace_id=workspace.id,
                fields={"cwd": cwd, "recipe_argv": json.dumps(argv), "recipe_display": " ".join(argv),
                        "folder_id": folder, "played_at": stamp},
                log_kind="promote", detail={"via": "claude_session", "session_id": conv.session_id},
            )
            return {"berth_id": holder["id"], "action": "relinked", "session_id": conv.session_id}
        if keep_old:
            folder = row["folder_id"]
            machine.transition(db, row["id"], "vanished", reason="replaced", log_kind="workspace_vanished",
                               detail={"via": "claude_session", "to_session": conv.session_id})
            from berth.configdir import of_process

            fields = _row(db, title=row["title"], folder=folder, cwd=cwd, session_id=conv.session_id, argv=argv,
                          state="live", workspace_id=workspace.id, protect=row["protect"],
                          config_dir=of_process(conv.pid))
            machine.insert(db, fields, event="adopt", log_kind="adopt",
                           detail={"via": "claude_session", "workspace_id": workspace.id, "from": row["id"]})
            return {"berth_id": fields["id"], "action": "adopted", "session_id": conv.session_id}
        folder = row["folder_id"]
        if folder == db.ensure_inbox():
            folder = family_folder(db, info.lineage if info else None, exclude=row["id"]) or folder
        from berth.configdir import of_process

        attachments = json.loads(row["attachments"] or "{}") if row["attachments"] else {}
        found = of_process(conv.pid)
        if found and not attachments.get("config_dir"):
            attachments["config_dir"] = found  # the account folder it really runs on
        db.update_berth(
            row["id"], kind="other-agent", agent_name="claude", agent_session_id=conv.session_id,
            cwd=cwd, recipe_argv=json.dumps(argv), recipe_display=" ".join(argv), folder_id=folder, note="",
            attachments=json.dumps(attachments, sort_keys=True),
        )
        db.insert_event(row["id"], "promote", {"via": "claude_session", "session_id": conv.session_id,
                                               "was": row["agent_session_id"]})
    return {"berth_id": row["id"], "action": "corrected", "session_id": conv.session_id}


def _row(db: Catalog, *, title, folder, cwd, session_id, argv, state, workspace_id=None, protect=0,
         config_dir: str | None = None) -> dict:
    stamp = utc_now()
    return {
        "id": str(uuid.uuid4()), "folder_id": folder, "title": title, "kind": "other-agent", "state": state,
        "protect": protect, "cwd": cwd, "note": "", "recipe_argv": json.dumps(argv),
        "recipe_display": " ".join(argv), "agent_name": "claude", "agent_session_id": session_id,
        "agent_version": None, "cmux_workspace_id": workspace_id, "cmux_surface_id": None, "cmux_pid": None,
        "attachments": json.dumps({"config_dir": config_dir}) if config_dir else "{}", "excerpt": "", "excerpt_at": None,
        "created_at": stamp, "updated_at": stamp,
        "parked_at": None if state == "live" else stamp, "played_at": stamp if state == "live" else None,
    }


RUNNING_NOTE = "background job running · enter moves it to a pane"
STOPPED_NOTE = "background job stopped · enter resumes it"
GONE_NOTE = "conversation is gone from disk · it cannot reopen · d deletes the row"


def _gone_notes(db: Catalog) -> None:
    """A parked Claude row whose conversation is gone says so, before anyone presses Enter.

    The same rows ``berth check`` reports as ``dead_session``. A note someone
    wrote is left alone; this note goes again if the conversation comes back.
    """
    from berth.invariant import _dead_claude

    rows = db.conn.execute("SELECT * FROM berth WHERE state = 'parked'").fetchall()
    gone = {item.berth_id for item in _dead_claude(rows)}
    with db.immediate():
        for row in rows:
            note = row["note"] or ""
            if row["id"] in gone and note in {"", GONE_NOTE} and note != GONE_NOTE:
                db.update_berth(row["id"], note=GONE_NOTE)
            elif row["id"] not in gone and note == GONE_NOTE:
                db.update_berth(row["id"], note="")


def job_title(name: str) -> str:
    """Claude names an in-app fork ``<parent> ⑂ <first words of the prompt>``. Keep ``<parent> ⑂``."""
    text = " ".join((name or "").split())
    if "⑂" in text:
        text = text.split("⑂", 1)[0].strip() + " ⑂"
    return text or "Claude background job"


def _background(db: Catalog, running) -> list[dict]:
    """Background jobs no row holds get a parked row, filed with their family.

    Every ``bg`` row's note says whether its job is running, so a job you
    cannot see in a pane can still be found in Berth.
    """
    jobs = {job.session_id for job in claude.background_jobs(running)}
    with db.immediate():
        for row in db.conn.execute(
            "SELECT id, agent_session_id, note FROM berth WHERE state = 'parked' AND park_reason = 'background'"
        ).fetchall():
            note = RUNNING_NOTE if row["agent_session_id"] in jobs else STOPPED_NOTE
            if row["note"] != note:
                db.update_berth(row["id"], note=note)
    held = {row[0] for row in db.conn.execute("SELECT agent_session_id FROM berth WHERE agent_session_id IS NOT NULL")}
    actions = []
    for job in claude.background_jobs(running):
        if job.session_id in held or not claude.resumable(job.session_id, job.cwd):
            continue
        info = claude.transcript(job.session_id, job.cwd)
        folder = family_folder(db, info.lineage if info else None) or db.ensure_inbox()
        fields = _row(db, title=job_title(job.name), folder=folder, cwd=(info.cwd if info else None) or job.cwd,
                      session_id=job.session_id, argv=_resume(job.session_id), state="parked")
        fields["park_reason"] = "background"
        fields["note"] = RUNNING_NOTE
        machine.insert(db, fields, event="import", log_kind="adopt",
                       detail={"via": "claude_background", "job_id": job.job_id})
        held.add(job.session_id)
        actions.append({"berth_id": fields["id"], "action": "background", "session_id": job.session_id})
    return actions


__all__ = ["follow", "family_folder", "pane_session", "INBOX_NAME"]
