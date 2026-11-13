"""The Berth MCP: Berth's catalog and every Berth action, as eleven tools (``berth mcp``).

Built 2026-10-28 so agents use Berth itself instead of the Berth half of
cmux-mcp. The idea is one scope model (``berth/scope.py``): an agent sees
its own folder by default and widens with ``down``, ``up``, and ``branches``.
Every action takes the same session references (a Berth id or its first
characters, a title, an agent session id, or "me") and the bulk ones take
the same scope, with a dry run first.

    status    who you are, counts, cmux, the watcher, drift (check: a fresh look)
    folders   the folder tree with counts
    sessions  the sessions in a scope, grouped by folder
    session   one session in full, its saved screen, its history
    control   wake, sleep, park, cycle, focus, cancel (a queued close): named sessions or a whole scope
    create    a new Claude, Grok, or shell session, or a fork of one
    update    rename, move to a folder, note, pin, urgent, follow_up: named sessions or a scope
    folder    create, rename, move, delete, archive, unarchive a folder
    delete    sessions to the trash (30 days)
    trash     list, restore, forget

The server speaks MCP over stdio (JSON-RPC, one message a line) with no
dependencies. Nothing but responses goes to stdout.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from berth.errors import BerthError
from berth.scope import ALL, EXTRA_FIELDS, STATES, View, agent_of, is_screen_berth, query, select, state_of

SERVER_NAME = "berth"
VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
ACTIONS = ("wake", "idle", "sleep", "park", "cycle", "focus", "cancel")
EXCERPT_LINES = 40
EXCERPT_BYTES = 4000

INSTRUCTIONS = """Berth is the catalog of every agent and terminal session on this Mac (Claude, Grok, shells, processes), filed in nested folders like HOMESTEAD/Learning. A session is live (open in a cmux workspace), idle (closed to free memory, in use again soon), sleeping (closed for now), or parked (paused and put away); wake opens any closed one.

Start with sessions: with no arguments it shows the sessions in your own folder. Widen with down (subfolders), up (ancestors' own sessions), and branches (the ancestors' other subfolders too). Each folder group says what it left out (deeper, beside), so you know where to widen. query finds a session by name anywhere.

Name a session by its Berth id (or its first 4+ characters), its title, its agent session id, or "me". Actions on a whole scope return a dry run first; pass confirm true to act. Your own session and the Berth screen are never closed by a scope.

create starts a new session (or forks one) filed in your folder unless you name another. send gives a live session a prompt, after looking at what it is doing: when it is busy you see why and choose to wait (idle), queue it (now), or interrupt. A pinned session (update pin) stays in its place in Berth and comes first in cmux's sidebar.

A live session's status is what cmux's sidebar shows for it: working, ready, needs_you (a question, a permission, a plan), waiting (agents or shells it started still run), btw_answered, urgent, follow_up, and a few more. update urgent marks a session as needing the operator urgently, with why: use it only when your instructions say to. Follow-up: when something only Dominik can do blocks your work (a sign-in, a password or code, a payment, a decision your instructions reserve for him), call update {"sessions": "me", "follow_up": true, "why": "<what he does>"} once, just before your final reply. It stays up until Dominik prompts you or you set follow_up false; another agent's message does not clear it. End every final reply with one line: "Follow-up: <what he does>" or "No follow-up."."""


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _bool(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _levels(description: str) -> dict[str, Any]:
    return {"type": ["integer", "string"], "description": description}


def _names(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


SCOPE = {
    "folder": _string("Berth folder to start from: a path (HOMESTEAD/Learning), a unique folder name (Learning), or "
                      "/ for everything. Default: your own session's Berth folder; everything when you are not a "
                      "Berth session. Berth folders are the catalog's, not directories on disk."),
    "down": _levels("Levels of subfolders to add: 0 (default) the folder alone, 1 its children, 2 grandchildren, or \"all\"."),
    "up": _levels("Ancestors to add: 1 the parent, 2 the grandparent too, or \"all\" to the top level. An ancestor "
                  "adds only its own sessions, not its other subfolders, unless branches is true. Default 0."),
    "branches": _bool("With up: take everything under the highest ancestor, down to your folder's level plus down, "
                      "so the parent's other subfolders come in too."),
    "query": _string("Words that must all be in the title (any case), or an id, agent session id, or workspace id "
                     "(or their first 4+ characters). With no folder, searches every folder."),
    "state": {"type": "array", "items": {"type": "string", "enum": list(STATES)},
              "description": "Only these states. busy is a live session in the middle of a turn."},
    "agent": _names("Only these: claude, grok, codex, cursor, shell, process."),
    "pinned": _bool("Only pinned sessions (true), or only unpinned ones (false)."),
    "busy": _bool("Only busy sessions (true), or only idle ones (false). Busy: a turn running, background shells or "
                  "agents still running, a question or dialog open, a command running in a shell, or a pane Berth "
                  "cannot read as a prompt."),
    "urgent": _bool("Only sessions marked urgent (true), or only the others (false)."),
    "follow_up": _bool("Only follow-up sessions (true), or the others."),
}

SESSIONS_REF = {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                "description": "One session or a list: a Berth id or its first 4+ characters, a title, an agent session id, or \"me\"."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "status",
        "description": "Berth at a glance: you (the session and folder calling this, when you run in cmux), session "
                       "counts by state, whether cmux and Berth's watcher are up, and drift (what the watcher's last "
                       "consistency check found). check true runs that check now.",
        "inputSchema": _schema({"check": _bool("Run Berth's consistency check now (Berth against cmux).")}),
    },
    {
        "name": "folders",
        "description": "The folder tree: each folder's path, its level under folder, and how many of its own sessions "
                       "are live, idle, sleeping, and parked. Folders nest; Inbox holds sessions that are not in a folder.",
        "inputSchema": _schema({
            "folder": _string("Show only this Berth folder and what is under it. Default: every folder."),
            "depth": _levels("Levels below folder to show, or \"all\" (default)."),
            "archived": _bool("Include archived folders."),
        }),
    },
    {
        "name": "sessions",
        "description": "The sessions in a scope, grouped by folder top-down. With no arguments: your own folder. Each "
                       "session has id, title, state (live, idle, sleeping, parked, background, playing, pausing), and "
                       "session (the agent's own session id), plus status (live: what cmux's sidebar shows, such as "
                       "working, ready, needs_you, waiting) with status_label, urgent (why and since) when it said it "
                       "needs the operator urgently, pinned and you when true, busy with busy_why (what keeps it "
                       "busy, read from its pane every few seconds), and draft when text is typed in its prompt box "
                       "(a close saves it and puts it back when the session opens). Each folder has its level "
                       "(0 = where the scope starts, -1 = its parent, 1 = a child) and what was left out: deeper "
                       "(subfolders below the scope) or beside (an ancestor's other subfolders).",
        "inputSchema": _schema({
            **SCOPE,
            "fields": {"type": "array", "items": {"type": "string", "enum": ["all", *EXTRA_FIELDS]},
                       "description": "More fields per session: agent, folder, cwd, workspace (cmux), last_used, "
                                      "created, note, park_reason, fork_of, recipe, config_dir (Claude's config "
                                      "folder), account (its Claude account); or all."},
            "limit": {"type": "integer", "description": "Most sessions to return, 1 to 500. Default 100."},
            "offset": {"type": "integer", "description": "Skip this many (next_offset from the last page)."},
            "archived": _bool("Include archived folders."),
        }),
    },
    {
        "name": "session",
        "description": "One session in full: every field, its folder path, and the agent and kind. excerpt adds the "
                       "screen Berth saved when it last closed; history adds its recent Berth events (opened, closed, "
                       "renamed, moved).",
        "inputSchema": _schema({
            "session": _string("A Berth id or its first 4+ characters, a title, an agent session id, or \"me\"."),
            "excerpt": _bool("Add the last screen saved when it closed (up to 40 lines)."),
            "history": {"type": "integer", "description": "Add this many recent events, 1 to 50."},
        }, ["session"]),
    },
    {
        "name": "control",
        "description": "Open and close sessions, as the Berth screen's keys do. wake opens a closed session on its "
                       "conversation (Enter, W). idle closes a live one to free memory, in use again soon (i); sleep "
                       "closes it for longer (S); park puts it away (p). cycle closes and reopens it "
                       "(c). focus shows it in cmux. Name sessions, or give a scope (as in sessions): a scope only "
                       "lists what would happen until confirm, and closes only sessions that are not busy, never you "
                       "or a process. Busy (a turn, background shells or agents, an open "
                       "question) is refused unless force: force cuts that work off. when idle queues the close for "
                       "Berth's watcher, which does it as soon as the session is idle. Closing yourself (\"me\", say to "
                       "load a new MCP server) is always queued: end your turn and Berth cycles you. cancel takes back "
                       "a queued close or send (session shows it as queued). Typed drafts are saved and put back on reopen.",
        "inputSchema": _schema({
            "action": {"type": "string", "enum": list(ACTIONS),
                       "description": "wake, idle, sleep, park, cycle, focus, or cancel (take back a queued close or send)."},
            "sessions": SESSIONS_REF,
            "scope": {"type": "object", "properties": SCOPE, "additionalProperties": False,
                      "description": "Act on every session this scope picks (instead of sessions)."},
            "when": {"type": "string", "enum": ["now", "idle"],
                     "description": "idle, sleep, park, cycle: now (default), or idle: Berth's watcher does it as soon "
                                    "as the session is not busy (within wait minutes). Closing yourself defaults to idle."},
            "force": _bool("sleep, park, cycle a busy session anyway, or one whose draft Berth cannot save exactly. "
                           "Not ideal: a turn in progress, background shells and agents, or an open question are cut "
                           "off. With when idle: do it anyway once wait runs out."),
            "wait": {"type": "integer", "description": "With when idle: minutes to wait for idle, 1 to 1440. Default 60."},
            "focus": _bool("wake and cycle: show the new workspace. Default false."),
            "confirm": _bool("Required to act on a scope, and to close or rerun a process."),
        }, ["action"]),
    },
    {
        "name": "send",
        "description": "Send a prompt to a live Claude or Grok session, after looking at what it is doing. Ready (its "
                       "prompt on screen, no turn running): it sends. Otherwise nothing is sent and you get its "
                       "status, why, and its screen; send again with when: idle (it waits in the session's mailbox, in "
                       "order behind other senders' prompts, and is typed once it is ready), now (a "
                       "working Claude takes it when it can, asked to finish first unless as_is; not Grok), or "
                       "interrupt (Esc, then send). Never types into a question or dialog, or over someone typing. "
                       "Not to yourself. compact (or compact_file) compacts a Claude session with that note first, "
                       "then sends the prompt if given: Berth waits until it is ready, types /compact itself, checks "
                       "Claude recorded the compaction, and returns at once while it runs. Works on yourself and on "
                       "closed sessions. Never place /compact yourself.",
        "inputSchema": _schema({
            "session": _string("The session: a Berth id or its first 4+ characters, a title, or an agent session id."),
            "text": _string("The prompt. Any length, any lines (a slash command: one line)."),
            "prompt_file": _string("An absolute path to a file whose text is the prompt (instead of text)."),
            "when": {"type": "string", "enum": ["now", "idle", "interrupt"],
                     "description": "What to do if it is not ready. Leave it out to look first: nothing is sent "
                                    "unless it is ready."},
            "wait": {"type": "integer", "description": "With when idle: minutes to wait, 1 to 1440. Default 60."},
            "as_is": _bool("With when now: send the prompt without the line asking it to finish first."),
            "lines": {"type": "integer", "description": "Screen lines to return, 1 to 40. Default 15."},
            "compact": _string("Compact first. The note gives the task the summary is for (TASK) and, if the session "
                               "becomes another agent, its seat (SEAT: name, home, who it reports to, whose work it "
                               "leaves alone). Optional: SOURCES (paths) and KEEP OUT (only on purpose, with why). Berth "
                               "adds the frame; no keep or drop lists. Up to 32 KB. Without /compact."),
            "compact_file": _string("An absolute path to a file whose text is the compaction note (instead of compact)."),
        }, ["session"]),
    },
    {
        "name": "create",
        "description": "Start a session in a new cmux workspace, filed in a Berth folder. With from: fork that Claude "
                       "or Grok session, as F does: the conversation is copied under a new session id and the copy "
                       "opens in the original's Berth folder, titled with ⑂; the original keeps running (never type "
                       "/fork yourself). from with cwd forks a Claude conversation into another working directory "
                       "(not for Grok). Without from: a new Claude (default), Grok, or shell session in cwd, filed in "
                       "folder or else your own Berth folder. prompt starts a new Claude session on that first "
                       "message. from with compact (or compact_file) is fork and compact: the fork is compacted with "
                       "that note before its pane opens (the original is untouched), then prompt is sent to it. "
                       "Returns the new session; its session id appears once the agent starts.",
        "inputSchema": _schema({
            "from": _string("Fork this session (a Berth id, title, or session id). It needs at least one turn."),
            "agent": {"type": "string", "enum": ["claude", "grok", "shell"],
                      "description": "A new session's agent. Default claude."},
            "title": _string("The name. Required for a new session; a fork defaults to the original's title plus ⑂."),
            "cwd": _string("The working directory (an absolute path on disk): where a new session runs, or, with "
                           "from, where a Claude fork runs. Default: your own session's working directory, or for a "
                           "fork the original's."),
            "folder": _string("The Berth folder to file a new session in: a path or unique name. Default: your own "
                              "Berth folder. Not a directory."),
            "model": _string("claude: the model (opus, sonnet, haiku, or a full id). Default: Claude's own default "
                             "from ~/.claude/settings.json, and opus when that is unset or Fable."),
            "config_dir": _string("claude: the Claude Code config folder it runs on, one per Claude account "
                                  "(e.g. ~/.claude-personal; \"default\" is ~/.claude). Default: the default "
                                  "account's folder."),
            "prompt": _string("claude: the first message, sent as Claude starts (with from: once the fork is "
                              "compacted and ready). Any length."),
            "compact": _string("With from: compact the fork first. The note is TASK and SEAT, as in send; Berth adds "
                               "the frame. Without /compact."),
            "compact_file": _string("With from: an absolute path to a file whose text is the compaction note."),
            "prompt_file": _string("claude: an absolute path to a file whose text is the first message (instead of prompt)."),
            "command": _string("shell: a command to run instead of a plain shell (it becomes a process)."),
            "focus": _bool("Show the new workspace in cmux. Default false."),
        }),
    },
    {
        "name": "update",
        "description": "Change sessions without opening or closing them. title renames one session everywhere (Berth, "
                       "the cmux sidebar, the agent's own name). folder moves sessions to another Berth folder. cwd "
                       "sets the working directory on disk a Claude, shell, or process session runs in from its next "
                       "wake or cycle, on the same conversation (Grok cannot); cycle true cycles a live one now. A Berth "
                       "folder and a working directory are independent: changing one never changes the other. note "
                       "sets the one-line note. pin puts a session first in cmux's sidebar. urgent true (with why) "
                       "says it needs the operator: red, first, and a notification, until a prompt reaches it; only "
                       "when your instructions say to. follow_up: see the server instructions. "
                       "\"me\" is your own "
                       "session. Name sessions, or give a scope (as in sessions): a scope only lists what would change "
                       "until confirm.",
        "inputSchema": _schema({
            "sessions": SESSIONS_REF,
            "scope": {"type": "object", "properties": SCOPE, "additionalProperties": False,
                      "description": "Change every session this scope picks (instead of sessions). Not with title."},
            "title": _string("New name. One session only."),
            "folder": _string("The Berth folder to move them into: a path or unique name. Inbox means not in a "
                              "Berth folder. Does not change the working directory."),
            "cwd": _string("The working directory to run in from the next open: an absolute path to a directory "
                           "that exists. Does not change the Berth folder."),
            "cycle": _bool("With cwd: cycle each live session now so it opens there (a busy one is refused; your "
                           "own is queued for when your turn ends). With config_dir: cycled onto it once idle."),
            "config_dir": _string("Claude: the Claude Code config folder (one per Claude account) it runs on from "
                                  "its next open or cycle; \"default\" is ~/.claude."),
            "note": _string("One-line note. Empty clears it."),
            "pin": _bool("Pin (true) or unpin (false)."),
            "urgent": _bool("true: the session needs the operator urgently (say why). false: it no longer does. "
                            "Named sessions only, not a scope."),
            "follow_up": _bool("true: something only the operator can do blocks you (why: what he does). false: "
                               "take it back."),
            "why": _string("With urgent or follow_up true: what the operator does, in one line."),
            "confirm": _bool("Required to change a scope."),
        }),
    },
    {
        "name": "folder",
        "description": "Change folders. create makes a folder and every missing level (HOMESTEAD/Tutors makes Tutors "
                       "inside HOMESTEAD). rename renames one folder in place. move puts a folder, with everything in "
                       "it, inside another (into), or at the top level when into is empty. delete removes a folder: its "
                       "sessions and subfolders move up a level; nothing closes. archive hides a folder with no live "
                       "sessions; unarchive shows it again. orchestrator names the folder's orchestrator (session). "
                       "Inbox holds no folders.",
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["create", "rename", "move", "delete", "archive", "unarchive",
                                                  "orchestrator"]},
            "path": _string("The folder: a path, or a unique folder name (create: the new path)."),
            "name": _string("rename: the new name, one level."),
            "into": _string("move: the folder to put it in. Empty: the top level."),
            "session": _string("orchestrator: the session that orchestrates this folder and those below it; "
                               "none: its sessions have none; inherit: clear it."),
        }, ["action", "path"]),
    },
    {
        "name": "delete",
        "description": "Move sessions to Berth's trash, as d does. A live one is closed first. The trash keeps it 30 "
                       "days (trash restores it); the agent's conversation files stay on disk. Name sessions or give a "
                       "scope. Without confirm nothing is deleted: the result lists what would be. Your own session and "
                       "a Berth screen are refused, unless force: then they are listed first as protected, and deleted "
                       "with confirm (your own session is trashed, then its pane closes, ending you).",
        "inputSchema": _schema({
            "sessions": SESSIONS_REF,
            "scope": {"type": "object", "properties": SCOPE, "additionalProperties": False,
                      "description": "Delete every session this scope picks (instead of sessions)."},
            "confirm": _bool("Delete. Without it, only the list of what would be deleted."),
            "force": _bool("Override the protection of your own session and of a Berth screen. Only when the "
                           "operator asked for exactly that."),
        }),
    },
    {
        "name": "trash",
        "description": "Berth's trash: sessions deleted in the last 30 days. list (default) shows them newest first "
                       "with the folder each was in and the days left. restore brings one back, parked, into Inbox, "
                       "with its own conversation; wake then opens it. forget deletes one for good (needs confirm).",
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["list", "restore", "forget"]},
            "item": _string("restore and forget: the trash id, its first characters, or the title."),
            "confirm": _bool("forget: required."),
        }),
    },
]


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def _refs(value: Any) -> list[str]:
    if value is None or value == "" or value == []:
        return []
    items = [value] if isinstance(value, str) else list(value) if isinstance(value, list) else None
    if items is None:
        raise ToolError("BAD_ARGUMENT", "sessions is a name or a list of names")
    return [str(item) for item in items if str(item).strip()]


def _clip(text: str) -> tuple[str, bool]:
    lines = (text or "").splitlines()
    tail = "\n".join(lines[-EXCERPT_LINES:])
    clipped = len(lines) > EXCERPT_LINES
    if len(tail.encode("utf-8")) > EXCERPT_BYTES:
        tail = tail.encode("utf-8")[-EXCERPT_BYTES:].decode("utf-8", "ignore")
        clipped = True
    return tail, clipped


def _row_state(row) -> str:
    from berth.model import closed_state

    return closed_state(row["state"], row["park_reason"])


class Api:
    """The eleven tools. ``db_path``, ``cmux``, and ``caller_workspace`` are injectable for tests."""

    def __init__(self, db_path: Path | str | None = None, *, cmux=None, caller_workspace: str | None = None) -> None:
        from berth.db import default_catalog_path

        self.db_path = Path(db_path) if db_path else default_catalog_path()
        self._cmux = cmux
        # How long a new workspace may take to report its agent before Berth files it anyway.
        self.adopt_wait = 3.0
        self.caller_workspace = caller_workspace if caller_workspace is not None else os.environ.get("CMUX_WORKSPACE_ID", "")

    @property
    def cmux(self):
        if self._cmux is None:
            from berth.cmux import Cmux

            self._cmux = Cmux()
        return self._cmux

    def __call__(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "status": self.status, "folders": self.folders, "sessions": self.sessions, "session": self.session,
            "control": self.control, "send": self.send, "create": self.create, "update": self.update, "folder": self.folder,
            "delete": self.delete, "trash": self.trash,
        }.get(name)
        if handler is None:
            raise ToolError("BAD_ARGUMENT", f"no tool {name}", {"tools": [tool["name"] for tool in TOOLS]})
        if not isinstance(arguments, dict):
            raise ToolError("BAD_ARGUMENT", "arguments are an object")
        try:
            return handler(arguments)
        except BerthError as exc:
            details = {key: value for key, value in dict(exc.details).items() if key != "excerpt"}
            raise ToolError(exc.code, str(exc), details) from exc

    # helpers -----------------------------------------------------------

    def _view(self, archived: bool = False) -> View:
        return View(self.db_path, caller_workspace=self.caller_workspace, archived=archived)

    def _db(self):
        from berth.db import Catalog

        return Catalog(self.db_path).open()

    def _up(self) -> None:
        if not self.cmux.socket_up():
            raise ToolError("CMUX_DOWN", "cmux is not running, so nothing can open or close")

    def _brief(self, db, berth_id: str, **extra) -> dict[str, Any]:
        from berth.ops import folder_path

        row = db.get_berth(berth_id)
        if row is None:
            return {"id": berth_id, **extra}
        out = {"id": row["id"], "title": row["title"], "state": _row_state(row)}
        if row["agent_session_id"]:
            out["session"] = row["agent_session_id"]
        out["folder"] = folder_path(db, row["folder_id"])
        out["cwd"] = row["cwd"] or None  # the working directory; not the Berth folder above
        from berth.workdir import pending_note

        note = pending_note(row)
        if note:
            out["cwd_note"] = note
        out.update(extra)
        return out

    def _is_caller(self, berth) -> bool:
        return bool(self.caller_workspace) and (berth.workspace_id or "").lower() == self.caller_workspace.lower() \
            and berth.state == "live"

    def _targets(self, view: View, arguments: dict, *, allow_scope: bool = True) -> tuple[list, dict | None]:
        refs = _refs(arguments.get("sessions"))
        scope = arguments.get("scope")
        if refs and scope:
            raise ToolError("BAD_ARGUMENT", "give sessions or scope, not both")
        if refs:
            seen, chosen = set(), []
            for ref in refs:
                berth = view.session(ref)
                if berth.id not in seen:
                    seen.add(berth.id)
                    chosen.append(berth)
            return chosen, None
        if scope is not None and allow_scope:
            if not isinstance(scope, dict):
                raise ToolError("BAD_ARGUMENT", "scope is an object, like the sessions tool's arguments")
            chosen, summary = select(view, scope)
            return chosen, summary
        raise ToolError("BAD_ARGUMENT", "name sessions" + (", or give a scope" if allow_scope else ""))

    # reading -----------------------------------------------------------

    def status(self, arguments: dict) -> dict[str, Any]:
        from berth.watch import watcher_state

        view = self._view()
        counts: dict[str, int] = {}
        for berth in view.snap.berths.values():
            if is_screen_berth(berth):
                continue
            state = state_of(berth)
            counts[state] = counts.get(state, 0) + 1
            if state == "live" and berth.busy:
                counts["busy"] = counts.get("busy", 0) + 1
        body: dict[str, Any] = {
            "you": None,
            "counts": {state: counts[state] for state in STATES if counts.get(state)},
            "folders": sum(1 for folder_id in view.snap.folders if view.visible_folder(folder_id)),
            "cmux": "up" if self.cmux.socket_up() else "down",
            "watcher": watcher_state(self.db_path.parent),
        }
        if view.caller is not None:
            body["you"] = view.row(view.caller, ("id", "title", "state", "session", "folder"))
        from berth.health import STARTED_WITH, report

        db = self._db()
        try:
            health = report(db, self.db_path.parent)
        finally:
            db.close()
        body["health"] = {"failing_jobs": health.get("failing_jobs") or {},
                          "watcher_ticking": health.get("watcher_ticking"),
                          "old_code": health.get("old_code") or [],
                          "you_run_old_code": STARTED_WITH != health["code"]}
        try:
            document = json.loads((self.db_path.parent / "invariant.json").read_text(encoding="utf-8"))
            violations = document.get("violations") or []
            body["drift"] = {"ok": bool(document.get("ok")), "at": document.get("at"),
                             "violations": [f"{item.get('rule')}: {item.get('message')}" for item in violations][:10]}
        except (OSError, ValueError):
            body["drift"] = None
        if arguments.get("check") is True:
            from berth.invariant import run

            db = self._db()
            try:
                body["check"] = run(db, self.cmux)
            finally:
                db.close()
        return body

    def folders(self, arguments: dict) -> dict[str, Any]:
        from berth.scope import levels

        view = self._view(bool(arguments.get("archived")))
        start = view.folder(arguments.get("folder")) if arguments.get("folder") not in (None, "") else None
        depth = levels(arguments.get("depth") if "depth" in arguments else ALL, "depth")
        base = view.depth(start)
        listed = ([start] if start else []) + view.subtree(start)
        mine = view.caller.folder_id if view.caller is not None else None
        rows = []
        for folder_id in listed:
            level = view.depth(folder_id) - base
            if depth is not None and level > depth:
                continue
            counts: dict[str, int] = {}
            for berth in view.sessions_in(folder_id):
                state = state_of(berth)
                counts[state] = counts.get(state, 0) + 1
            row: dict[str, Any] = {"folder": view.path(folder_id), "level": level}
            row.update({state: counts[state] for state in ("live", "idle", "sleeping", "parked") if counts.get(state)})
            other = sum(n for state, n in counts.items() if state not in ("live", "idle", "sleeping", "parked"))
            if other:
                row["other"] = other
            if folder_id == mine:
                row["you"] = True
            rows.append(row)
        return {"from": view.path(start), "folders": rows, "total": len(rows)}

    def sessions(self, arguments: dict) -> dict[str, Any]:
        return query(self.db_path, arguments, caller_workspace=self.caller_workspace)

    def session(self, arguments: dict) -> dict[str, Any]:
        view = self._view(archived=True)
        berth = view.session(str(arguments.get("session") or ""))
        out = view.row(berth, ("id", "title", "state", "session", "agent", "folder", "cwd", "workspace",
                               "last_used", "created", "note", "park_reason", "fork_of", "recipe", "config_dir",
                               "account", "memory"))
        out["kind"] = berth.kind
        db = self._db()
        try:
            row = db.get_berth(berth.id)
            data = json.loads(row["attachments"] or "{}") if row is not None else {}
        finally:
            db.close()
        if row is not None:
            from berth.workdir import pending_note

            note = pending_note(row)
            if note:
                out["cwd_note"] = note
        if isinstance(data.get("draft"), dict):
            out["saved_draft"] = {"text": data["draft"].get("text", ""), "exact": data["draft"].get("exact", True),
                                  "restore": "pasted back, unsent, once the session is open and its prompt is ready"}
        from berth.deferred import pending as deferred_pending

        from berth import delivery

        db = self._db()
        try:
            waiting = deferred_pending(db, berth.id)
            mailbox = delivery.waiting(db, berth.id)
        finally:
            db.close()
        if waiting:
            out["queued"] = {key: waiting[0][key] for key in ("action", "until", "requested_by")}
            out["queued"]["when"] = "idle" if waiting[0]["idle"] else "now"
            out["queued"]["force"] = bool(waiting[0]["force"])
        if mailbox:
            # Prompts waiting for it, in the order they will be typed (berth/delivery.py).
            out["prompts"] = [{"delivery": item["id"], "sender": item["sender"], "state": item["state"],
                               "position": number, "until": item["until"],
                               "prompt": item["prompt"][:200] + ("…" if len(item["prompt"]) > 200 else "")}
                              for number, item in enumerate(mailbox, 1)]
        if isinstance(data.get("compaction"), dict):
            item = data["compaction"]
            out["compaction"] = {key: item.get(key) for key in ("job", "state", "mode", "why", "pre_tokens", "post_tokens", "at")
                                 if item.get(key) is not None}
        if isinstance(data.get("draft_unrestored"), dict):
            out["unrestored_draft"] = {"text": data["draft_unrestored"].get("text", ""),
                                       "note": "saved at a close but not put back (the box held other text)"}
        if is_screen_berth(berth):
            out["screen"] = True  # the Berth screen itself, not a session
        if arguments.get("excerpt") is True:
            text, clipped = _clip(berth.excerpt)
            out["excerpt"] = {"at": berth.excerpt_at or None, "text": text, "clipped": clipped}
        history = arguments.get("history")
        if history:
            if not isinstance(history, int) or isinstance(history, bool) or not 1 <= history <= 50:
                raise ToolError("BAD_ARGUMENT", "history is 1 to 50", {"history": history})
            db = self._db()
            try:
                events = list(db.events_for(berth.id))[-history:]
            finally:
                db.close()
            out["history"] = [{"at": item["at"], "event": item["kind"], **_event_brief(item)} for item in events]
        return out

    # acting ------------------------------------------------------------

    def control(self, arguments: dict) -> dict[str, Any]:
        action = str(arguments.get("action") or "")
        if action not in ACTIONS:
            raise ToolError("BAD_ARGUMENT", f"action is one of {', '.join(ACTIONS)}", {"action": action})
        view = self._view()
        chosen, summary = self._targets(view, arguments)
        confirm = arguments.get("confirm") is True
        if action == "cancel":
            return self._cancel(view, chosen, summary, confirm)
        focus = arguments.get("focus") is True
        force = arguments.get("force") is True
        when = arguments.get("when")
        if when not in (None, "now", "idle"):
            raise ToolError("BAD_ARGUMENT", "when is now or idle", {"when": when})
        closing = action in {"idle", "sleep", "park", "cycle"}
        if when == "idle" and not closing:
            raise ToolError("BAD_ARGUMENT", "when idle is for idle, sleep, park, and cycle")
        wait = arguments.get("wait")
        if wait is not None and (not isinstance(wait, int) or isinstance(wait, bool) or not 1 <= wait <= 1440):
            raise ToolError("BAD_ARGUMENT", "wait is 1 to 1440 minutes", {"wait": wait})
        skipped: list[dict] = []
        if summary is not None:
            fits = {
                "wake": lambda b: state_of(b) in {"idle", "sleeping", "parked", "background"},
                "idle": lambda b: state_of(b) == "live",
                "sleep": lambda b: state_of(b) == "live",
                "park": lambda b: state_of(b) == "live",
                "cycle": lambda b: state_of(b) == "live",
                "focus": lambda b: state_of(b) == "live",
            }[action]
            picked = []
            db = self._db() if closing and when != "idle" else None
            try:
                for berth in chosen:
                    if self._is_caller(berth):
                        skipped.append({"id": berth.id, "title": berth.title, "why": "you"})
                    elif berth.kind == "process" and action != "focus":
                        skipped.append({"id": berth.id, "title": berth.title, "why": "a process is closed or rerun only when named"})
                    elif fits(berth):
                        # when idle: every session is queued and closed as it becomes idle.
                        why = self._unsafe_to_close(db, berth) if db is not None else []
                        if why:
                            skipped.append({"id": berth.id, "title": berth.title, "why": "busy: " + "; ".join(why)})
                        else:
                            picked.append(berth)
            finally:
                if db is not None:
                    db.close()
            chosen = picked
            if not confirm:
                plan = {"action": action, "confirmed": False, "scope": summary,
                        "would": [view.row(b, ("id", "title", "state")) for b in chosen], "skipped": skipped}
                if when == "idle":
                    plan["when"] = "idle: each one as soon as it is idle"
                return plan
        if action == "focus" and len(chosen) != 1:
            raise ToolError("BAD_ARGUMENT", "focus shows one session", {"sessions": len(chosen)})
        done, failed, queued = [], [], []
        db = self._db()
        try:
            for berth in chosen:
                me = self._is_caller(berth)
                try:
                    if closing and (me or when == "idle"):
                        queued.append(self._queue(db, action, berth, me=me, when=when, force=force, wait=wait))
                        continue
                    # A named session: force (or confirm, as before) closes a busy one; confirm closes a process.
                    result = self._act(db, action, berth, focus=focus,
                                       confirm=summary is None and (force or confirm))
                    done.append(self._brief(db, berth.id, result=result))
                except (BerthError, ToolError) as exc:
                    code = exc.code
                    item = {"id": berth.id, "title": berth.title, "code": code, "message": str(exc)}
                    if code == "BUSY":
                        item["hint"] = ("when idle queues it for when the session is idle; force does it now and cuts "
                                        "the work off")
                    failed.append(item)
        finally:
            db.close()
        body: dict[str, Any] = {"action": action, "done": done}
        if queued:
            body["queued"] = queued
        if failed:
            body["failed"] = failed
        if skipped:
            body["skipped"] = skipped
        if summary is not None:
            body["scope"] = summary
        return body

    def _queue(self, db, action: str, berth, *, me: bool, when: str | None, force: bool, wait: int | None) -> dict:
        """Hand a close to Berth's watcher: for yourself (your pane cannot close itself), or for when it is idle."""
        from berth.deferred import SELF_GRACE_SECONDS, queue

        if is_screen_berth(berth):
            raise ToolError("PROTECTED", "the Berth screen is not a session")
        if me and when == "now" and not force:
            raise ToolError("BUSY", "closing yourself now cuts this turn off; leave when out (idle: after your turn), "
                                    "or pass force to do it in a few seconds anyway")
        idle = when != "now"
        caller = self._view().caller
        item = queue(db, berth.id, action, idle=idle, force=force, wait_minutes=wait,
                     delay_seconds=SELF_GRACE_SECONDS if me else 0,
                     requested_by=(caller.title if caller is not None else "an agent outside Berth"))
        if me and idle:
            note = f"Berth's watcher will {action} you as soon as this turn ends: finish your reply and stop."
        elif me:
            note = f"Berth's watcher will {action} you in {SELF_GRACE_SECONDS} seconds, cutting this turn off."
        else:
            note = f"Berth's watcher will {action} it as soon as it is idle."
        if idle:
            note += f" It gives up at {item['until']}" + (" and then does it anyway (force)." if force else ".")
        return {"id": berth.id, "title": berth.title, "action": action, "when": "idle" if idle else "now",
                "force": force, "until": item["until"], "note": note}

    def _unsafe_to_close(self, db, berth) -> list[str]:
        """Why a session should not be closed now, read from its pane this moment (berth/activity.py)."""
        from berth.activity import observe, program_states

        row = db.get_berth(berth.id)
        if row is None or row["state"] != "live":
            return []
        activity = observe(self.cmux, row, programs=program_states(self.db_path.parent))
        why = list(activity.reasons)
        if activity.draft is not None and not activity.draft.exact:
            why.append(f"a draft Berth cannot save exactly ({activity.draft.why})")
        return why

    def _act(self, db, action: str, berth, *, focus: bool, confirm: bool) -> str:
        from berth.hooks import hook_for, load_hooks
        from berth.ops import cycle_berth, idle_berth, sleep_berth
        from berth.place import pid_alive
        from berth.state import pause_berth, play_berth

        if is_screen_berth(berth):
            raise ToolError("PROTECTED", "the Berth screen is not a session")
        row = db.require_berth(berth.id)
        state = _row_state(row)
        if state in {"playing", "pausing"}:
            raise ToolError("IN_PROGRESS", f"{row['title']} is {state}")
        workspace = row["cmux_workspace_id"]
        caller = self.caller_workspace or None
        if action == "wake":
            if state == "live":
                if focus:
                    self._up()
                    self.cmux.select_workspace(workspace)
                    return "focused"
                return "already live"
            self._up()
            play_berth(db, self.cmux, row["id"], focus=focus, run_command=confirm)
            return "woken"
        if action == "focus":
            if state != "live":
                raise ToolError("NOT_LIVE", f"{row['title']} is {state}; wake opens it")
            self._up()
            self.cmux.select_workspace(workspace)
            return "focused"
        if action == "cycle":
            if state != "live":
                raise ToolError("NOT_LIVE", f"{row['title']} is {state}; cycle restarts a live session, wake opens a closed one")
            self._up()
            cycle_berth(db, self.cmux, row["id"], confirm=confirm, focus=focus, hook=hook_for(load_hooks(), workspace),
                        caller_workspace_id=caller, pid_alive_fn=pid_alive)
            return "cycled"
        if action == "idle":
            if state == "idle":
                return "already idle"
            if state == "live":
                self._up()
            idle_berth(db, self.cmux, row["id"], pid_alive_fn=pid_alive, caller_workspace_id=caller,
                       hook=hook_for(load_hooks(), workspace), confirm=confirm)
            return "idle"
        if action == "sleep":
            if state == "sleeping":
                return "already sleeping"
            if state == "live":
                self._up()
            sleep_berth(db, self.cmux, row["id"], pid_alive_fn=pid_alive, caller_workspace_id=caller,
                        hook=hook_for(load_hooks(), workspace), confirm=confirm)
            return "asleep"
        if action == "park":
            if state == "parked":
                return "already parked"
            if state == "live":
                self._up()
            pause_berth(db, self.cmux, row["id"], force=confirm, confirm=confirm, hook=hook_for(load_hooks(), workspace),
                        caller_workspace_id=caller, pid_alive_fn=pid_alive)
            return "parked"
        raise ToolError("BAD_ARGUMENT", f"no action {action}")

    def send(self, arguments: dict) -> dict[str, Any]:
        """A prompt to one live session, after a look at its pane (berth/send.py)."""
        from berth.activity import program_states
        from berth.deferred import queue
        from berth.send import BUSY_NOTE, KINDS, WHAT_TO_DO, agent_of, deliver, interrupt, is_slash, look, refused

        ref = str(arguments.get("session") or "")
        if not ref:
            raise ToolError("BAD_ARGUMENT", "name the session to send to")
        when = arguments.get("when")
        if when not in (None, "now", "idle", "interrupt"):
            raise ToolError("BAD_ARGUMENT", "when is now, idle, or interrupt", {"when": when})
        wait = arguments.get("wait")
        if wait is not None and (not isinstance(wait, int) or isinstance(wait, bool) or not 1 <= wait <= 1440):
            raise ToolError("BAD_ARGUMENT", "wait is 1 to 1440 minutes", {"wait": wait})
        lines = arguments.get("lines", 15)
        if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= 40:
            raise ToolError("BAD_ARGUMENT", "lines is 1 to 40", {"lines": lines})
        if arguments.get("compact") not in (None, "") or arguments.get("compact_file") not in (None, ""):
            if arguments.get("when") not in (None, "idle"):
                raise ToolError("BAD_ARGUMENT", "a compaction always waits until the session is ready; leave out when")
            berth = self._view().session(ref)
            if is_screen_berth(berth):
                raise ToolError("PROTECTED", "the Berth screen is not a session")
            return self._compact(berth, arguments)
        body = self._send_body(arguments)
        view = self._view()
        berth = view.session(ref)
        if is_screen_berth(berth):
            raise ToolError("PROTECTED", "the Berth screen is not a session")
        if self._is_caller(berth):
            raise ToolError("PROTECTED", "that is you: a prompt to yourself is just your next step")
        db = self._db()
        try:
            row = db.require_berth(berth.id)
            state = _row_state(row)
            if state != "live":
                raise ToolError("NOT_LIVE", f"{row['title']} is {state}: wake it (control wake), then send",
                                {"state": state})
            if agent_of(row) not in KINDS:
                raise ToolError("KIND_UNSAFE", "send is for Claude and Grok sessions; type into a shell with "
                                               "cmux-mcp's input, target terminal", {"kind": row["kind"]})
            self._up()
            programs = program_states(self.db_path.parent)

            def reread():
                return look(self.cmux, row, lines=lines, programs=programs)

            seen = reread()
            out = self._brief(db, row["id"])

            def report(current, sent: bool, **extra) -> dict[str, Any]:
                from berth.cmux_sidebar import status_of

                shown = status_of(current.activity, row["kind"] or "")
                body_out = {**out, "sent": sent, "status": current.situation, "status_label": shown.label}
                if current.activity.reasons:
                    body_out["why"] = list(current.activity.reasons)
                body_out["screen"] = {"lines": lines, "clipped": current.clipped, "text": current.tail}
                body_out.update(extra)
                return body_out

            def send_now(text: str, current, **extra) -> dict[str, Any]:
                try:
                    detail = deliver(self.cmux, row, text, reread=reread)
                    from berth.attention import note_agent_input

                    caller = self._view().caller
                    # Typed by an agent, not Dominik: it never answers this session's flag (berth/attention.py).
                    note_agent_input(db, row["id"], text, by=caller.title if caller is not None else "an agent")
                except BerthError as exc:
                    why = refused(exc)
                    if why is not None:
                        hint = "when idle sends it once the box is clear" if why == "typing" else WHAT_TO_DO["needs_you"]
                        return report(reread(), False, refused=exc.message, what_to_do=hint, **extra)
                    raise
                return report(current, True, **detail, **extra)

            from berth import delivery

            ahead = delivery.waiting(db, row["id"])  # prompts already in its mailbox go first (berth/delivery.py)
            if seen.situation == "ready" and not ahead:
                return send_now(body, seen)
            if when is None:
                if ahead:
                    return report(seen, False, waiting=len(ahead), what_to_do=(
                        f"{len(ahead)} prompt(s) are waiting for {row['title']} ahead of yours; send again with "
                        "when idle and yours is typed after them"))
                return report(seen, False, what_to_do=WHAT_TO_DO[seen.situation])
            if when == "idle":
                caller = view.caller
                item = delivery.enqueue(db, row["id"], body,
                                        sender=caller.title if caller is not None else "an agent outside Berth",
                                        wait_minutes=wait or 60)
                return report(seen, False, queued={
                    "when": "idle", "until": item["until"], "delivery": item["id"], "position": item["position"],
                    "note": f"Berth's watcher types it as soon as {row['title']} is ready and the prompts ahead of "
                            f"it are in; it gives up at {item['until']}. control cancel takes it back."})
            if seen.situation != "working":
                return report(seen, False, what_to_do=WHAT_TO_DO[seen.situation])
            if when == "now":
                if (row["kind"] or "") == "grok":
                    raise ToolError("BUSY", "a message sent mid-turn stops Grok's turn: use when idle, or interrupt "
                                            "if stopping it is what you want")
                if is_slash(body):
                    raise ToolError("BUSY", "a slash command runs only at a ready prompt: use when idle")
                text = body if arguments.get("as_is") is True else BUSY_NOTE + body
                return send_now(text, seen, note="Claude takes it as soon as it can, which can be before its current "
                                                 "work is done" + ("" if arguments.get("as_is") is True else
                                                                   "; it was asked to finish that first"))
            after = interrupt(self.cmux, row, reread)
            if after.situation != "ready":
                return report(after, False, interrupted=True,
                              what_to_do=WHAT_TO_DO.get(after.situation, "Esc was pressed, but its prompt did not "
                                                        "come back in time; look with screen, then send again."))
            return send_now(body, after, interrupted=True)
        finally:
            db.close()

    def _note(self, arguments: dict) -> str | None:
        """The compaction note from compact or compact_file, or None."""
        text, file = arguments.get("compact"), arguments.get("compact_file")
        if text not in (None, "") and file not in (None, ""):
            raise ToolError("BAD_ARGUMENT", "give compact or compact_file, not both")
        if file not in (None, ""):
            path = Path(str(file)).expanduser()
            if not path.is_absolute() or not path.is_file() or path.stat().st_size > 64 * 1024:
                raise ToolError("BAD_ARGUMENT", "compact_file is an absolute path to a readable file under 64 KiB",
                                {"compact_file": str(file)})
            text = path.read_text(encoding="utf-8")
        return None if text in (None, "") else str(text)

    def _compact(self, berth, arguments: dict, *, fork: bool = False) -> dict[str, Any]:
        """Start a compaction (berth/compact.py): a detached worker does it and sends the prompt after."""
        from berth.compact import plan, start

        note = self._note(arguments)
        has_prompt = arguments.get("text") not in (None, "") or arguments.get("prompt") not in (None, "") or \
            arguments.get("prompt_file") not in (None, "")
        prompt = None
        if has_prompt:
            prompt = self._send_body({"text": arguments.get("text") or arguments.get("prompt"),
                                      "prompt_file": arguments.get("prompt_file")})
        wait = arguments.get("wait") or 60
        title = arguments.get("title")
        self._up()
        db = self._db()
        try:
            caller = self._view().caller
            job = plan(db, berth.id, note=note, prompt=prompt, fork=fork,
                       title=title.strip() if isinstance(title, str) and title.strip() else None,
                       wait_minutes=wait, caller=self.caller_workspace, cmux=self.cmux,
                       requested_by=caller.title if caller is not None else "an agent outside Berth",
                       focus=arguments.get("focus") is True,
                       cwd=str(arguments["cwd"]) if fork and arguments.get("cwd") not in (None, "") else None)
            started = start(db, job)
            out = self._brief(db, job["berth_id"])
            if fork:
                out["fork_of"] = {"id": berth.id, "title": berth.title}
            steps = {"fork": "compacting the fork before its pane opens, then opening it",
                     "pane": "waiting until it is ready, then typing /compact with the note",
                     "closed": "compacting it while it stays closed"}[job["mode"]]
            if job["mode"] == "pane" and job.get("own"):
                steps = "waiting for your turn to end, then typing /compact with the note"
            if not note:
                steps = "opening the fork"
            out["compaction"] = {
                "job": started["job"], "state": started["state"],
                "doing": steps + (", then sending the prompt" if prompt else "") + ".",
                "follow": "session shows compaction.state (done or failed, with why); Claude's own record of the "
                          "compaction is the proof. Don't place /compact or the prompt yourself.",
            }
            return out
        except BerthError as exc:
            raise ToolError(exc.code, exc.message, exc.details) from exc
        finally:
            db.close()

    def _send_body(self, arguments: dict) -> str:
        text, file = arguments.get("text"), arguments.get("prompt_file")
        if (text in (None, "")) == (file in (None, "")):
            raise ToolError("BAD_ARGUMENT", "give text or prompt_file")
        if file not in (None, ""):
            path = Path(str(file)).expanduser()
            if not path.is_absolute() or not path.is_file() or path.stat().st_size > 512 * 1024:
                raise ToolError("BAD_ARGUMENT", "prompt_file is an absolute path to a readable file under 512 KiB",
                                {"prompt_file": str(file)})
            text = path.read_text(encoding="utf-8")
        from berth.send import check

        check(str(text))
        return str(text)

    def create(self, arguments: dict) -> dict[str, Any]:
        source = arguments.get("from")
        if source not in (None, ""):
            return self._fork(str(source), arguments)
        from berth.adopt import adopt_appearing
        from berth.color import paint_berth
        from berth.hooks import load_hooks
        from berth.ops import move_berth

        agent = str(arguments.get("agent") or "claude")
        if agent not in {"claude", "grok", "shell"}:
            raise ToolError("BAD_ARGUMENT", "agent is claude, grok, or shell", {"agent": agent})
        title = " ".join(str(arguments.get("title") or "").split())
        if not title:
            raise ToolError("BAD_ARGUMENT", "a new session needs a title")
        if arguments.get("prompt") not in (None, "") and arguments.get("prompt_file") not in (None, ""):
            raise ToolError("BAD_ARGUMENT", "give prompt or prompt_file, not both")
        if arguments.get("prompt_file") not in (None, ""):
            source = Path(str(arguments["prompt_file"])).expanduser()
            if not source.is_absolute() or not source.is_file() or source.stat().st_size > 512 * 1024:
                raise ToolError("BAD_ARGUMENT", "prompt_file is an absolute path to a readable file under 512 KiB",
                                {"prompt_file": str(source)})
            arguments = {**arguments, "prompt": source.read_text(encoding="utf-8")}
        if agent == "shell" and _starts_screen(str(arguments.get("command") or "")):
            # One Berth screen, in the workspace named Berth (berth/screens.py). It is not a session to create.
            from berth.screens import holder

            running = holder(self.db_path.parent)
            raise ToolError("PROTECTED", "the Berth screen is not created: at most one runs, in the workspace named "
                                         "Berth" + (" (it is open now)" if running else ""),
                            {"open": running, "hint": "control focus (or wake) the row titled Berth; the operator "
                                                      "presses ⌘⇧B"})
        for name, allowed in (("prompt", "claude"), ("model", "claude"), ("command", "shell"), ("config_dir", "claude")):
            if arguments.get(name) not in (None, "") and agent != allowed:
                raise ToolError("BAD_ARGUMENT", f"{name} is for {allowed} sessions",
                                {"agent": agent, "hint": "send a Grok its first prompt after it starts" if name == "prompt" else None})
        view = self._view()
        cwd = Path(str(arguments.get("cwd") or (view.caller.cwd if view.caller is not None and view.caller.cwd else Path.home())))
        cwd = cwd.expanduser()
        if not cwd.is_dir():
            raise ToolError("BAD_ARGUMENT", f"{cwd} is not a directory", {"cwd": str(cwd)})
        if arguments.get("folder") not in (None, ""):
            folder = view.path(view.folder(str(arguments["folder"])))
            if folder == "/":
                raise ToolError("BAD_ARGUMENT", "a session goes in a folder; Inbox means not in a folder")
        elif view.caller is not None:
            folder = view.path(view.caller.folder_id)
        else:
            folder = "Inbox"
        config_dir = None
        from berth.configdir import check as check_config_dir

        if arguments.get("config_dir") not in (None, ""):
            try:
                config_dir = check_config_dir(arguments["config_dir"])
            except BerthError as exc:
                raise ToolError(exc.code, exc.message, exc.details) from exc
        elif agent == "claude":
            # No folder named: the default account's (Settings Agent's default_account, berth/accounts.py).
            from berth.accounts import default_folder

            try:
                config_dir = check_config_dir(default_folder())
            except BerthError:
                config_dir = None  # a default folder that is not there: ~/.claude, as before
        command = spawn_command(agent, title, model=arguments.get("model"), command=arguments.get("command"),
                                prompt_file=self._prompt_file(str(arguments["prompt"])) if arguments.get("prompt") else None,
                                known_sessions={extra.session for extra in view.extra.values() if extra.session},
                                config_dir=config_dir)
        self._up()
        created = self.cmux.create_workspace(title=title, cwd=str(cwd), command=command, focus=arguments.get("focus") is True)
        db = self._db()
        try:
            record = adopt_appearing(db, self.cmux, load_hooks, created.id, wait_s=self.adopt_wait)
            if record is None:
                raise ToolError("CMUX_REJECTED", "cmux opened the workspace but Berth could not file it", {"workspace": created.id})
            # The session exists from here on: a step that fails now is a warning in the result, never an error, so
            # no caller takes it for "not created" and starts a second one (2026-11-10, UNI Orch: create answered
            # KIND_UNSAFE for a Claude it had started and prompted).
            warnings = []
            try:
                move_berth(db, record["id"], folder)
            except BerthError as exc:
                warnings.append(f"not filed in {folder}: {exc.message}")
            if config_dir is not None:
                from berth.configdir import set_config_dir

                try:
                    set_config_dir(db, record["id"], config_dir, by=view.caller.title if view.caller is not None else "",
                                   spawned_claude=agent == "claude")
                except BerthError as exc:
                    warnings.append(f"its config folder is not recorded: {exc.message}")
            with contextlib.suppress(Exception):
                paint_berth(db, self.cmux, record["id"])
            brief = self._brief(db, record["id"], agent=agent, cwd=str(cwd))
            if config_dir is not None:
                brief["config_dir"] = config_dir
            if warnings:
                brief["warnings"] = warnings
            return brief
        finally:
            db.close()

    def _fork(self, source: str, arguments: dict) -> dict[str, Any]:
        from berth.ops import fork_berth

        for name in ("agent", "folder", "model", "command"):
            if arguments.get(name) not in (None, ""):
                raise ToolError("BAD_ARGUMENT", f"a fork takes title, cwd, compact, prompt, and focus, not {name}",
                                {"hint": "a fork is filed in the original's Berth folder; move it with update"})
        wants = [name for name in ("compact", "compact_file", "prompt", "prompt_file") if arguments.get(name) not in (None, "")]
        if wants:
            return self._compact(self._view().session(source), arguments, fork=True)
        view = self._view()
        berth = view.session(source)
        self._up()
        title = arguments.get("title")
        db = self._db()
        try:
            record = fork_berth(db, self.cmux, berth.id, focus=arguments.get("focus") is True,
                                title=title.strip() if isinstance(title, str) and title.strip() else None,
                                cwd=arguments.get("cwd") if arguments.get("cwd") not in (None, "") else None)
            return self._brief(db, record["id"], fork_of={"id": berth.id, "title": berth.title})
        finally:
            db.close()

    def _prompt_file(self, text: str) -> Path:
        """The first message, in a private file the launch command reads (any length, any characters)."""
        import time
        import uuid

        directory = self.db_path.parent / "prompts"
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        for old in directory.glob("*.md"):
            with contextlib.suppress(OSError):
                if time.time() - old.stat().st_mtime > 86400:
                    old.unlink()
        path = directory / f"{uuid.uuid4()}.md"
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        return path

    def _cancel(self, view, chosen, summary, confirm: bool) -> dict[str, Any]:
        """control cancel: take back the close queued for each session (berth/deferred.py)."""
        from berth.deferred import cancel, pending

        db = self._db()
        try:
            queued = {item["berth_id"]: item for item in pending(db)}
            if summary is not None and not confirm:
                return {"action": "cancel", "confirmed": False, "scope": summary,
                        "would": [{**view.row(b, ("id", "title")), "queued": queued[b.id]["action"]}
                                  for b in chosen if b.id in queued]}
            from berth import delivery

            caller = view.caller
            mine = caller.title if caller is not None else "an agent outside Berth"
            cancelled, nothing = [], []
            for berth in chosen:
                item = cancel(db, berth.id, requested_by=self.caller_workspace or "")
                # Your own queued prompts for it too; never another sender's (berth/delivery.py).
                taken = [delivery.cancel(db, prompt["id"], sender=mine)["id"] for prompt in delivery.waiting(db, berth.id)
                         if prompt["state"] == "queued" and prompt["sender"] == mine]
                if item is None and not taken:
                    nothing.append({"id": berth.id, "title": berth.title})
                    continue
                entry = {"id": berth.id, "title": berth.title}
                if item is not None:
                    entry["action"] = item["action"]
                if taken:
                    entry["prompts"] = taken
                cancelled.append(entry)
        finally:
            db.close()
        body: dict[str, Any] = {"action": "cancel", "cancelled": cancelled}
        if nothing:
            body["nothing_queued"] = nothing
        if summary is not None:
            body["scope"] = summary
        return body

    def update(self, arguments: dict) -> dict[str, Any]:
        from berth.attention import lower_flag, raise_flag
        from berth.color import paint_berth
        from berth.ops import move_berth, rename_berth, set_note
        from berth.pins import set_pinned

        changes = [name for name in ("title", "folder", "cwd", "note", "pin", "urgent", "follow_up", "config_dir")
                   if name in arguments]
        if not changes:
            raise ToolError("BAD_ARGUMENT", "change something: title, folder, cwd, note, pin, urgent, follow_up, or "
                                            "config_dir")
        if "cycle" in arguments and "cwd" not in arguments and "config_dir" not in arguments:
            raise ToolError("BAD_ARGUMENT", "cycle goes with cwd or config_dir")
        new_config = False
        config_dir = None
        if "config_dir" in arguments:
            from berth.configdir import check as check_config_dir

            try:
                config_dir = check_config_dir(arguments.get("config_dir"))
            except BerthError as exc:
                raise ToolError(exc.code, exc.message, exc.details) from exc
            new_config = True
        new_cwd = None
        if "cwd" in arguments:
            from berth.workdir import check_directory

            new_cwd = check_directory(arguments.get("cwd"))
        for name in ("pin", "urgent", "follow_up"):
            if name in arguments and not isinstance(arguments.get(name), bool):
                raise ToolError("BAD_ARGUMENT", f"{name} is true or false")
        if arguments.get("urgent") is True and arguments.get("follow_up") is True:
            raise ToolError("BAD_ARGUMENT", "urgent or follow_up, not both")
        if "why" in arguments and arguments.get("urgent") is not True and arguments.get("follow_up") is not True:
            raise ToolError("BAD_ARGUMENT", "why goes with urgent true or follow_up true")
        for name in ("urgent", "follow_up"):
            if name in arguments and arguments.get("scope") is not None:
                raise ToolError("BAD_ARGUMENT", f"{name} names sessions, not a scope")
        if arguments.get("follow_up") is True:
            from berth.attention import check_follow_up

            try:
                check_follow_up(str(arguments.get("why") or ""))  # before anything changes
            except BerthError as exc:
                raise ToolError(exc.code, exc.message, exc.details) from exc
        view = self._view()
        chosen, summary = self._targets(view, arguments)
        if "title" in arguments:
            title = str(arguments.get("title") or "").strip()
            if not title:
                raise ToolError("BAD_ARGUMENT", "title is empty")
            if summary is not None or len(chosen) != 1:
                raise ToolError("BAD_ARGUMENT", "a title names one session")
        target_path = None
        if "folder" in arguments:
            target_path = view.path(view.folder(str(arguments.get("folder") or "")))
            if target_path == "/":
                raise ToolError("BAD_ARGUMENT", "a session goes in a folder; Inbox means not in a folder")
        skipped: list[dict] = []
        if new_cwd is not None:
            # Grok sessions (and agents not tested) keep their directory: refused when named, skipped in a scope.
            from berth.workdir import unmovable

            db = self._db()
            try:
                movable = []
                for berth in chosen:
                    reason = unmovable(db.require_berth(berth.id))
                    if reason is None:
                        movable.append(berth)
                    elif summary is None:
                        raise ToolError("KIND_UNSAFE", f"{berth.title}: {reason}", {"id": berth.id})
                    else:
                        skipped.append({"id": berth.id, "title": berth.title, "why": reason})
                chosen = movable
            finally:
                db.close()
        if new_config:
            # A config folder is Claude's: other sessions are refused when named, skipped in a scope.
            kept = []
            for berth in chosen:
                if agent_of(berth.kind, berth.agent_name) == "claude":
                    kept.append(berth)
                elif summary is None:
                    raise ToolError("KIND_UNSAFE", f"{berth.title}: config_dir is for Claude sessions", {"id": berth.id})
                else:
                    skipped.append({"id": berth.id, "title": berth.title, "why": "not a Claude session"})
            chosen = kept
        if summary is not None:
            chosen = [berth for berth in chosen if not is_screen_berth(berth)]
            if arguments.get("confirm") is not True:
                plan = {"confirmed": False, "changes": changes, "scope": summary,
                        "would_change": [view.row(berth, ("id", "title", "state", "folder", "cwd")) for berth in chosen]}
                if new_cwd is not None:
                    plan["new_cwd"] = new_cwd
                if new_config:
                    plan["new_config_dir"] = config_dir or "~/.claude"
                if skipped:
                    plan["skipped"] = skipped
                return plan
        elif any(is_screen_berth(berth) for berth in chosen):
            raise ToolError("PROTECTED", "the Berth screen is not a session")
        done = []
        db = self._db()
        try:
            for berth in chosen:
                if target_path is not None:
                    move_berth(db, berth.id, target_path)
                    with contextlib.suppress(Exception):
                        paint_berth(db, self.cmux, berth.id)
                cycled = None
                if new_cwd is not None:
                    from berth.workdir import set_cwd

                    set_cwd(db, berth.id, new_cwd, by=view.caller.title if view.caller is not None else "")
                    if arguments.get("cycle") is True and berth.state == "live":
                        cycled = self._cycle_into(db, berth, summary is not None)
                if new_config:
                    from berth.configdir import set_config_dir

                    set_config_dir(db, berth.id, config_dir, by=view.caller.title if view.caller is not None else "")
                    if arguments.get("cycle") is True and berth.state == "live" and cycled is None:
                        # Moving a session to another account waits until it is safe: idle, like any queued cycle.
                        self._queue(db, "cycle", berth, me=self._is_caller(berth), when="idle", force=False, wait=None)
                        cycled = "queued: Berth's watcher cycles it onto the new config folder as soon as it is idle"
                if "note" in arguments:
                    set_note(db, berth.id, " ".join(str(arguments.get("note") or "").split()))
                if "pin" in arguments:
                    set_pinned(db, berth.id, arguments["pin"])
                if arguments.get("urgent") is True:
                    raise_flag(db, berth.id, str(arguments.get("why") or ""), by=self.caller_workspace or "",
                               cmux=self.cmux)
                elif arguments.get("urgent") is False:
                    lower_flag(db, berth.id, level="urgent")
                if arguments.get("follow_up") is True:
                    try:
                        raise_flag(db, berth.id, str(arguments.get("why") or ""), by=self.caller_workspace or "",
                                   level="follow_up")
                    except BerthError as exc:
                        raise ToolError(exc.code, exc.message, exc.details) from exc
                elif arguments.get("follow_up") is False:
                    lower_flag(db, berth.id, level="follow_up")
                if "title" in arguments:
                    if berth.state == "live":
                        self._up()
                    rename_berth(db, self.cmux, berth.id, str(arguments["title"]).strip())
                brief = self._brief(db, berth.id)
                if cycled is not None:
                    brief["cycle"] = cycled
                now = db.require_berth(berth.id)
                if now["pinned"]:
                    brief["pinned"] = True
                if now["attention"] in ("urgent", "follow_up"):
                    brief[now["attention"]] = {"why": now["attention_why"], "since": now["attention_at"]}
                    if arguments.get("follow_up") is True and now["attention"] == "urgent":
                        brief["note"] = "it is urgent: follow_up does not replace that"
                done.append(brief)
        finally:
            db.close()
        body: dict[str, Any] = {"changed": changes, "sessions": done}
        if skipped:
            body["skipped"] = skipped
        if summary is not None:
            body["scope"] = summary
        return body

    def _cycle_into(self, db, berth, in_scope: bool) -> str:
        """Cycle a live session so it opens in its new working directory. Never cuts work off."""
        if self._is_caller(berth):
            self._queue(db, "cycle", berth, me=True, when=None, force=False, wait=None)
            return "queued: Berth's watcher cycles you into the new directory once this turn ends"
        why = self._unsafe_to_close(db, berth)
        if why:
            return ("not cycled, busy: " + "; ".join(why) + ". It opens there at its next cycle; "
                    "control cycle with when idle does that as soon as it is idle")
        try:
            self._act(db, "cycle", berth, focus=False, confirm=False)
        except (BerthError, ToolError) as exc:
            return f"not cycled ({exc.code}): {exc}. It opens there at its next cycle"
        return "cycled: open in the new directory now"

    def folder(self, arguments: dict) -> dict[str, Any]:
        from berth.ops import (archive_folder, mkdir_path, move_folder_path, rename_folder_path, rmdir_folder,
                               unarchive_folder)

        action = str(arguments.get("action") or "")
        raw = str(arguments.get("path") or "").strip().strip("/")
        if not raw:
            raise ToolError("BAD_ARGUMENT", "path is empty")
        db = self._db()
        try:
            if action == "create":
                return {"action": action, **mkdir_path(db, raw)}
            view = self._view(archived=True)
            path = view.path(view.folder(raw))
            if path == "/":
                raise ToolError("BAD_ARGUMENT", "the root is not a folder")
            if action == "rename":
                name = str(arguments.get("name") or "").strip()
                if not name or "/" in name:
                    raise ToolError("BAD_ARGUMENT", "name is one level, no slash")
                return {"action": action, **rename_folder_path(db, path, name)}
            if action == "move":
                into = str(arguments.get("into") or "").strip().strip("/")
                if into:
                    into = view.path(view.folder(into))
                return {"action": action, **move_folder_path(db, path, into)}
            if action == "delete":
                return {"action": action, **rmdir_folder(db, path)}
            if action == "archive":
                return {"action": action, **archive_folder(db, path)}
            if action == "unarchive":
                return {"action": action, **unarchive_folder(db, path)}
            if action == "orchestrator":
                from berth.orchestrator import set_orchestrator

                from berth.orchestrator import NONE

                named = str(arguments.get("session") or "").strip()
                word = named.lower()
                target = NONE if word == "none" else None if word in ("", "inherit") else view.session(named).id
                caller = view.caller
                return {"action": action, **set_orchestrator(db, path, target,
                                                             by=caller.title if caller is not None else "")}
            raise ToolError("BAD_ARGUMENT", "action is create, rename, move, delete, archive, unarchive, or orchestrator",
                            {"action": action})
        finally:
            db.close()

    def delete(self, arguments: dict) -> dict[str, Any]:
        from berth.ops import delete_berth

        view = self._view()
        chosen, summary = self._targets(view, arguments)
        force = arguments.get("force") is True
        refused = [{"id": b.id, "title": b.title, "why": "you" if self._is_caller(b) else "a Berth screen"}
                   for b in chosen if self._is_caller(b) or is_screen_berth(b)]
        if force and refused:
            # His words, 2026-10-30: never impossible to delete, even past a safety check. Listed, then confirmed.
            protected, refused = refused, []
            if arguments.get("confirm") is not True:
                body = {"confirmed": False, "would_delete": [view.row(b, ("id", "title", "state")) for b in chosen],
                        "protected": protected,
                        "warning": "force deletes these too: a Berth screen closes, and your own session ends"}
                if summary is not None:
                    body["scope"] = summary
                return body
            mine = [b for b in chosen if self._is_caller(b)]
            chosen = [b for b in chosen if not self._is_caller(b)] + mine  # your own pane last: it ends this server
        if refused and summary is None:
            raise ToolError("PROTECTED", "refused: " + ", ".join(f"{item['title']} ({item['why']})" for item in refused)
                            + ". Protected, not impossible: call delete again with force true to see what it "
                              "takes, then with force and confirm true to do it",
                            {"refused": refused, "override": {"force": True, "confirm": True}})
        if not force:
            chosen = [b for b in chosen if not self._is_caller(b) and not is_screen_berth(b)]
        if arguments.get("confirm") is not True:
            body: dict[str, Any] = {"confirmed": False, "would_delete": [view.row(b, ("id", "title", "state")) for b in chosen]}
            if refused:
                body["skipped"] = refused
            if summary is not None:
                body["scope"] = summary
            return body
        if any(b.state != "parked" for b in chosen):
            self._up()
        deleted, failed = [], []
        db = self._db()
        try:
            for berth in chosen:
                try:
                    delete_berth(db, self.cmux, berth.id, caller_workspace_id=self.caller_workspace or None, force=force)
                    deleted.append({"id": berth.id, "title": berth.title, "folder": view.path(berth.folder_id)})
                except BerthError as exc:
                    failed.append({"id": berth.id, "title": berth.title, "code": exc.code, "message": str(exc)})
        finally:
            db.close()
        body = {"confirmed": True, "deleted": deleted, "days_kept": 30}
        if failed:
            body["failed"] = failed
        if refused:
            body["skipped"] = refused
        return body

    def trash(self, arguments: dict) -> dict[str, Any]:
        from berth import trash

        action = str(arguments.get("action") or "list")
        db = self._db()
        try:
            if action == "list":
                items = trash.items(db)
                keep = ("id", "title", "folder", "deleted_at", "days_left", "session", "held_by")
                return {"total": len(items), "days_kept": trash.TRASH_DAYS,
                        "items": [{key: item[key] for key in keep if item.get(key) not in (None, "")} for item in items[:100]]}
            item = trash.find(db, str(arguments.get("item") or ""))
            if action == "restore":
                restored = trash.restore(db, item["id"])
                return {"restored": self._brief(db, restored["id"]), "was_in": item["folder"] or "Inbox"}
            if action == "forget":
                if arguments.get("confirm") is not True:
                    raise ToolError("BAD_ARGUMENT", "forget deletes for good; pass confirm true",
                                    {"item": {"id": item["id"], "title": item["title"]}})
                return trash.forget(db, item["id"]) | {"title": item["title"]}
            raise ToolError("BAD_ARGUMENT", "action is list, restore, or forget", {"action": action})
        finally:
            db.close()


CLAUDE_WRAPPER = "/Applications/cmux.app/Contents/Resources/bin/cmux-claude-wrapper"


def _starts_screen(command: str) -> bool:
    """True when a shell command would start the Berth screen: berth, or berth screen, alone."""
    words = command.replace(";", " ").split()
    words = [word for word in words if word not in ("exec", "unset") and "=" not in word]
    return bool(words) and words[0].rsplit("/", 1)[-1] == "berth" and (len(words) == 1 or words[1] == "screen")


def claude_model(asked: Any = None) -> str:
    """The model a new Claude starts on: the one asked for; else Claude's default, unless that is unset or Fable."""
    if isinstance(asked, str) and asked.strip():
        return asked.strip()
    try:
        configured = json.loads((Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError, AttributeError):
        configured = None
    if not isinstance(configured, str) or not configured.strip() or "fable" in configured.casefold():
        return "opus"
    return configured.strip()


def spawn_command(agent: str, title: str, *, model: Any = None, command: Any = None, prompt_file: Path | None = None,
                  known_sessions: set[str] | None = None, config_dir: str | None = None) -> str | None:
    """The command a new session's workspace runs. These are the cmux-claude and cmux-grok skills' spawn commands.

    Claude goes through cmux's wrapper (hooks, so cmux agent messages reach it), in color (cmux starts with
    FORCE_COLOR=0), with no model pinned from the parent pane. The prompt is read from a file, so any text is safe.
    """
    import shlex

    if agent == "claude":
        binary = CLAUDE_WRAPPER if Path(CLAUDE_WRAPPER).exists() else "claude"
        # No permission flag: a new session starts in the mode Claude's settings give (permissions.defaultMode,
        # Settings Agent's). A hard-coded --dangerously-skip-permissions put every agent-made session in bypass
        # while the settings said auto (2026-10-31).
        parts = [binary, "--effort", "xhigh", "--model", claude_model(model), "--name", title]
        from berth.configdir import shell_prefix

        text = ("unset FORCE_COLOR NO_COLOR ANTHROPIC_MODEL ANTHROPIC_SMALL_FAST_MODEL; " + shell_prefix(config_dir)
                + "exec " + shlex.join(parts))
        if prompt_file is not None:
            text += f' "$(cat {shlex.quote(str(prompt_file))})"'
        return text
    if agent == "grok":
        return "bash -lc 'export PATH=\"$HOME/.grok/bin:$PATH\"; exec grok --always-approve --no-subagents --reasoning-effort xhigh'"
    if command in (None, ""):
        return None
    text = str(command)
    from berth.place import is_berth_command, split_command

    parts = split_command(text)
    if is_berth_command(parts) or any(Path(part).name == "berth" for part in parts):
        raise ToolError("PROTECTED", "the Berth screen is not started from here")
    for session in known_sessions or ():
        if session and session in parts:
            raise ToolError("SESSION_IN_USE", "that session already has a Berth row: wake it instead", {"session": session})
    return text


def _event_brief(item) -> dict[str, Any]:
    try:
        detail = json.loads(item["detail"] or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(detail, dict):
        return {}
    keep = {key: detail[key] for key in ("title", "path", "reason", "to", "from", "via", "event", "workspace_id")
            if key in detail and detail[key] not in (None, "")}
    return {"detail": keep} if keep else {}


# the stdio server ----------------------------------------------------


def _result(request_id, result) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(api: Api, message: dict) -> dict[str, Any] | None:
    """One JSON-RPC message in, its response out (None for a notification)."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if request_id is None:
        return None
    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        try:
            with contextlib.redirect_stdout(sys.stderr):
                body = api(name, arguments)
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}]})
        except ToolError as exc:
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(exc.payload(), ensure_ascii=False)}],
                                        "isError": True})
        except Exception as exc:  # a bug must not take the server down
            payload = {"error": {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"}}
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": True})
    return _error(request_id, -32601, f"method not found: {method}")


def serve(api: Api | None = None, stdin=None, stdout=None) -> int:
    api = api or Api()
    from berth.health import register

    register(Path(api.db_path).parent, "mcp")  # so status can say which sessions run old code (berth/health.py)
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    for line in source:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            sink.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            sink.flush()
            continue
        if not isinstance(message, dict):
            continue
        response = handle(api, message)
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
    return 0
