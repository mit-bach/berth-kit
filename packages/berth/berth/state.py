"""Pause, play, and reconcile.

Pause writes the recipe and the excerpt, commits that row as ``pausing``,
and only then asks cmux to close the workspace. A crash in between is
repaired by reconcile: a missing workspace becomes parked, a workspace that
is still there becomes live, and the recipe from the flush stays.

Play moves ``parked`` to ``playing`` in one immediate transaction. A second
play that sees ``playing`` returns ``IN_PROGRESS`` and does not create a
workspace. A ``playing`` row older than two minutes with no workspace id is
stale. Reconcile, and play itself, return that row to ``parked`` before a
new create.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from berth import machine
from berth.db import Catalog, public_berth
from berth.errors import BerthError
from berth.model import HookRecord, Workspace
from berth.place import (
    blank_grok_allowed,
    command_text,
    grok_exec_text,
    grok_flags,
    grok_fork_argv,
    grok_resume_argv,
    grok_session_argv,
    other_agent_exec_text,
    other_agent_program,
    other_agent_resume_argv,
    resume_argv,
    session_on_disk,
)
from berth.timeutil import STALE_PLAY_SECONDS, age_seconds, parse_time, utc_now

PidAlive = Callable[[int | None], bool]
SessionsRoot = Path


def _argv(row) -> list[str]:
    return json.loads(row["recipe_argv"] or "[]")


def _attachments(row) -> dict:
    return json.loads(row["attachments"] or "{}")


def _same(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.lower() == right.lower()


def _present_map(workspaces: list[Workspace]) -> dict[str, Workspace]:
    return {workspace.id.lower(): workspace for workspace in workspaces}


def _caller_protected(row, caller_workspace_id: str | None) -> bool:
    return _same(row["cmux_workspace_id"], caller_workspace_id)


def pause_berth(
    db: Catalog,
    cmux,
    berth_id: str,
    *,
    force: bool = False,
    confirm: bool = False,
    hook: HookRecord | None = None,
    caller_workspace_id: str | None = None,
    pid_alive_fn: PidAlive,
    now: str | None = None,
    reason: str = "pause",
) -> dict:
    """Close a live row's workspace and park it. ``reason`` "sleep" puts it to sleep instead."""
    stamp = now or utc_now()
    row = db.require_berth(berth_id)
    # The ! mark is a label. It does not block pause. The pane this command
    # is running in stays up, so the desk does not close itself.
    if _caller_protected(row, caller_workspace_id):
        raise BerthError(
            "PROTECTED",
            f"{row['title']} is the workspace running this command",
            {"berth_id": berth_id},
        )
    if row["state"] == "parked":
        was = row["park_reason"] or ""
        if was != reason and reason in {"pause", "sleep", "idle"} and was != "background":
            # Already closed: idle, sleeping and paused switch without opening anything.
            machine.transition(db, berth_id, "relabel", reason=reason, log_kind=reason, detail={"was": row["park_reason"]})
            return public_berth(db.require_berth(berth_id))
        return public_berth(row)
    if row["state"] == "playing":
        raise BerthError(
            "IN_PROGRESS",
            f"{row['title']} is playing",
            {"berth_id": berth_id},
        )

    workspace_id = row["cmux_workspace_id"]
    present = _present_map(cmux.list_workspaces()) if workspace_id else {}
    if workspace_id and workspace_id.lower() not in present:
        park_workspace_gone(db, berth_id, workspace_id, stamp, via="pause")
        return public_berth(db.require_berth(berth_id))

    # A terminal that has not drawn yet must not block the close.
    excerpt = ""
    if workspace_id:
        try:
            excerpt = cmux.read_screen(workspace_id, lines=120)
        except BerthError:
            excerpt = "screen not ready"
    screen = excerpt
    excerpt = _clip_excerpt(excerpt)
    kind = row["kind"]
    if kind == "process" and not (confirm or force):
        raise BerthError(
            "KIND_UNSAFE",
            f"{row['title']} is a process. Pause stops it. Pass --confirm.",
            {"berth_id": berth_id, "kind": kind, "excerpt": excerpt},
        )
    # The pane, read just now (berth/activity.py): a busy session is closed only when
    # confirmed, and what is typed in its prompt box is kept and put back when it opens.
    activity = _activity_now(cmux, row, screen, db)
    if activity.busy and not (confirm or force):
        raise BerthError(
            "BUSY",
            f"{row['title']} is busy: {'; '.join(activity.reasons)}. Pass --confirm to close it anyway.",
            {"berth_id": berth_id, "why": activity.reasons, "excerpt": excerpt},
        )
    draft = activity.draft
    if draft is not None and not draft.exact and not (confirm or force):
        raise BerthError(
            "BUSY",
            f"{row['title']} has a draft in its prompt box that Berth cannot save exactly ({draft.why}). "
            "Send it or clear it first, or pass --confirm to close it anyway.",
            {"berth_id": berth_id, "why": [f"draft: {draft.why}"], "excerpt": excerpt},
        )
    attachments = _attachments(row)
    if draft is not None:
        attachments["draft"] = {**draft.as_dict(), "at": stamp}
    if activity.mode:
        # Off and on again comes back the same: Claude starts in its default mode otherwise.
        attachments["permission_mode"] = activity.mode

    recipe_argv = _argv(row)
    recipe_display = row["recipe_display"]
    agent_version = row["agent_version"]
    if hook is not None and hook.agent_name == "grok":
        recipe_argv = grok_resume_argv(hook)
        if not grok_flags(recipe_argv) and grok_flags(_argv(row)):
            # The hook captured fewer arguments than Berth launched with.
            recipe_argv = grok_session_argv(_argv(row), hook.session_id or row["agent_session_id"] or "")
        recipe_display = " ".join(recipe_argv)
        from berth.place import version_from_executable

        agent_version = version_from_executable(hook.executable) or agent_version
    elif hook is not None and kind == "other-agent":
        recipe_argv = other_agent_resume_argv(hook)
        recipe_display = " ".join(recipe_argv)

    # The recipe hits disk before workspace close. The commit is the flush.
    machine.transition(
        db,
        berth_id,
        "pause_flush",
        fields={
            "excerpt": excerpt,
            "excerpt_at": stamp,
            "recipe_argv": json.dumps(recipe_argv),
            "recipe_display": recipe_display,
            "agent_version": agent_version,
            "attachments": json.dumps(attachments, sort_keys=True),
        },
        log_kind="pause",
        detail={"phase": "flush", "workspace_id": workspace_id},
        now=stamp,
    )
    if draft is not None:
        with db.immediate():
            # A second copy, kept in the history whatever happens to the row.
            db.insert_event(berth_id, "draft_saved", draft.as_dict())

    try:
        if workspace_id:
            cmux.close_workspace(workspace_id)
    except BerthError as exc:
        machine.transition(
            db,
            berth_id,
            "pause_failed",
            log_kind="close_failed",
            detail={"workspace_id": workspace_id, "message": exc.message},
        )
        raise

    machine.transition(
        db,
        berth_id,
        "pause_closed",
        reason=reason,
        log_kind="pause",
        detail={"phase": "closed", "workspace_id": workspace_id},
    )
    return public_berth(db.require_berth(berth_id))


CLAUDE_MODES = frozenset({"acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"})


def set_reopen_mode(db: Catalog, berth_id: str, mode: str) -> dict:
    """Open this Claude session in ``mode`` the next time it opens, once; after that it keeps what it had.

    2026-10-31: sessions made while Berth's create forced bypass reopened in bypass at every cycle,
    because Berth reopens in the mode read at the close. Dominik: switch them to auto, once.
    """
    from berth.db import public_berth

    row = db.require_berth(berth_id)
    if (row["agent_name"] or "").casefold() != "claude":
        raise BerthError("KIND_UNSAFE", f"{row['title']}: a permission mode is Claude's", {"kind": row["kind"]})
    if mode not in CLAUDE_MODES:
        raise BerthError("BAD_ARGUMENT", f"the mode is one of {', '.join(sorted(CLAUDE_MODES))}", {"mode": mode})
    data = _attachments(row)
    data = data if isinstance(data, dict) else {}
    data["reopen_mode"] = mode
    with db.immediate():
        db.update_berth(berth_id, attachments=json.dumps(data, sort_keys=True))
        db.insert_event(berth_id, "reopen_mode", {"mode": mode, "was": data.get("permission_mode")})
    return {**public_berth(db.require_berth(berth_id)), "reopen_mode": mode}


def _used_reopen_mode(db: Catalog, berth_id: str) -> None:
    """It opened in the mode set for it: that is now its mode, and the one-time choice is spent."""
    row = db.get_berth(berth_id)
    if row is None:
        return
    data = _attachments(row)
    if not isinstance(data, dict):
        return
    mode = data.pop("reopen_mode", None)
    if mode is None:
        return
    data["permission_mode"] = mode
    with db.immediate():
        db.update_berth(berth_id, attachments=json.dumps(data, sort_keys=True))


def _with_mode(argv: list[str], row) -> list[str]:
    """The command a Claude session reopens with: in the permission mode it was in when it closed.

    The mode is read from Claude's status strip at the close (``attachments.permission_mode``).
    It replaces any permission flag in the stored recipe, and is not stored in it.
    """
    data = _attachments(row)
    # A mode set on purpose for the next open (set_reopen_mode) wins over the one read at the close.
    mode = data.get("reopen_mode") or data.get("permission_mode")
    if (row["agent_name"] or "").casefold() != "claude" or mode not in CLAUDE_MODES:
        return argv
    out: list[str] = []
    skip = False
    for item in argv:
        if skip:
            skip = False
            continue
        if item == "--permission-mode":
            skip = True
            continue
        if item == "--dangerously-skip-permissions" or item.startswith("--permission-mode="):
            continue
        out.append(item)
    return [*out, "--permission-mode", mode]


def _with_name(argv: list[str], row) -> list[str]:
    """A Claude session reopens under its Berth title (``-n``) unless a person already gave it that name.

    Claude names a session from its ``custom-title`` (``/rename`` or ``-n``); without one it makes a name
    from the working directory. An ``ai-title`` that happens to match is not a name: on 2026-10-30
    "Diagram MCP setup and capabilities" came back from a move as "tooling-1e" in ListAgents and
    SendMessage. ``claude -r <id> -n <title>`` writes the ``custom-title`` (tested that day).
    """
    if (row["agent_name"] or "").casefold() != "claude" or "-r" not in argv or not (row["title"] or "").strip():
        return argv
    from berth.identity import claude_title

    title = " ".join(row["title"].split())
    named, manual = claude_title(row["agent_session_id"] or "", row["cwd"] or "")
    if manual and named == title:
        return argv
    out: list[str] = []
    skip = False
    for item in argv:
        if skip:
            skip = False
            continue
        if item in ("-n", "--name"):
            skip = True
            continue
        if item.startswith("--name="):
            continue
        out.append(item)
    return [*out, "-n", title]


def _activity_now(cmux, row, screen: str, db=None):
    """Busy and the draft, from the screen just read (a shell: from its terminal's foreground)."""
    from berth.activity import assess, foreground, program_states

    names = None
    workspace_id = row["cmux_workspace_id"] or ""
    if row["kind"] in ("shell", "process") and workspace_id:
        try:
            names = [name for tty in cmux.terminals().get(workspace_id.lower(), []) for name in foreground(tty)]
        except (BerthError, AttributeError):
            names = []
    if screen == "screen not ready" and row["kind"] not in ("shell", "process"):
        screen = ""
    programs = program_states(db.path.parent) if db is not None else {}
    from berth.activity import running_subagents

    return assess(screen, row["kind"] or "", (row["agent_name"] or "").casefold(), foreground=names,
                  program=programs.get(workspace_id.lower()), subagents_fn=lambda: running_subagents(row))


def play_berth(
    db: Catalog,
    cmux,
    berth_id: str,
    *,
    focus: bool = True,
    run_command: bool = False,
    sessions_root: Path | None = None,
    now: str | None = None,
) -> dict:
    stamp = now or utc_now()
    row = db.require_berth(berth_id)
    workspaces = cmux.list_workspaces()
    present = _present_map(workspaces)
    row = _prepare_play_row(db, row, present, stamp)
    workspace_id = row["cmux_workspace_id"]
    if row["state"] == "live" and workspace_id and workspace_id.lower() in present:
        cmux.select_workspace(workspace_id)
        with db.immediate():
            db.update_berth(berth_id, played_at=stamp, updated_at=stamp)
            db.insert_event(berth_id, "play", {"phase": "focus", "workspace_id": workspace_id})
        return public_berth(db.require_berth(berth_id))
    if row["state"] == "playing":
        raise BerthError(
            "IN_PROGRESS",
            f"{row['title']} is already playing",
            {"berth_id": berth_id},
        )
    if row["state"] == "pausing":
        raise BerthError(
            "IN_PROGRESS",
            f"{row['title']} is pausing",
            {"berth_id": berth_id},
        )

    _claim_playing(db, berth_id, stamp)
    playing = db.require_berth(berth_id)
    try:
        argv, display = _play_argv(db, playing, run_command, sessions_root)
        if playing["kind"] == "grok":
            command = grok_exec_text(argv)
        elif playing["kind"] == "other-agent":
            command = other_agent_exec_text(_with_name(_with_mode(argv, playing), playing)) or command_text(argv, display)
            from berth import configdir

            if configdir.is_claude(playing):
                command = configdir.shell_prefix(configdir.of(playing)) + command  # its account's folder
            with db.immediate():
                db.update_berth(
                    berth_id,
                    recipe_argv=json.dumps(argv),
                    recipe_display=display,
                    updated_at=utc_now(),
                )
        else:
            command = command_text(argv, display)
        created = cmux.create_workspace(
            title=row["title"],
            cwd=db.require_berth(berth_id)["cwd"] or row["cwd"] or "/",
            command=command,
            focus=focus,
        )
    except BerthError:
        _return_to_parked(db, berth_id)
        raise

    played = utc_now()
    current = db.require_berth(berth_id)
    if current["state"] == "live" and _same(current["cmux_workspace_id"], created.id):
        # The watcher adopted the new workspace onto this claim first.
        live = current
    else:
        live = machine.transition(
            db,
            berth_id,
            "play_created",
            workspace_id=created.id,
            fields={"cmux_pid": None, "played_at": played},
            log_kind="play",
            detail={"phase": "created", "workspace_id": created.id, "focus": focus},
            now=played,
        )
    from berth.workdir import mark_opened

    mark_opened(db, berth_id)
    _used_reopen_mode(db, berth_id)
    try:
        from berth.color import paint_workspace

        paint_workspace(db, cmux, created.id, live["folder_id"])
    except Exception:
        pass
    if live["kind"] == "grok" and not getattr(cmux, "fast_screen", False):
        _settle_grok_screen(cmux, created.id, live["agent_session_id"])
    return public_berth(db.require_berth(berth_id))


def reconcile(
    db: Catalog,
    *,
    present: dict[str, Workspace],
    hooks: dict[str, HookRecord],
    now: str | None = None,
    adopt_missing: list[dict] | None = None,
) -> dict:
    """Apply the section 10 table. ``adopt_missing`` are already-built records.

    This function does not close a workspace. The caller decides whether to
    pass uncataloged workspaces. The watcher does not.
    """
    stamp = now or utc_now()
    moment = parse_time(stamp)
    corrections = []
    for row in db.all_berths():
        berth_id = row["id"]
        state = row["state"]
        workspace_id = row["cmux_workspace_id"]
        known = bool(workspace_id) and workspace_id.lower() in present
        if state == "live" and known:
            hook = hooks.get(workspace_id.lower()) if workspace_id else None
            if hook is not None and hook.pid is not None and hook.pid != row["cmux_pid"]:
                with db.immediate():
                    db.update_berth(berth_id, cmux_pid=hook.pid, updated_at=stamp)
                corrections.append({"berth_id": berth_id, "action": "refresh_pid"})
            continue
        if state == "live" and not known:
            park_workspace_gone(db, berth_id, workspace_id, stamp, via="reconcile")
            corrections.append({"berth_id": berth_id, "action": "parked"})
            continue
        if state == "parked" and known:
            machine.transition(
                db, berth_id, "relink", workspace_id=workspace_id,
                log_kind="reconcile", detail={"action": "reattach"}, now=stamp,
            )
            corrections.append({"berth_id": berth_id, "action": "live"})
            continue
        if state == "pausing" and not known:
            park_workspace_gone(db, berth_id, workspace_id, stamp, via="pausing")
            corrections.append({"berth_id": berth_id, "action": "parked"})
            continue
        if state == "pausing" and known:
            machine.transition(
                db, berth_id, "relink", log_kind="reconcile",
                detail={"action": "close_did_not_happen"}, now=stamp,
            )
            corrections.append({"berth_id": berth_id, "action": "live"})
            continue
        if state == "playing" and known:
            machine.transition(
                db, berth_id, "relink", fields={"played_at": stamp}, log_kind="reconcile",
                detail={"action": "play_landed"}, now=stamp,
            )
            corrections.append({"berth_id": berth_id, "action": "live"})
            continue
        if state == "playing" and not known:
            stale = age_seconds(row["updated_at"], moment) >= STALE_PLAY_SECONDS
            if stale:
                machine.transition(
                    db, berth_id, "play_stale", reason="stale", log_kind="play_stale", now=stamp,
                )
                corrections.append({"berth_id": berth_id, "action": "play_stale"})
            continue
    adopted = []
    for fields in adopt_missing or []:
        adopted.append(fields["id"])
        with db.immediate():
            existing = None
            workspace_id = fields.get("cmux_workspace_id")
            if workspace_id:
                existing = db.berth_by_workspace(workspace_id)
            if existing is not None:
                continue
            machine.insert(
                db,
                fields,
                event="adopt",
                log_kind="adopt",
                detail={
                    "workspace_id": fields.get("cmux_workspace_id"),
                    "via": "reconcile",
                    "kind": fields.get("kind"),
                },
            )
    return {"corrections": corrections, "adopted": adopted}


def _prepare_play_row(db: Catalog, row, present: dict[str, Workspace], stamp: str):
    berth_id = row["id"]
    state = row["state"]
    workspace_id = row["cmux_workspace_id"]
    known = bool(workspace_id) and workspace_id.lower() in present
    if state == "live" and not known:
        park_workspace_gone(db, berth_id, workspace_id, stamp, via="play")
        return db.require_berth(berth_id)
    if state == "playing":
        stale = age_seconds(row["updated_at"], parse_time(stamp)) >= STALE_PLAY_SECONDS
        if known:
            return machine.transition(
                db, berth_id, "relink", fields={"played_at": stamp},
                log_kind="reconcile", detail={"action": "play_landed"}, now=stamp,
            )
        if stale:
            return machine.transition(
                db, berth_id, "play_stale", reason="stale", log_kind="play_stale", now=stamp,
            )
    return row


def _claim_playing(db: Catalog, berth_id: str, stamp: str) -> None:
    with db.immediate():
        row = db.require_berth(berth_id)
        if row["state"] == "playing":
            raise BerthError(
                "IN_PROGRESS",
                f"{row['title']} is already playing",
                {"berth_id": berth_id},
            )
        if row["state"] != "parked":
            raise BerthError(
                "IN_PROGRESS",
                f"{row['title']} is {row['state']}",
                {"berth_id": berth_id, "state": row["state"]},
            )
        machine.transition(db, berth_id, "play_claim", workspace_id=None, log_kind="play_claim", now=stamp)


def _return_to_parked(db: Catalog, berth_id: str) -> None:
    with db.immediate():
        row = db.get_berth(berth_id)
        if row is None or row["state"] != "playing":
            return
        machine.transition(db, berth_id, "play_failed", reason="play_failed", log_kind="play_failed")


def _play_argv(db: Catalog, row, run_command: bool, sessions_root: Path | None) -> tuple[list[str], str]:
    kind = row["kind"]
    argv = _argv(row)
    display = row["recipe_display"] or ""
    attachments = _attachments(row)
    session_id = row["agent_session_id"]
    if kind == "process":
        if not run_command:
            raise BerthError(
                "KIND_UNSAFE",
                f"{row['title']} is a process. Play runs its command again. Pass --run-command.",
                {"berth_id": row["id"], "kind": kind},
            )
        if not argv:
            raise BerthError(
                "KIND_UNSAFE",
                f"{row['title']} has no command to run",
                {"berth_id": row["id"], "kind": kind},
            )
        return argv, display
    if kind == "shell":
        return argv, display or "shell"
    if kind == "berth":
        return ["berth"], "berth"
    if kind == "grok":
        if session_id:
            root = sessions_root or (Path.home() / ".grok" / "sessions")
            parent = attachments.get("fork_of")
            if not session_on_disk(root, session_id) and isinstance(parent, str) and session_on_disk(root, parent):
                # A fork that never wrote its session: fork the parent again, same id.
                argv = grok_fork_argv(argv, parent, session_id)
                return argv, " ".join(argv)
            if not session_on_disk(root, session_id):
                raise BerthError(
                    "SESSION_MISSING",
                    f"Grok session {session_id} is not on disk",
                    {"berth_id": row["id"], "session_id": session_id},
                )
            owner = db.live_session_owner(session_id, row["id"])
            if owner is not None:
                raise BerthError(
                    "SESSION_IN_USE",
                    f"session {session_id} is live on {owner['title']}",
                    {
                        "berth_id": row["id"],
                        "other_berth_id": owner["id"],
                        "session_id": session_id,
                    },
                )
            # The recipe's flags, then -r: a fork whose session exists now
            # resumes it and never forks again.
            argv = grok_session_argv(argv, session_id)
            return argv, " ".join(argv)
        if blank_grok_allowed(kind, session_id, argv, attachments):
            return argv or ["grok"], display or "grok"
        raise BerthError(
            "SESSION_MISSING",
            f"{row['title']} has no Grok session id",
            {"berth_id": row["id"]},
        )
    if kind == "other-agent" and (row["agent_name"] or "").casefold() == "claude":
        claude_argv = _claude_play_argv(db, row, attachments)
        if claude_argv is not None:
            return claude_argv, " ".join(claude_argv)
    if kind == "other-agent":
        if not session_id:
            raise BerthError(
                "SESSION_MISSING",
                f"{row['title']} has no session id",
                {"berth_id": row["id"]},
            )
        if session_id:
            owner = db.live_session_owner(session_id, row["id"])
            if owner is not None:
                raise BerthError(
                    "SESSION_IN_USE",
                    f"session {session_id} is live on {owner['title']}",
                    {
                        "berth_id": row["id"],
                        "other_berth_id": owner["id"],
                        "session_id": session_id,
                    },
                )
            program = other_agent_program(argv, agent_name=row["agent_name"])
            argv = resume_argv(program, row["agent_name"], session_id)
            return argv, " ".join(argv)
        return argv, display or " ".join(argv)
    raise BerthError("KIND_UNSAFE", f"unknown kind {kind}", {"berth_id": row["id"], "kind": kind})


def _claude_play_argv(db: Catalog, row, attachments: dict) -> list[str] | None:
    """Claude rows play from what Claude Code says, not from the stored id alone.

    * A conversation running as a background job is brought to the pane:
      the background copy is stopped (``claude stop``, which keeps the
      conversation) and the conversation resumes as a normal session. Berth
      keeps no background agents.
    * A fork row with no id yet (or whose fork never wrote a transcript)
      forks its parent again with ``--fork-session``.
    * An id with no transcript is refused with SESSION_MISSING, instead of
      opening a workspace whose ``claude -r`` exits at once.
    * The cwd is the transcript's own directory, which ``claude -r`` needs.
    Returns None to fall through to the plain resume.
    """
    from berth import claude

    session_id = row["agent_session_id"]
    binary = claude.claude_binary()
    job = claude.running_job(session_id)
    if job is not None and not claude.stop_job(job):
        raise BerthError(
            "BUSY",
            f"{row['title']}: the background copy did not stop; try again",
            {"berth_id": row["id"], "session_id": session_id},
        )
    info = claude.transcript(session_id, row["cwd"] or "")
    from berth.workdir import chosen, confirm_from_transcript

    if chosen(row) is not None:
        # A working directory set on purpose (berth/workdir.py) wins over the transcript's
        # last cwd, which stays the old one until Claude records a turn in the new directory.
        if info is not None:
            confirm_from_transcript(db, row, info.cwd)
    elif info is not None and info.cwd and info.cwd != row["cwd"]:
        with db.immediate():
            db.update_berth(row["id"], cwd=info.cwd)
    if info is not None and info.has_messages:
        return None
    parent = attachments.get("fork_of")
    if isinstance(parent, str) and parent:
        fresh = session_id
        if not fresh or info is not None:
            # Claude refuses an id that already has a transcript file.
            fresh = str(uuid.uuid4())
            with db.immediate():
                db.update_berth(row["id"], agent_session_id=fresh)
        return [binary, "-r", parent, "--fork-session", "--session-id", fresh, "-n", row["title"]]
    raise BerthError(
        "SESSION_MISSING",
        f"{row['title']}: Claude has no conversation {session_id or '(none)'} to resume",
        {"berth_id": row["id"], "session_id": session_id},
    )


# A welcome-screen Resume control is not the nav label. These are the
# confirmations that the session we asked for has not opened yet.
_RESUME_GATES = (
    "press resume",
    "press enter to resume",
    "resume this session",
    "continue this session",
)


def park_workspace_gone(
    db: Catalog,
    berth_id: str,
    workspace_id: str | None,
    stamp: str,
    *,
    via: str,
) -> None:
    """Park the row. The session id, the recipe, and ``last_workspace_id`` stay."""
    machine.transition(
        db,
        berth_id,
        "vanished",
        reason=via,
        log_kind="workspace_vanished",
        detail={"workspace_id": workspace_id, "via": via},
        now=stamp,
    )


def _settle_grok_screen(cmux, workspace_id: str, session_id: str | None) -> str:
    """Wait until the visible terminal shows the session, not the login scrollback.

    The key goes only to this workspace. A login banner is not a resume.
    """
    text = ""
    nudged = False
    for _ in range(40):
        try:
            text = cmux.read_screen(workspace_id, lines=40, scrollback=False)
        except BerthError:
            text = ""
        lowered = text.lower()
        if not nudged and any(gate in lowered for gate in _RESUME_GATES):
            cmux.send_key(workspace_id, "enter")
            nudged = True
            time.sleep(0.6)
            continue
        if _proves_resume(text, session_id):
            return text
        time.sleep(0.5)
    return text


def _proves_resume(text: str, session_id: str | None) -> bool:
    """True when the visible screen is Grok, not the shell echoing `exec grok -r`."""
    if not text.strip() or _login_banner(text):
        return False
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if session_id and stripped.startswith("exec ") and session_id in stripped:
            continue
        kept.append(line)
    body = "\n".join(kept)
    if session_id and session_id in body:
        return True
    lowered = body.lower()
    return "grok" in lowered and "last login:" not in lowered


def _login_banner(text: str) -> bool:
    lowered = text.lower()
    if "last login:" in lowered:
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    last = lines[-1]
    return last.endswith("%") or last.endswith("$")


def _clip_excerpt(text: str) -> str:
    lines = text.splitlines()
    clipped = "\n".join(lines[-120:])
    if len(clipped) > 100_000:
        clipped = clipped[-100_000:]
    return clipped
