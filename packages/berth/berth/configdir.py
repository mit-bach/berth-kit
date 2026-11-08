"""Which Claude Code config folder a session runs on: one folder per Claude account (2026-11-03).

Dominik's Accounts Agent spreads Claude sessions across his two Claude
accounts and moves them between accounts when they are safe to move
(~/STUDIO/Harness/Coding-Agents/Settings-Agent/work/2026-11-account-steward/
PROPOSAL.md). Claude Code keeps one login per config folder, so each account
gets one: ``~/.claude`` and, for example, ``~/.claude-personal``, which links to
``~/.claude`` for everything but the login.

Berth records the folder on each Claude row (``attachments.config_dir``; none
means ``~/.claude``) and starts the session with ``CLAUDE_CONFIG_DIR`` set to
it: a new session (``create`` with ``config_dir``), every open and cycle, a
fork (which inherits its source's folder), and a headless compaction. To move
a session to the other account: ``update`` with ``config_dir`` and ``cycle``
(or ``berth config-dir <id> <folder> --cycle``), which waits until the session
is idle like any cycle.

Berth reads what Claude writes from every such folder, not only ``~/.claude``:
running sessions (``sessions/``), transcripts (``projects/``), and prompt
history (``history.jsonl``). A folder that links those to ``~/.claude`` is read
once (``unique``).
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from berth.errors import BerthError

MARKERS = ("settings.json", ".claude.json", "projects", "sessions", "CLAUDE.md")


def default_root() -> Path:
    return Path.home() / ".claude"


def extra_roots() -> list[Path]:
    """The other Claude config folders on this Mac: ``~/.claude-*`` folders that hold Claude's files."""
    found = []
    for path in sorted(Path.home().glob(".claude-*")):
        if path.is_dir() and any((path / marker).exists() for marker in MARKERS):
            found.append(path)
    return found


def unique(paths) -> list[Path]:
    """Paths in order, each real place once: a linked folder is not read twice."""
    seen: set[str] = set()
    out = []
    for path in paths:
        try:
            key = str(Path(path).resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(Path(path))
    return out


def check(path) -> str | None:
    """A config folder as Berth records it: an absolute path, or None for the default ``~/.claude``."""
    text = str(path or "").strip()
    if text in ("", "default", "~/.claude"):
        return None
    folder = Path(text).expanduser()
    if not folder.is_absolute():
        raise BerthError("BAD_ARGUMENT", "config_dir is an absolute path (or ~/...)", {"config_dir": text})
    if not folder.is_dir():
        raise BerthError("BAD_ARGUMENT", f"{folder} is not a directory", {"config_dir": text})
    if not any((folder / marker).exists() for marker in MARKERS):
        raise BerthError("BAD_ARGUMENT", f"{folder} is not a Claude Code config folder (no settings.json, "
                                         ".claude.json, projects, or sessions in it)", {"config_dir": text})
    if folder.resolve() == default_root().resolve():
        return None
    return str(folder)


def _attachments(row) -> dict:
    try:
        data = json.loads(row["attachments"] or "{}")
    except (TypeError, ValueError, KeyError, IndexError):
        return {}
    return data if isinstance(data, dict) else {}


def of(row) -> str | None:
    """The folder a row runs on, or None for ``~/.claude``."""
    value = _attachments(row).get("config_dir")
    return value if isinstance(value, str) and value else None


def is_claude(row) -> bool:
    return (row["kind"] or "") != "grok" and (row["agent_name"] or "").casefold() == "claude"


def shell_prefix(folder: str | None) -> str:
    """What goes in front of the launch command, typed into the pane's login shell."""
    return f"export CLAUDE_CONFIG_DIR={shlex.quote(folder)}; " if folder else ""


def child_env(folder: str | None, base: dict | None = None) -> dict | None:
    """The environment for a headless ``claude`` run on this folder; None leaves the caller's own."""
    if not folder:
        return base
    import os

    env = dict(base if base is not None else os.environ)
    env["CLAUDE_CONFIG_DIR"] = folder
    return env


def set_config_dir(db, berth_id: str, path, *, by: str = "") -> dict:
    """Run this Claude session on ``path`` from its next open or cycle. Returns the row."""
    from berth.timeutil import utc_now

    row = db.require_berth(berth_id)
    if not is_claude(row):
        raise BerthError("KIND_UNSAFE", f"{row['title']}: config_dir is for Claude sessions", {"berth_id": berth_id})
    folder = check(path)
    data = _attachments(row)
    was = data.get("config_dir")
    if folder is None:
        data.pop("config_dir", None)
    else:
        data["config_dir"] = folder
    with db.immediate():
        db.update_berth(row["id"], attachments=json.dumps(data, sort_keys=True), updated_at=utc_now())
        db.insert_event(row["id"], "config_dir", {"was": was, "now": folder, "by": by})
    return dict(db.require_berth(row["id"]))
