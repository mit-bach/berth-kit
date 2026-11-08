"""A shell that starts Grok becomes a Grok row. The two names stay one name.

A workspace whose hook is Claude becomes a Claude row. The Grok session that
used to own that workspace stays parked, with its session id and its recipe.

Grok, Claude, Codex, Antigravity, and Cursor all take ``/rename <name>``
typed into the live session. Claude stores that name as a ``custom-title``
line in the transcript, and the name it generates on its own as an
``ai-title`` line. A parked Claude row has no separate title file that
Claude prefers over that line. Cursor stores the name in ``meta.json``. Codex
stores a manual name on the thread. Antigravity stores it on the conversation
summary.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from berth.cmux import Cmux
from berth.db import Catalog
from berth.hooks import HookRecord, hook_for
from berth.model import Workspace
from berth.timeutil import utc_now

_sent: set[tuple[str, str]] = set()
_renamed: set[tuple[str, str]] = set()
_claude_cache: dict[str, tuple[int, int, str | None, bool]] = {}
_NAME_KEYS = ("name_lock", "name_seen", "name_hold", "name_sent", "name_auto", "name_sent_at")


def grok_sessions_root() -> Path:
    return Path.home() / ".grok" / "sessions"


def session_home(cwd: str) -> Path:
    encoded = quote(cwd, safe="")
    return grok_sessions_root() / encoded


def grok_summary_path(session_id: str, cwd: str) -> Path | None:
    """``summary.json`` lives under the directory Grok started in.

    The pane may have moved since, so the row cwd can name the wrong
    directory. Then the session id is looked up under every directory.
    """
    if not _session_id_ok(session_id):
        return None
    direct = session_home(cwd) / session_id / "summary.json"
    if direct.is_file():
        return direct
    root = grok_sessions_root()
    if not root.is_dir():
        return None
    try:
        homes = list(root.iterdir())
    except OSError:
        return None
    for home in homes:
        candidate = home / session_id / "summary.json"
        if candidate.is_file():
            return candidate
    return None


def grok_title(session_id: str, cwd: str) -> tuple[str | None, bool]:
    """Return the session title and whether a person set it with /rename."""
    path = grok_summary_path(session_id, cwd)
    if path is None:
        return None, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, False
    if not isinstance(data, dict):
        return None, False
    title = data.get("generated_title")
    manual = bool(data.get("title_is_manual"))
    if not isinstance(title, str) or not title.strip():
        return None, manual
    return " ".join(title.split()), manual


def _is_grok_hook(hook: HookRecord) -> bool:
    blob = " ".join(
        part
        for part in (
            hook.executable or "",
            hook.launcher or "",
            " ".join(hook.arguments),
            hook.agent_name,
        )
        if part
    ).casefold()
    return "grok" in blob and hook.session_id is not None


def _load_attachments(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _norm(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


_RENAME_AGENTS = frozenset({"claude", "codex", "antigravity", "cursor"})


def accepts_session_rename(kind: str | None, agent_name: str | None) -> bool:
    """These agents take ``/rename``. A shell or a process does not."""
    if kind == "grok":
        return True
    return kind == "other-agent" and (agent_name or "").casefold() in _RENAME_AGENTS


def attachments_with_lock(
    raw: str | None,
    title: str,
    *,
    seen: str | None = None,
    hold: str | None = None,
    sent: str | None = None,
) -> str:
    """Remember a name Berth asked for.

    ``seen`` is the session title at that moment, so a later different manual
    title is a rename inside the session and the old title is not. ``hold`` is
    the workspace title before Berth renamed it. That stale sidebar must not
    win the lock back. ``sent`` is a ``/rename`` already typed, so the next
    tick does not type it again.
    """
    data = _load_attachments(raw)
    data["name_lock"] = _norm(title)
    if seen is not None:
        data["name_seen"] = _norm(seen)
    else:
        data.pop("name_seen", None)
    if hold:
        data["name_hold"] = _norm(hold)
    else:
        data.pop("name_hold", None)
    if sent:
        import time

        data["name_sent"] = _norm(sent)
        data["name_sent_at"] = time.time()
    else:
        data.pop("name_sent", None)
    return json.dumps(data, sort_keys=True)


RENAME_ON_OPEN = "rename_on_open"


def with_rename_on_open(raw: str | None, title: str | None) -> str:
    """Remember a name to type when the session next opens, or forget it (``None``)."""
    data = _load_attachments(raw)
    if title:
        data[RENAME_ON_OPEN] = _norm(title)
    else:
        data.pop(RENAME_ON_OPEN, None)
    return json.dumps(data, sort_keys=True)


# A prompt that takes typing: the status strip is on screen and no dialog is.
_READY = ("shift+tab", "for shortcuts")
_DIALOG = ("do you trust", "trust this folder", "trust these settings", "yes, i accept", "enter to confirm",
           "no, exit", "no, quit", "make auto mode your default")


def prompt_ready(text: str) -> bool:
    lowered = (text or "").lower()
    return any(mark in lowered for mark in _READY) and not any(mark in lowered for mark in _DIALOG)


def rename_on_open(db: Catalog, cmux: Cmux, workspaces: list[Workspace]) -> list[str]:
    """Type ``/rename`` into a session renamed while it was closed, once its prompt is ready.

    Waits tick by tick while the agent starts or a dialog (folder trust) is
    up. Typed once; the lock's ``name_sent`` keeps sync_names from typing it
    again. (2026-10-28: a sleeping Claude renamed in Berth opened under its
    old name, because Claude did not take the name written into its file.)
    """
    open_ids = {item.id.lower() for item in workspaces}
    done: list[str] = []
    for row in db.conn.execute("SELECT * FROM berth WHERE state = 'live' AND attachments LIKE '%rename_on_open%'"):
        data = _load_attachments(row["attachments"])
        title = _norm(data.get(RENAME_ON_OPEN))
        workspace_id = row["cmux_workspace_id"] or ""
        if not title or workspace_id.lower() not in open_ids:
            continue
        if not accepts_session_rename(row["kind"], row["agent_name"]):
            data.pop(RENAME_ON_OPEN, None)
        else:
            try:
                text = cmux.read_screen(workspace_id, lines=40, scrollback=False)
            except Exception:
                continue
            if not prompt_ready(text):
                continue
            if not send_session_rename(cmux, workspace_id, row["agent_session_id"] or "", title, force=True,
                                       kind=row["kind"], agent=row["agent_name"]):
                continue  # not idle yet, or someone is typing: next tick
            data.pop(RENAME_ON_OPEN, None)
            data["name_lock"] = title
            data["name_sent"] = title
            data["name_sent_at"] = __import__("time").time()
            done.append(row["id"])
        with db.immediate():
            db.update_berth(row["id"], attachments=json.dumps(data, sort_keys=True), updated_at=utc_now())
            if row["id"] in done:
                db.insert_event(row["id"], "rename", {"title": title, "via": "open"})
    return done


DRAFT_TRIES = 3


def _same_text(left: str, right: str) -> bool:
    return " ".join((left or "").split()) == " ".join((right or "").split())


def restore_drafts(db: Catalog, cmux: Cmux, workspaces: list[Workspace]) -> list[str]:
    """Put a draft saved at close back into the prompt box once the session is open again, unsent.

    ``pause_berth`` and ``delete_berth`` save the draft (``attachments.draft``,
    and a ``draft_saved`` event). Here it is pasted, as ⌘V would, when the
    prompt is ready, not busy, and empty, and checked on the next look. A
    rename waiting to be typed goes first. If the box already holds other
    text, or three pastes did not land, nothing is overwritten: the draft is
    kept as ``draft_unrestored`` (the MCP's ``session`` shows it) and logged.
    """
    from berth.activity import assess

    open_ids = {item.id.lower() for item in workspaces}
    done: list[str] = []
    rows = db.conn.execute("SELECT * FROM berth WHERE state = 'live' AND attachments LIKE '%\"draft\"%'").fetchall()
    for row in rows:
        data = _load_attachments(row["attachments"])
        draft = data.get("draft")
        workspace_id = row["cmux_workspace_id"] or ""
        if not isinstance(draft, dict) or data.get(RENAME_ON_OPEN) or workspace_id.lower() not in open_ids:
            continue
        try:
            screen = cmux.read_screen(workspace_id, lines=80)
        except Exception:
            continue
        now = assess(screen, row["kind"] or "", (row["agent_name"] or "").casefold())
        if not now.prompt or now.busy:
            continue  # starting, or working: look again next tick
        tries = int(draft.get("pasted") or 0)
        event = None
        if now.draft is not None:
            if tries or _same_text(now.draft.text, draft.get("text", "")):
                data.pop("draft", None)
                event = ("draft_restored", {"text": draft.get("text", ""), "pastes": tries})
            else:
                data.pop("draft", None)
                data["draft_unrestored"] = draft
                event = ("draft_not_restored", {"why": "the prompt box already held other text", "text": draft.get("text", "")})
        elif tries >= DRAFT_TRIES:
            data.pop("draft", None)
            data["draft_unrestored"] = draft
            event = ("draft_not_restored", {"why": f"{tries} pastes did not land", "text": draft.get("text", "")})
        else:
            try:
                cmux.paste_text(workspace_id, draft.get("text", ""))
            except Exception:
                pass
            draft["pasted"] = tries + 1
            data["draft"] = draft
        with db.immediate():
            db.update_berth(row["id"], attachments=json.dumps(data, sort_keys=True), updated_at=utc_now())
            if event is not None:
                db.insert_event(row["id"], event[0], event[1])
        if event is not None and event[0] == "draft_restored":
            done.append(row["id"])
    return done


def claude_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _session_id_ok(session_id: str) -> bool:
    if not session_id or session_id in {".", ".."}:
        return False
    return "/" not in session_id and "\\" not in session_id and "\x00" not in session_id


def _claude_project_name(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd or "")


def claude_transcript(session_id: str, cwd: str) -> Path | None:
    """The jsonl Claude appends. ``custom-title`` in that file is the session name."""
    if not _session_id_ok(session_id):
        return None
    from berth.configdir import extra_roots, unique

    name = f"{session_id}.jsonl"
    # ~/.claude first, then every other Claude config folder (one per account; berth/configdir.py).
    roots = unique([claude_projects_root()] + [root / "projects" for root in extra_roots()])
    for root in roots:
        direct = root / _claude_project_name(cwd) / name
        if direct.is_file():
            return direct
    for root in roots:
        if not root.is_dir():
            continue
        try:
            projects = list(root.iterdir())
        except OSError:
            continue
        for project in projects:
            if not project.is_dir():
                continue
            candidate = project / name
            if candidate.is_file():
                return candidate
    return None


def claude_title(session_id: str, cwd: str) -> tuple[str | None, bool]:
    """The session name in the transcript, and whether a person set it.

    The last ``custom-title`` is manual: Claude wrote it for ``/rename``.
    Without one, the last ``ai-title`` is the name Claude generated, and it
    is not manual. Reading the transcript does not rewrite it. There is no
    sidecar Claude prefers over these lines.
    """
    path = claude_transcript(session_id, cwd)
    if path is None:
        return None, False
    try:
        st = path.stat()
    except OSError:
        return None, False
    key = str(path)
    cached = _claude_cache.get(key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2], cached[3]
    title, manual = _last_claude_title(path)
    _claude_cache[key] = (st.st_mtime_ns, st.st_size, title, manual)
    return title, manual


def _last_claude_title(path: Path) -> tuple[str | None, bool]:
    custom = None
    generated = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "custom-title" not in line and "ai-title" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") == "custom-title":
                    raw = obj.get("customTitle")
                    if isinstance(raw, str) and raw.strip():
                        custom = _norm(raw)
                elif obj.get("type") == "ai-title":
                    raw = obj.get("aiTitle")
                    if isinstance(raw, str) and raw.strip():
                        generated = _norm(raw)
    except OSError:
        return None, False
    if custom:
        return custom, True
    return generated, False


def write_claude_title(session_id: str, cwd: str, title: str) -> bool:
    """Write Claude's fallback sidecar. The transcript is not touched.

    Claude reads ``<session>/custom-title.json`` only when the transcript has
    no ``custom-title`` line. A line that is already there still wins, so a
    parked rename also waits for ``/rename`` once the session is live.
    """
    transcript = claude_transcript(session_id, cwd)
    if transcript is None or not transcript.is_file() or not _session_id_ok(session_id):
        return False
    dest = transcript.parent / session_id / "custom-title.json"
    try:
        if not dest.resolve().is_relative_to(transcript.parent.resolve()):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({"customTitle": title}) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def cursor_chats_root() -> Path:
    return Path.home() / ".cursor" / "chats"


def codex_state_path() -> Path:
    return Path.home() / ".codex" / "state_5.sqlite"


def codex_lock_dir() -> Path:
    return Path.home() / ".codex" / "thread-writer-locks"


def agy_summary_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "conversation_summaries.db"


def agy_presence_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "presence"


def _cursor_metas() -> list[tuple[str, dict, Path]]:
    root = cursor_chats_root()
    if not root.is_dir():
        return []
    found: list[tuple[str, dict, Path]] = []
    try:
        projects = list(root.iterdir())
    except OSError:
        return []
    for project in projects:
        if not project.is_dir():
            continue
        try:
            children = list(project.iterdir())
        except OSError:
            continue
        for child in children:
            meta = child / "meta.json"
            if not meta.is_file() or not _session_id_ok(child.name):
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                found.append((child.name, data, meta))
    return found


def _cursor_meta(session_id: str) -> tuple[dict, Path] | None:
    if not _session_id_ok(session_id):
        return None
    for ident, data, path in _cursor_metas():
        if ident == session_id:
            return data, path
    return None


def cursor_title(session_id: str, cwd: str) -> tuple[str | None, bool]:
    """The name Cursor wrote for ``/rename`` or for its own first title.

    Both land in ``meta.json`` ``title``. A non-empty title is the session
    name. ``cwd`` is unused; the chat id is the key.
    """
    del cwd
    found = _cursor_meta(session_id)
    if found is None:
        return None, False
    raw = found[0].get("title")
    if not isinstance(raw, str) or not raw.strip():
        return None, False
    return _norm(raw), True


def write_cursor_title(session_id: str, cwd: str, title: str) -> bool:
    """Store a parked Cursor chat name. The transcript is not touched."""
    del cwd
    found = _cursor_meta(session_id)
    if found is None:
        return False
    data, path = found
    data["title"] = title
    try:
        path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _sql_rows(path: Path, query: str, params: tuple = ()) -> list[tuple] | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return list(conn.execute(query, params))
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def codex_title(session_id: str, cwd: str) -> tuple[str | None, bool]:
    """``threads.name`` is a ``/rename``. ``threads.title`` is generated.

    A generated title does not break a name Berth just set. ``cwd`` is unused.
    """
    del cwd
    if not _session_id_ok(session_id):
        return None, False
    rows = _sql_rows(
        codex_state_path(),
        "SELECT name, title FROM threads WHERE id = ?",
        (session_id,),
    )
    if not rows:
        return None, False
    name, title = rows[0]
    if isinstance(name, str) and name.strip():
        return _norm(name), True
    if isinstance(title, str) and title.strip():
        return _norm(title), False
    return None, False


def write_codex_title(session_id: str, cwd: str, title: str) -> bool:
    """Store a parked Codex thread name. A missing thread is left alone."""
    del cwd
    path = codex_state_path()
    if not path.is_file() or not _session_id_ok(session_id):
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            cursor = conn.execute(
                "UPDATE threads SET name = ? WHERE id = ?",
                (title, session_id),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _file_uri_path(value: str) -> str:
    text = value.strip()
    if text.startswith("file://"):
        return unquote(urlparse(text).path or "")
    return text


def agy_title(session_id: str, cwd: str) -> tuple[str | None, bool]:
    """The conversation summary title. Antigravity writes ``/rename`` there."""
    del cwd
    if not _session_id_ok(session_id):
        return None, False
    rows = _sql_rows(
        agy_summary_path(),
        "SELECT title FROM conversation_summaries WHERE conversation_id = ?",
        (session_id,),
    )
    if not rows:
        return None, False
    raw = rows[0][0]
    if not isinstance(raw, str) or not raw.strip():
        return None, False
    return _norm(raw), True


def write_agy_title(session_id: str, cwd: str, title: str) -> bool:
    """Store a parked Antigravity conversation name. A missing row is left alone."""
    del cwd
    path = agy_summary_path()
    if not path.is_file() or not _session_id_ok(session_id):
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            cursor = conn.execute(
                "UPDATE conversation_summaries SET title = ? WHERE conversation_id = ?",
                (title, session_id),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def current_session_title(kind: str | None, agent_name: str | None, session_id: str | None, cwd: str) -> tuple[str | None, bool]:
    if not session_id or not accepts_session_rename(kind, agent_name):
        return None, False
    if kind == "grok":
        return grok_title(session_id, cwd)
    name = (agent_name or "").casefold()
    if name == "cursor":
        return cursor_title(session_id, cwd)
    if name == "codex":
        return codex_title(session_id, cwd)
    if name == "antigravity":
        return agy_title(session_id, cwd)
    return claude_title(session_id, cwd)


def ready_to_type(cmux: Cmux, workspace_id: str, kind: str | None, agent: str | None) -> bool:
    """True when the session is idle: its prompt on screen and empty, no turn running, no question or dialog.

    Claude's status strip ("shift+tab") is on screen mid-turn too, so ``prompt_ready`` alone is not idle
    (2026-10-31: names are fixed only in an idle pane; berth/send.py reads the same way).
    """
    from berth.activity import assess
    from berth.send import situation

    try:
        screen = cmux.read_screen(workspace_id, lines=120)
    except Exception:
        return False
    activity = assess(screen, kind or "", (agent or "").casefold())
    draft = activity.draft
    typing = draft is not None and bool((draft.text or "").strip()) if hasattr(draft, "text") else bool(draft)
    return situation(activity) == "ready" and not typing  # never over someone's draft


def send_session_rename(cmux: Cmux, workspace_id: str, session_id: str, title: str, *, force: bool = False,
                        kind: str | None = None, agent: str | None = None) -> bool:
    """Type one ``/rename`` line, only into an idle pane. True when it was typed; False means try again later.

    ``force`` is an explicit rename from Berth. The following tick still sees
    the name in ``_sent`` and does not type it a second time. Codex, Cursor,
    and Antigravity use the same ``/rename`` line as Grok and Claude. With
    ``kind``, the pane must be idle (``ready_to_type``); cmux itself refuses to
    type over someone's typing, and that is a False too, never an error.
    """
    from berth.errors import BerthError

    safe = _norm(title)
    marker = session_id or workspace_id
    if not safe or safe == "--auto" or not marker or not workspace_id:
        return False
    key = (marker, safe)
    if key in _sent and not force:
        return False
    readable = kind == "grok" or (agent or "").casefold() in ("claude", "grok")  # boxes Berth can read
    if kind is not None and readable and not ready_to_type(cmux, workspace_id, kind, agent):
        return False
    try:
        cmux.send_text(workspace_id, f"/rename {safe}\n")
    except BerthError:
        return False
    _sent.add(key)
    return True


def note_workspace_renamed(workspace_id: str, title: str) -> None:
    safe = _norm(title)
    if workspace_id and safe:
        _renamed.add((workspace_id.lower(), safe))


def write_session_title(session_id: str, cwd: str, title: str) -> bool:
    """Store a parked session's name. A live session is renamed with ``/rename``."""
    path = grok_summary_path(session_id, cwd)
    if path is None:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    data["generated_title"] = title
    data["title_is_manual"] = True
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def promote_shells(db: Catalog, hooks: dict[str, HookRecord]) -> list[str]:
    """The hook on a live workspace is the session that pane is running.

    A shell becomes a grok row. A grok row whose pane started a different
    session takes that session. A session id stays on one row: the pane whose
    hook still names it. The catalog title is left for ``sync_names``.
    """
    from berth.hooks import load_hook_records
    from berth.place import grok_resume_argv

    records = load_hook_records()
    live = list(db.conn.execute("SELECT * FROM berth WHERE state = 'live'"))
    hooked: list[tuple] = []
    for row in live:
        if row["kind"] == "berth":
            continue
        hook = hook_for(hooks, row["cmux_workspace_id"])
        if hook is None or not _is_grok_hook(hook):
            continue
        hook = _with_real_session(hook, records)
        if not _session_ready(hook) or ended(hook):
            continue
        if row["kind"] == "grok" and row["agent_session_id"] == hook.session_id:
            continue
        hooked.append((row, hook))
    winners: dict[str, tuple] = {}
    for row, hook in hooked:
        session_id = hook.session_id or ""
        current = winners.get(session_id)
        if current is None or (hook.updated_at or 0) >= (current[1].updated_at or 0):
            winners[session_id] = (row, hook)
    changed: list[str] = []
    stamp = utc_now()
    for session_id, (row, hook) in winners.items():
        holders = [
            item
            for item in live
            if item["agent_session_id"] == session_id and item["id"] != row["id"]
        ]
        if any(_hook_still(hooks, item, session_id) for item in holders):
            continue
        parked = db.conn.execute(
            """
            SELECT * FROM berth
            WHERE agent_session_id = ? AND id != ? AND state = 'parked'
            ORDER BY created_at, id
            """,
            (session_id, row["id"]),
        ).fetchone()
        if parked is not None and not row["protect"]:
            _reattach_parked(db, parked, row, hook, stamp)
            changed.append(parked["id"])
            continue
        argv = grok_resume_argv(hook)
        with db.immediate():
            for item in holders:
                if item["id"] in {winner[0]["id"] for winner in winners.values()}:
                    continue
                db.update_berth(
                    item["id"],
                    agent_session_id=None,
                    updated_at=stamp,
                )
            db.update_berth(
                row["id"],
                kind="grok",
                agent_name="grok",
                agent_session_id=session_id,
                cmux_surface_id=hook.surface_id,
                cmux_pid=hook.pid,
                note="",
                recipe_argv=json.dumps(argv),
                recipe_display=" ".join(argv),
                updated_at=stamp,
            )
            db.insert_event(row["id"], "promote", {"session_id": session_id})
        changed.append(row["id"])
    return changed


def ended(hook: HookRecord) -> bool:
    """The hook's process said SessionEnd and is gone, so it is not what the pane runs.

    A headless ``grok -p`` or ``claude -p`` started from a pane's shell (an
    agent running a check) writes hooks under that pane's workspace. Its
    session must not replace the session the pane is really showing.
    """
    from berth.place import pid_alive

    if (hook.hook_event or "").casefold() != "sessionend":
        return False
    return not hook.pid or not pid_alive(hook.pid)


def _session_ready(hook: HookRecord) -> bool:
    if not hook.session_id:
        return False
    return (session_home(hook.cwd or "") / hook.session_id).is_dir()


def _with_real_session(hook: HookRecord, records: list[HookRecord]) -> HookRecord:
    """A hook id with no session directory loses to the same process's real session."""
    if _session_ready(hook) or not hook.pid:
        return hook
    alts = [
        item
        for item in records
        if item.pid == hook.pid and item.agent_name == "grok" and _session_ready(item)
    ]
    if not alts:
        return hook
    return max(alts, key=lambda item: item.updated_at or 0)


def _reattach_parked(db: Catalog, parked, placeholder, hook: HookRecord, stamp: str) -> None:
    """The open pane is a session that already has a parked row. Keep that row."""
    from berth.place import grok_resume_argv

    from berth import machine

    argv = grok_resume_argv(hook)
    with db.immediate():
        machine.delete(db, placeholder["id"], reason="merged", detail={"into": parked["id"]})
        machine.transition(
            db,
            parked["id"],
            "relink",
            workspace_id=placeholder["cmux_workspace_id"],
            fields={
                "kind": "grok",
                "agent_name": "grok",
                "agent_session_id": hook.session_id,
                "cmux_surface_id": hook.surface_id,
                "cmux_pid": hook.pid,
                "note": "",
                "recipe_argv": json.dumps(argv),
                "recipe_display": " ".join(argv),
                "played_at": stamp,
            },
            log_kind="promote",
            detail={"session_id": hook.session_id, "via": "reattach"},
            now=stamp,
        )


def _hook_still(hooks: dict[str, HookRecord], row, session_id: str) -> bool:
    hook = hook_for(hooks, row["cmux_workspace_id"])
    return hook is not None and _is_grok_hook(hook) and hook.session_id == session_id


def _taken_session_ids(db: Catalog) -> set[str]:
    rows = db.conn.execute(
        """
        SELECT agent_session_id FROM berth
        WHERE agent_session_id IS NOT NULL AND agent_session_id != ''
        """
    )
    return {row["agent_session_id"] for row in rows}


def _cwd_same(left: str, right: str) -> bool:
    from berth.place import normalize_cwd

    return normalize_cwd(left) == normalize_cwd(right)


def _cursor_for_cwd(cwd: str, taken: set[str]) -> str | None:
    matches: list[tuple[int, str]] = []
    for ident, data, _path in _cursor_metas():
        if ident in taken:
            continue
        meta_cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
        if not meta_cwd or not _cwd_same(cwd, meta_cwd):
            continue
        updated = data.get("updatedAtMs")
        stamp = updated if isinstance(updated, int) else 0
        matches.append((stamp, ident))
    if not matches:
        return None
    matches.sort()
    return matches[-1][1]


def _codex_locks(taken: set[str], known: set[str]) -> list[str]:
    directory = codex_lock_dir()
    if not directory.is_dir():
        return []
    found: list[str] = []
    try:
        names = list(directory.iterdir())
    except OSError:
        return []
    for path in names:
        if path.name.startswith(".") or not path.name.endswith(".lock"):
            continue
        ident = path.name[: -len(".lock")]
        if not _session_id_ok(ident) or ident in taken or ident in known:
            continue
        found.append(ident)
    return found


def _codex_for_cwd(cwd: str, taken: set[str], waiting: int) -> str | None:
    rows = _sql_rows(
        codex_state_path(),
        "SELECT id, cwd, updated_at FROM threads WHERE archived = 0",
    ) or []
    matches: list[tuple[int, str]] = []
    known: set[str] = set()
    for ident, thread_cwd, updated in rows:
        if not isinstance(ident, str):
            continue
        known.add(ident)
        if ident in taken or not isinstance(thread_cwd, str) or not _cwd_same(cwd, thread_cwd):
            continue
        stamp = updated if isinstance(updated, int) else 0
        matches.append((stamp, ident))
    if matches:
        matches.sort()
        return matches[-1][1]
    locks = _codex_locks(taken, known)
    if waiting == 1 and len(locks) == 1:
        return locks[0]
    return None


def _agy_paths(raw: object) -> list[str]:
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [_file_uri_path(item) for item in raw if isinstance(item, str)]


def _agy_for_cwd(cwd: str, taken: set[str], waiting: int) -> str | None:
    rows = _sql_rows(
        agy_summary_path(),
        """
        SELECT conversation_id, workspace_uris, last_modified_time
        FROM conversation_summaries
        """,
    ) or []
    matches: list[tuple[str, str]] = []
    for ident, uris, modified in rows:
        if not isinstance(ident, str) or ident in taken or not _session_id_ok(ident):
            continue
        if any(_cwd_same(cwd, path) for path in _agy_paths(uris)):
            matches.append((str(modified or ""), ident))
    if matches:
        present = agy_presence_dir()
        locked = [item for item in matches if (present / f"{item[1]}.lock").is_file()]
        pool = locked or matches
        pool.sort()
        return pool[-1][1]
    directory = agy_presence_dir()
    locks: list[str] = []
    if directory.is_dir():
        try:
            names = list(directory.iterdir())
        except OSError:
            names = []
        for path in names:
            if path.name.startswith(".") or not path.name.endswith(".lock"):
                continue
            ident = path.name[: -len(".lock")]
            if _session_id_ok(ident) and ident not in taken:
                locks.append(ident)
    if waiting == 1 and len(locks) == 1:
        return locks[0]
    return None


def _attach_session(db: Catalog, row, session_id: str) -> None:
    from berth.place import resume_argv

    try:
        argv = json.loads(row["recipe_argv"] or "[]")
    except json.JSONDecodeError:
        argv = []
    program = ""
    if argv and isinstance(argv[0], str) and argv[0] and not argv[0].startswith("-"):
        program = argv[0]
    elif row["recipe_display"]:
        program = str(row["recipe_display"]).split()[0]
    if not program:
        program = row["agent_name"] or "agent"
    new_argv = resume_argv(program, row["agent_name"], session_id)
    with db.immediate():
        db.update_berth(
            row["id"],
            agent_session_id=session_id,
            recipe_argv=json.dumps(new_argv),
            recipe_display=" ".join(new_argv),
            updated_at=utc_now(),
        )


def discover_agent_sessions(db: Catalog) -> list[str]:
    """Fill a missing session id from the agent's own files.

    Cursor takes the newest chat in that directory. Codex takes the thread in
    that directory, or the one open lock when only one Codex row is waiting.
    Antigravity takes the conversation whose workspace is that directory.
    An id already stored on any row is not reused.
    """
    rows = list(
        db.conn.execute(
            """
            SELECT * FROM berth
            WHERE state = 'live'
              AND kind = 'other-agent'
              AND agent_name IN ('codex', 'antigravity', 'cursor')
              AND (agent_session_id IS NULL OR agent_session_id = '')
            """
        )
    )
    if not rows:
        return []
    taken = _taken_session_ids(db)
    waiting = {"codex": 0, "antigravity": 0, "cursor": 0}
    for row in rows:
        name = (row["agent_name"] or "").casefold()
        if name in waiting:
            waiting[name] += 1
    rows.sort(key=lambda item: item["updated_at"] or "", reverse=True)
    counts = dict(waiting)
    changed: list[str] = []
    for row in rows:
        name = (row["agent_name"] or "").casefold()
        cwd = row["cwd"] or ""
        if name == "cursor":
            session_id = _cursor_for_cwd(cwd, taken)
        elif name == "codex":
            session_id = _codex_for_cwd(cwd, taken, counts["codex"])
        elif name == "antigravity":
            session_id = _agy_for_cwd(cwd, taken, counts["antigravity"])
        else:
            session_id = None
        if not session_id:
            continue
        _attach_session(db, row, session_id)
        taken.add(session_id)
        changed.append(row["id"])
    return changed


def sync_names(db: Catalog, cmux: Cmux, workspaces: list[Workspace]) -> None:
    """One string for the catalog, the workspace, and the agent session.

    Grok, Claude, Codex, Antigravity, and Cursor are included. A name Berth
    just set is ``name_lock``. A stale session title does not replace it. A
    workspace title that the operator changed, or a new manual session title,
    becomes the name and the other side is brought along. ``/rename`` for a
    given session and name is typed once.
    """
    by_id = {item.id.lower(): item for item in workspaces}
    rows = db.conn.execute(
        """
        SELECT id, title, cwd, kind, agent_name, agent_session_id,
               cmux_workspace_id, attachments
        FROM berth
        WHERE state = 'live'
          AND (
                (kind = 'grok' AND agent_session_id IS NOT NULL AND agent_session_id != '')
                OR (
                    kind = 'other-agent'
                    AND lower(agent_name) IN ('claude', 'codex', 'antigravity', 'cursor')
                )
              )
        """
    )
    for row in rows:
        workspace = by_id.get((row["cmux_workspace_id"] or "").lower())
        if workspace is None:
            continue
        cwd = row["cwd"] or workspace.cwd
        session_name, manual = current_session_title(
            row["kind"], row["agent_name"], row["agent_session_id"], cwd
        )
        sidebar = _norm(workspace.title)
        catalog_title = row["title"] or ""
        data = _load_attachments(row["attachments"])
        if data.get(RENAME_ON_OPEN):
            continue  # rename_on_open types it once the prompt is ready
        lock = _norm(data.get("name_lock")) or None
        if lock:
            _sync_locked(
                db, cmux, row, workspace, data, lock, session_name, manual, sidebar, catalog_title
            )
            continue
        if session_name and sidebar == session_name:
            if catalog_title != session_name:
                _set_title(db, row["id"], session_name)
            continue
        if workspace.custom and sidebar and sidebar != catalog_title:
            _adopt_sidebar(db, cmux, row, workspace.id, sidebar, session_name)
            continue
        if session_name and session_name != sidebar:
            if not manual and not _auto_may_replace(row, workspace, data, catalog_title, cwd):
                continue
            _take_session(db, cmux, row, workspace.id, session_name, auto=not manual)


def _auto_may_replace(row, workspace, data: dict, catalog_title: str, cwd: str) -> bool:
    """A generated session title does not replace a name someone chose.

    It fills a sidebar cmux named on its own, a directory title, or a name
    that was itself the previous generated title. A custom workspace name is
    kept until the session is renamed with ``/rename``.
    """
    del row
    if not workspace.custom or _path_title(catalog_title, cwd):
        return True
    auto = _norm(data.get("name_auto"))
    return bool(auto) and auto == _norm(catalog_title)


def _sync_locked(db, cmux, row, workspace, data, lock, session_name, manual, sidebar, catalog_title) -> None:
    if session_name == lock:
        _clear_lock(db, row)
        if sidebar != lock:
            _rename_workspace(cmux, workspace.id, lock)
        if catalog_title != lock:
            _set_title(db, row["id"], lock)
        return
    hold = _norm(data.get("name_hold"))
    if hold and sidebar == hold:
        _push_locked(db, cmux, row, workspace.id, lock, sidebar, catalog_title)
        return
    if workspace.custom and sidebar and sidebar != lock:
        _adopt_sidebar(db, cmux, row, workspace.id, sidebar, session_name)
        return
    seen = _norm(data.get("name_seen")) if "name_seen" in data else None
    if manual and session_name and seen is not None and session_name != seen and session_name != lock:
        _take_session(db, cmux, row, workspace.id, session_name)
        return
    _push_locked(db, cmux, row, workspace.id, lock, sidebar, catalog_title)


def _set_title(db: Catalog, berth_id: str, title: str) -> None:
    with db.immediate():
        db.update_berth(berth_id, title=title, updated_at=utc_now())


def _clear_lock(db: Catalog, row) -> None:
    data = _load_attachments(row["attachments"])
    if not any(key in data for key in _NAME_KEYS):
        return
    for key in _NAME_KEYS:
        data.pop(key, None)
    with db.immediate():
        db.update_berth(
            row["id"],
            attachments=json.dumps(data, sort_keys=True),
            updated_at=utc_now(),
        )


def _adopt_sidebar(db, cmux, row, workspace_id: str, title: str, session_name: str | None) -> None:
    """The workspace was renamed. The session and the catalog take that name."""
    sent = None
    if session_name != title and accepts_session_rename(row["kind"], row["agent_name"]):
        if send_session_rename(cmux, workspace_id, row["agent_session_id"], title, kind=row["kind"],
                               agent=row["agent_name"]):
            sent = title  # else the lock stays unsent, and _push_locked types it once the pane is idle
    fields = {
        "attachments": attachments_with_lock(
            row["attachments"], title, seen=session_name, sent=sent
        ),
        "updated_at": utc_now(),
    }
    if row["title"] != title:
        fields["title"] = title
    with db.immediate():
        db.update_berth(row["id"], **fields)


def _take_session(db, cmux, row, workspace_id: str, title: str, *, auto: bool = False) -> None:
    """The session was renamed. The workspace and the catalog take that name.

    ``auto`` is a generated title. It is remembered as ``name_auto`` so the
    next generated title may replace it.
    """
    _rename_workspace(cmux, workspace_id, title)
    data = _load_attachments(row["attachments"])
    before = dict(data)
    for key in _NAME_KEYS:
        data.pop(key, None)
    if auto:
        data["name_auto"] = _norm(title)
    fields: dict = {}
    if data != before:
        fields["attachments"] = json.dumps(data, sort_keys=True)
    if row["title"] != title:
        fields["title"] = title
    if not fields:
        return
    fields["updated_at"] = utc_now()
    with db.immediate():
        db.update_berth(row["id"], **fields)


_STUCK_AFTER = 45.0  # seconds a typed /rename may take before Berth looks at the pane again
_stuck_checked: dict[str, float] = {}


def _finish_stuck_rename(cmux, row, workspace_id: str, lock: str, data: dict) -> str | None:
    """A ``/rename`` Berth typed that never took (2026-10-31, Travel Agent).

    It was typed while the session was starting, and its Enter was lost: the
    line sat in the prompt box and ``name_sent`` said it was done. Once the
    session is idle: our own ``/rename <name>`` alone in the box is submitted
    (``"submitted"``); an empty box means it never arrived, so it is typed
    again (``"retype"``). Anything else in the box is someone's and is left.
    """
    import time

    from berth.activity import assess

    sent_at = data.get("name_sent_at")
    now = time.time()
    if not isinstance(sent_at, (int, float)):
        return "stamp"  # sent before Berth kept the time: count from now
    if now - sent_at < _STUCK_AFTER:
        return None
    key = (row["id"] or "")
    if now - _stuck_checked.get(key, 0.0) < _STUCK_AFTER:
        return None
    _stuck_checked[key] = now
    try:
        screen = cmux.read_screen(workspace_id, lines=120)
    except Exception:
        return None
    activity = assess(screen, row["kind"] or "", (row["agent_name"] or "").casefold())
    if activity.turn or activity.asking or not activity.prompt:
        return None
    draft = " ".join(((activity.draft.text if activity.draft is not None else "") or "").split())
    if draft == f"/rename {lock}":
        try:
            cmux.send_key(workspace_id, "enter")
        except Exception:
            return None
        return "submitted"
    if not draft:
        return "retype"
    return None


def _push_locked(db, cmux, row, workspace_id, lock, sidebar, catalog_title) -> None:
    """Keep the name Berth set. Type ``/rename`` only if this tick has not."""
    import time

    data = _load_attachments(row["attachments"])
    if sidebar != lock:
        _rename_workspace(cmux, workspace_id, lock)
    changed = False
    event = None  # written with the row, in one transaction (2026-10-31: a second one met "database is locked")
    if sidebar == lock and "name_hold" in data:
        data.pop("name_hold", None)
        changed = True
    if _norm(data.get("name_sent")) == lock and accepts_session_rename(row["kind"], row["agent_name"]):
        fix = _finish_stuck_rename(cmux, row, workspace_id, lock, data)
        if fix == "retype":
            data.pop("name_sent", None)
            _sent.discard((row["agent_session_id"] or workspace_id, lock))
            changed = True
        elif fix in ("submitted", "stamp"):
            data["name_sent_at"] = time.time()
            changed = True
            if fix == "submitted":
                event = {"title": lock, "via": "submitted a /rename left in the box"}
    if _norm(data.get("name_sent")) != lock and send_session_rename(
            cmux, workspace_id, row["agent_session_id"], lock, kind=row["kind"], agent=row["agent_name"]):
        data["name_sent"] = lock  # only once it was typed: a busy pane is tried again next tick
        data["name_sent_at"] = time.time()
        changed = True
    fields: dict = {}
    if changed:
        fields["attachments"] = json.dumps(data, sort_keys=True)
    if catalog_title != lock:
        fields["title"] = lock
    if not fields:
        return
    fields["updated_at"] = utc_now()
    with db.immediate():
        db.update_berth(row["id"], **fields)
        if event is not None:
            db.insert_event(row["id"], "rename", event)


def _rename_workspace(cmux: Cmux, workspace_id: str, title: str) -> None:
    safe = _norm(title)
    if not safe or not workspace_id:
        return
    key = (workspace_id.lower(), safe)
    if key in _renamed:
        return
    cmux.rename_workspace(workspace_id, safe)
    _renamed.add(key)


def _become_other(db: Catalog, row, hook: HookRecord) -> None:
    """A shell with no session id is the agent that is now in the pane."""
    from berth.place import normalize_cwd, other_agent_resume_argv, version_from_executable

    argv = other_agent_resume_argv(hook)
    with db.immediate():
        db.update_berth(
            row["id"],
            kind="other-agent",
            agent_name=hook.agent_name,
            agent_session_id=hook.session_id,
            agent_version=version_from_executable(hook.executable),
            cmux_surface_id=hook.surface_id,
            cmux_pid=hook.pid,
            cwd=normalize_cwd(hook.cwd or row["cwd"]),
            note="",
            recipe_argv=json.dumps(argv),
            recipe_display=" ".join(argv),
            updated_at=utc_now(),
        )
        db.insert_event(
            row["id"],
            "promote",
            {"session_id": hook.session_id, "agent": hook.agent_name},
        )


def retarget_other_agents(
    db: Catalog,
    cmux: Cmux,
    hooks: dict[str, HookRecord],
    workspaces: list,
) -> list[str]:
    """The pane is Claude. The catalog row that still says Grok gives the pane up.

    The previous session is parked. Its session id and its recipe stay.
    Nothing is closed. A shell with no session id becomes the new agent in place.
    """
    from berth.adopt import _safe_groups, _safe_surfaces, adopt_workspace
    from berth.state import park_workspace_gone

    by_id = {item.id.lower(): item for item in workspaces}
    changed: list[str] = []
    groups = None
    rows = list(db.conn.execute("SELECT * FROM berth WHERE state = 'live'"))
    for row in rows:
        if row["kind"] == "berth":
            continue
        hook = hook_for(hooks, row["cmux_workspace_id"])
        if hook is None or not hook.session_id or hook.agent_name == "grok" or ended(hook):
            continue
        if hook.agent_name == "claude" and _claude_tracks():
            # Claude's own session records win; berth.claude_sync follows them.
            continue
        if row["agent_name"] == hook.agent_name and row["agent_session_id"] == hook.session_id:
            continue
        workspace = by_id.get((row["cmux_workspace_id"] or "").lower())
        if workspace is None:
            continue
        current = db.berth_by_workspace(workspace.id)
        if current is None or current["id"] != row["id"] or current["state"] != "live":
            continue
        if current["agent_name"] == hook.agent_name and current["agent_session_id"] == hook.session_id:
            continue
        if not current["agent_session_id"]:
            _become_other(db, current, hook)
            changed.append(current["id"])
            continue
        workspace_id = current["cmux_workspace_id"]
        protected = bool(current["protect"])
        if groups is None:
            groups = _safe_groups(cmux)
        surfaces = _safe_surfaces(cmux, workspace.id)
        # One transaction: a failed adopt leaves the old row live on the pane.
        with db.immediate():
            park_workspace_gone(db, current["id"], workspace_id, utc_now(), via="replaced")
            record = adopt_workspace(
                db,
                workspace,
                hook=hook,
                surfaces=surfaces,
                groups=groups,
                cmux=cmux,
            )
        if protected and record["id"] != current["id"] and not record["protect"]:
            with db.immediate():
                db.update_berth(record["id"], protect=1, updated_at=utc_now())
        changed.append(record["id"])
    return changed


def _claude_tracks() -> bool:
    from berth import claude

    return claude.sessions_dir().is_dir()


def refresh_identity(db: Catalog, cmux: Cmux, hooks: dict[str, HookRecord]) -> None:
    promote_shells(db, hooks)
    if not cmux.socket_up():
        return
    try:
        workspaces = cmux.list_workspaces()
    except Exception:
        return
    promote_foreground_agents(db, cmux, workspaces)
    from berth import claude_sync

    claude_sync.follow(db, cmux, workspaces)
    retarget_other_agents(db, cmux, hooks, workspaces)
    discover_agent_sessions(db)
    rename_on_open(db, cmux, workspaces)
    restore_drafts(db, cmux, workspaces)
    sync_names(db, cmux, workspaces)


def _agent_program(pid: int, comm: str) -> str:
    """The binary to resume. A `node` process keeps the path that launched it."""
    if comm.casefold() not in {"node", "agent", "python", "python3"}:
        return comm
    command = _process_command(pid)
    first = command.split()[0] if command else ""
    if first.startswith("/") or first in {"agent", "agy", "codex", "cursor-agent"}:
        return first
    if comm.casefold() == "node" and "cursor-agent" in command.casefold():
        return "agent"
    return comm


def _process_command(pid: int) -> str:
    from berth import proc

    return proc.command(pid)  # one table a second, not a ps per pid (berth/proc.py)


def _path_title(title: str, cwd: str) -> bool:
    text = (title or "").strip()
    if not text or text.startswith("~") or text.startswith("/"):
        return True
    base = os.path.basename((cwd or "").rstrip("/"))
    return bool(base) and text == base


def promote_foreground_agents(db: Catalog, cmux: Cmux, workspaces: list) -> list[str]:
    """A shell whose pane is Codex, Antigravity, or Cursor becomes that agent.

    cmux already names the foreground process. The row takes that kind and,
    when its title is still the directory, the sidebar name. No resume command
    is stored until a hook has the session id. Nothing is closed.
    """
    from berth.place import agents_in_top

    reader = getattr(cmux, "top_processes", None)
    if reader is None:
        return []
    try:
        found = agents_in_top(reader(), workspaces, command_fn=_process_command)
    except Exception:
        return []
    if not found:
        return []
    by_id = {item.id.lower(): item for item in workspaces}
    changed: list[str] = []
    rows = list(
        db.conn.execute(
            """
            SELECT * FROM berth
            WHERE state = 'live'
              AND (kind = 'shell' OR (kind = 'other-agent' AND agent_name IN ('codex', 'antigravity', 'cursor')))
            """
        )
    )
    for row in rows:
        workspace_id = (row["cmux_workspace_id"] or "").lower()
        hit = found.get(workspace_id)
        if hit is None:
            continue
        agent, pid, comm = hit
        workspace = by_id.get(workspace_id)
        title = row["title"] or ""
        sidebar = workspace.title if workspace is not None else ""
        program = _agent_program(pid, comm)
        fields: dict = {"updated_at": utc_now()}
        if row["kind"] == "shell" or row["recipe_display"] in {"node", "python", "python3"}:
            fields.update(
                {
                    "kind": "other-agent",
                    "agent_name": agent,
                    "cmux_pid": pid,
                    "recipe_argv": json.dumps([program]),
                    "recipe_display": program,
                }
            )
        # A directory title takes the sidebar name once. A later workspace
        # rename is left for sync_names, which also renames the agent.
        # A custom name copied here is locked so the agent's old title does
        # not immediately replace it.
        if sidebar and sidebar != title and _path_title(title, row["cwd"] or ""):
            fields["title"] = sidebar
            if workspace is not None and workspace.custom and accepts_session_rename("other-agent", agent):
                fields["attachments"] = attachments_with_lock(row["attachments"], sidebar)
        if len(fields) == 1:
            continue
        with db.immediate():
            db.update_berth(row["id"], **fields)
            if row["kind"] == "shell":
                db.insert_event(row["id"], "promote", {"agent": agent, "via": "process", "pid": pid})
        changed.append(row["id"])
    return changed
