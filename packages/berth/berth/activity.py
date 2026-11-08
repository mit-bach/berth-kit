"""Is a session busy, and what is in its prompt box: read from the pane itself (2026-10-28).

The hook files said busy from the last hook event, which went stale and
missed most of what keeps a session busy. The pane does not: Claude Code and
Grok draw their state at the bottom of the screen, every frame. A session is
busy when any of these is true:

* a turn is running: cmux's program status for the pane is ``working`` (the
  progress the agent reports to the terminal; Claude repeats it every few
  seconds of a turn, so one older than ``WORKING_FRESH`` is stale), or
  Claude's status strip says ``esc to interrupt``, or Grok says ``send a
  message to interrupt``. The strip alone is not enough: Claude hides
  ``esc to interrupt`` while you type the next message during a turn;
* its turn ended but work it started is still running: Claude's strip counts
  background shells (``· 2 shells``), and its subagents still run: their own
  transcripts say so (``<session>/subagents/``: no ``finished`` yet, written in the
  last ``SUBAGENT_FRESH`` seconds). Not Claude's ``← N agents``: since Claude Code
  2.1.31x that is its agents view, every background session on the Mac, and a
  stale background job put "Waiting on 1 subagent" on 39 idle panes (2026-11-07);
* a question or a permission dialog is waiting for an answer (the program
  status is ``blocked``, or the dialog is on screen);
* in a shell, a command is running (a foreground process on its terminal
  that is not the shell);
* the pane shows something but not a prompt Berth recognizes (a menu open,
  something unknown). That counts as busy: closing it is not known to be safe.
  A blank pane (nothing drawn yet) is not busy: there is nothing to lose.

A draft (text typed into the prompt box and not sent) does not make a
session busy. Closing it saves the draft (``Draft``) and opening it again
pastes it back (``berth/identity.py``), so nothing typed is lost. A draft
Berth cannot read exactly (pasted text or an image shown as a chip, or a box
taller than the screen) blocks a close unless it is confirmed.

``observe`` reads one session's pane through cmux; everything else reads
text and returns facts. The watcher observes every live session every few
seconds into the ``runtime`` table (the screen's busy and the MCP's
``busy``); a close observes again right before it closes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from berth.lineedit import cell_width

_RULE = re.compile(r"^\s*─{8,}")
_ONLY_RULE = re.compile(r"^\s*─{8,}\s*$")
# What Claude shows in an empty prompt, in grey, not typed text (2026-10-29): a suggestion, or (while a
# fork or subagent runs) who a message would go to.
_PLACEHOLDER = re.compile(r'^(?:Try "[^"\n]*"|Message @\S+)$')
# Claude's agent list under the status strip, one row per agent, shown once a fork exists (/subtask, f on
# a /btw answer, a background agent): ⏺ on the one the prompt box talks to and ◯ on the rest. "◯ main",
# "⏺ <name>  <what it did>  idle", and a running one counting its tokens,
# "◯ <name>  <what it does>  17m 21s · ↓ 224.2k tokens".
_AGENT_ROW = re.compile(r"^\s*([⏺◯])\s+(\S+)(.*)$")
_COUNTING = re.compile(r"[↑↓]\s*[\d.,]+\s*[kKmM]?\s+tokens\s*$")
_CHIP = re.compile(r"\[(Pasted text|Pasted image|Image|Text) #\d+")
_SHELLS = re.compile(r"(\d+) (?:background )?shells?\b")
_AGENTS = re.compile(r"←\s*(\d+) agents?\b")
_TASKS = re.compile(r"(\d+) background tasks?\b")
# What covers Claude's prompt box, from the most telling words on it (Claude Code 2.1.312's own strings, 2026-10-29).
# Claude stopped and asks you:
_DIALOGS = (
    ("limit", ("usage limit reached", "you've hit your limit", "your usage limit has reset")),
    ("trust", ("quick safety check", "i trust this folder")),
    ("mcp", ("new mcp server found",)),
    ("plan", ("would you like to proceed?",)),  # "Claude has written up a plan and is ready to execute."
    ("question", ("type something.", "chat about this", "tab/arrow keys to navigate", "review your answers",
                  "submit answers")),  # AskUserQuestion: multiple choice
    ("permission", ("do you want to proceed?", "do you want to allow", "don't ask again", "always allow",
                    "tell claude what to do differently")),
)
NEEDS_YOU = ("trust", "mcp", "plan", "question", "permission")
# Any other list or picker: one you opened (/model, /resume), unless cmux says Claude is blocked on it.
_MENU = ("enter to select", "esc to cancel", "↑/↓ to navigate", "esc to close", "tab to amend", "press enter to continue")
_INTERRUPT = ("esc to interrupt", "to interrupt")
# Claude's status strip names the permission mode; a reopened session gets the same one back.
_MODES = (("bypass permissions on", "bypassPermissions"), ("auto mode on", "auto"), ("plan mode on", "plan"),
          ("accept edits on", "acceptEdits"), ("don't ask on", "dontAsk"))
SHELLS = {"zsh", "-zsh", "bash", "-bash", "sh", "-sh", "fish", "-fish", "login", "tmux", "nu", "dash"}


@dataclass
class Draft:
    """What was typed into the prompt box and not sent."""

    text: str
    exact: bool = True
    why: str = ""  # when not exact: what Berth could not read

    def as_dict(self) -> dict:
        out = {"text": self.text, "exact": self.exact}
        if self.why:
            out["why"] = self.why
        return out


@dataclass
class Activity:
    busy: bool = False
    reasons: list[str] = field(default_factory=list)
    draft: Draft | None = None
    prompt: bool = False  # a prompt box Berth recognizes is on screen
    # What covers the prompt box: a _DIALOGS kind, "needs" (a dialog cmux says Claude is blocked on),
    # "menu", or the /btw panel, "btw_answering" or "btw_answered".
    dialog: str | None = None
    mode: str | None = None  # Claude's permission mode, from its status strip
    # The same facts as ``reasons``, for code (the cmux sidebar's status, berth/cmux_sidebar.py).
    turn: bool = False
    shells: int = 0
    agents: int = 0
    asking: bool = False
    unclear: bool = False
    command: str | None = None
    blank: bool = False
    # Claude's turn ended on an API error ("API Error: Can't reach the API server …"): the text, when its last
    # message is one (2026-11-03). It is idle and takes a prompt; the cmux sidebar says "API error".
    api_error: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"busy": self.busy}
        if self.reasons:
            out["why"] = list(self.reasons)
        if self.draft is not None:
            out["draft"] = "saved on close" if self.draft.exact else f"cannot be saved exactly: {self.draft.why}"
        return out


def _clean(screen: str) -> list[str]:
    # Claude draws a no-break space after ❯ and in some indents; it is not typed text.
    return [line.replace("\xa0", " ").rstrip() for line in (screen or "").replace("\r", "").split("\n")]


def _join(lines: list[str], width: int) -> str:
    """Rebuild typed text from wrapped screen lines: a break the next word would not fit across is a wrap.

    The renderer wraps a few cells inside the box, so the wrap width is the
    longest line drawn, and never less than six cells inside the box.
    """
    if not lines:
        return ""
    width = max([width - 6] + [cell_width(line) + 1 for line in lines])
    out = lines[0]
    for previous, current in zip(lines, lines[1:]):
        if previous and current:
            first = current.split(" ", 1)[0]
            used = cell_width(previous)
            if used >= width - 1 and " " not in previous.strip():
                out += current  # one word longer than the line (a URL): broken at the edge
                continue
            if used + 1 + cell_width(first) > width - 1:
                out += " " + current  # the next word did not fit: a soft wrap
                continue
        out += "\n" + current
    return out


def _claude(lines: list[str]) -> tuple[bool, Draft | None, list[str]]:
    """The prompt box: a rule, ``❯`` lines, a rule, then the status strip. Returns (found, draft, strip)."""
    bottom = next((i for i in range(len(lines) - 1, -1, -1) if _ONLY_RULE.match(lines[i])), None)
    if bottom is None:
        return False, None, []
    # Five lines, not three (2026-10-31): a statusLine setting draws its own line first, above Claude's mode line.
    strip = [line for line in lines[bottom + 1:] if line.strip()][:5]
    top = next((i for i in range(bottom - 1, -1, -1) if _RULE.match(lines[i])), None)
    body = lines[(top + 1) if top is not None else 0:bottom]
    while body and not body[0].strip():
        body = body[1:]
    if body and not body[0].lstrip().startswith("❯") and all(not line.strip() or line.startswith("  ") for line in body) \
            and (top is not None or len(body) >= 20):
        # The draft's first lines are not drawn: taller than its box, or than what Berth read.
        part = "\n".join(line.strip() for line in body)
        return True, Draft(part, exact=False, why="it is taller than the screen, so Berth sees only part of it"), strip
    if not body or not body[0].lstrip().startswith("❯"):
        return False, None, strip
    width = max(cell_width(lines[bottom].strip()) - 2, 10)
    first = body[0].lstrip()[1:]
    first = first[1:] if first.startswith(" ") else first
    rows = [first] + [line[2:] if line.startswith("  ") else line.lstrip() for line in body[1:]]
    while rows and not rows[-1].strip():
        rows.pop()
    text = _join(rows, width)
    if not text.strip() or _PLACEHOLDER.match(text.strip()):
        return True, None, strip
    draft = Draft(text)
    if _CHIP.search(text):
        draft.exact, draft.why = False, "it holds pasted text or an image Berth cannot read"
    return True, draft, strip


_COVERED = {
    "btw_answering": "a /btw side question is being answered",
    "btw_answered": "a /btw answer is open",
    "limit": "it stopped at the usage limit",
    "menu": "a menu is open",
}


_API_ERROR = ("api error", "can't reach the api server", "unable to connect to api")


def _api_error(lines: list[str]) -> str | None:
    """Claude's last message, when it is an API error; else None.

    Only the last ``⏺`` message above the prompt box counts: an old error stays
    on screen after a later turn worked, and must not count then. What follows
    it there is Claude's own end-of-turn lines (``✻ Brewed for …``, a ``※``
    recap, hints), never another message.
    """
    bottom = next((i for i in range(len(lines) - 1, -1, -1) if _ONLY_RULE.match(lines[i])), None)
    if bottom is None:
        return None
    top = next((i for i in range(bottom - 1, -1, -1) if _RULE.match(lines[i])), None)
    for line in reversed(lines[:top if top is not None else bottom]):
        text = line.strip()
        if not text.startswith("⏺"):
            continue
        message = text[1:].strip()
        return message if any(marker in message.casefold()[:80] for marker in _API_ERROR) else None
    return None


def _dialog(lines: list[str]) -> str | None:
    """What covers Claude's prompt box, when something does."""
    text = "\n".join(lines).casefold()
    if "esc to close" in text and any(line.strip() == "/btw" or line.strip().startswith("/btw ") for line in lines):
        # The /btw panel: "/btw <question>", then "Answering…" until the answer is in, then "c to copy".
        return "btw_answering" if "answering…" in text or "answering..." in text else "btw_answered"
    for kind, markers in _DIALOGS:
        if any(marker in text for marker in markers):
            return kind
    if any(marker in text for marker in _MENU):
        return "menu"
    return None


def _below_box(lines: list[str]) -> list[str]:
    """Everything under Claude's prompt box: the status strip, then the agent list when there is one."""
    bottom = next((i for i in range(len(lines) - 1, -1, -1) if _ONLY_RULE.match(lines[i])), None)
    return [] if bottom is None else lines[bottom + 1:]


def _agent_rows(lines: list[str]) -> list[tuple[str, bool, bool]]:
    """Claude's agent list: (name, the box talks to it, it is running) for each row."""
    rows = []
    for line in _below_box(lines):
        found = _AGENT_ROW.match(line)
        if found:
            rows.append((found.group(2), found.group(1) == "⏺", bool(_COUNTING.search(found.group(3)))))
    return rows


_TURN_STALE = 3600.0  # a turn whose transcript has said nothing for an hour is not trusted to be running
# A subagent with no "finished" whose transcript has said nothing this long is not running: a crash or a killed
# Claude leaves one unfinished for good.
SUBAGENT_FRESH = 600.0


def running_subagents(row, *, now: float | None = None) -> int | None:
    """How many of this Claude session's subagents (forks, background agents) are running, from their own records;
    None when unknown. ``<session>/subagents/agent-*.meta.json`` says ``"finished": true`` once one is done."""
    if (row["agent_name"] or "").casefold() != "claude" or not row["agent_session_id"]:
        return None
    import time as _time

    from berth import identity

    try:
        path = identity.claude_transcript(row["agent_session_id"], row["cwd"] or "")
    except OSError:
        return None
    if path is None:
        return None
    folder = path.with_suffix("") / "subagents"
    moment = _time.time() if now is None else now
    count = 0
    try:
        metas = list(folder.glob("agent-*.meta.json"))
    except OSError:
        return 0
    for meta in metas:
        try:
            if json.loads(meta.read_text(encoding="utf-8")).get("finished") is True:
                continue
            written = meta.with_name(meta.name[: -len(".meta.json")] + ".jsonl").stat().st_mtime
        except (OSError, ValueError, AttributeError):
            continue
        if moment - written <= SUBAGENT_FRESH:
            count += 1
    return count
_turn_cache: dict[str, tuple[int, int, bool | None, datetime | None]] = {}


def main_turn(row) -> bool | None:
    """Whether Claude's own thread (main, not a fork) is in a turn, from its transcript; None when unknown.

    Forks write their own transcripts (``<session>/subagents/``), so the main file
    moves only for main: a prompt, a tool call or result, or a task notice starts
    or continues a turn; Claude's ``turn_duration`` line, an ``end_turn`` reply,
    or an interrupt ends it. Needed because the strip's "esc to interrupt" belongs
    to the agent the box talks to, and cmux's program status says working while
    any agent in the process works (2026-10-29).
    """
    if (row["agent_name"] or "").casefold() != "claude" or not row["agent_session_id"]:
        return None
    from berth import identity

    try:
        path = identity.claude_transcript(row["agent_session_id"], row["cwd"] or "")
        if path is None:
            return None
        st = path.stat()
    except OSError:
        return None
    cached = _turn_cache.get(str(path))
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        running, last = cached[2], cached[3]
    else:
        running, last = None, None
        with path.open("rb") as handle:
            handle.seek(max(0, st.st_size - 512 * 1024))
            for raw in handle.read().splitlines():
                if b'"type"' not in raw:
                    continue
                try:
                    item = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(item, dict) or item.get("isSidechain") is True:
                    continue
                kind = item.get("type")
                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                if kind == "system" and item.get("subtype") == "turn_duration":
                    running = False
                elif kind == "assistant":
                    running = message.get("stop_reason") != "end_turn"
                elif kind == "user":
                    content = message.get("content")
                    if isinstance(content, list):
                        content = next((part.get("text") for part in content
                                        if isinstance(part, dict) and part.get("type") == "text"), "")
                    running = not str(content or "").startswith("[Request interrupted")
                else:
                    continue
                stamp = item.get("timestamp")
                if isinstance(stamp, str):
                    try:
                        last = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    except ValueError:
                        pass
        _turn_cache[str(path)] = (st.st_mtime_ns, st.st_size, running, last)
    if running and last is not None and (datetime.now(timezone.utc) - last).total_seconds() > _TURN_STALE:
        return None
    return running


def _grok(lines: list[str]) -> tuple[bool, Draft | None, list[str]]:
    bottom = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].lstrip().startswith("╰")), None)
    if bottom is None:
        return False, None, []
    strip = [line for line in lines[bottom + 1:] if line.strip()][:3]
    top = next((i for i in range(bottom - 1, -1, -1) if lines[i].lstrip().startswith("╭")), None)
    if top is None:
        return False, None, strip
    inner = []
    for line in lines[top + 1:bottom]:
        text = line.strip()
        if text.startswith("│"):
            text = text[1:]
        if text.endswith("│"):
            text = text[:-1]
        inner.append(text.rstrip())
    if not inner or not inner[0].lstrip().startswith("❯"):
        return False, None, strip
    width = max(cell_width(lines[top].strip()) - 6, 10)
    first = inner[0].lstrip()[1:].lstrip(" ")
    rows = [first] + [row[3:] if row.startswith("   ") else row.strip() for row in inner[1:]]
    while rows and not rows[-1].strip():
        rows.pop()
    text = _join(rows, width)
    if not text.strip():
        return True, None, strip
    draft = Draft(text)
    if _CHIP.search(text):
        draft.exact, draft.why = False, "it holds pasted text or an image Berth cannot read"
    return True, draft, strip


def assess(screen: str, kind: str, agent: str = "", *, foreground: list[str] | None = None,
           program: str | None = None, main_turn_fn=None, subagents_fn=None) -> Activity:
    """What the pane says about one session.

    ``foreground``: a shell's foreground process names. ``program``: cmux's
    latest program status for the pane (``working``, ``blocked``, ...), when fresh.
    """
    activity = Activity()
    lines = _clean(screen)
    tail = "\n".join(line for line in lines[-12:]).casefold()
    if kind in ("shell", "process", "berth"):
        running = [name for name in (foreground or []) if name.rsplit("/", 1)[-1] not in SHELLS]
        if kind == "berth":
            return activity  # the Berth screen is not a session
        if running:
            activity.busy = True
            activity.command = running[0].rsplit("/", 1)[-1]
            activity.reasons.append(f"running {activity.command}")
        activity.prompt = True
        return activity
    if program == "working":
        activity.turn = True
        activity.reasons.append("a turn is running")
    elif program == "blocked":
        activity.asking = True
        activity.reasons.append("a question or a dialog is waiting for an answer")
    if not any(line.strip() for line in lines):
        activity.blank = True
        activity.busy = bool(activity.reasons)
        return activity  # nothing drawn yet: nothing to lose but a turn
    grok = kind == "grok" or agent == "grok"
    found, draft, strip = (_grok if grok else _claude)(lines)
    if not found:
        # A row filed as the other agent (or a shell that became one): read the other box.
        other = (_claude if grok else _grok)(lines)
        if other[0]:
            found, draft, strip = other
    activity.prompt, activity.draft = found, draft
    text = " ".join(strip).casefold()
    if not grok:
        activity.mode = next((mode for words, mode in _MODES if words in text), None)
    if (any(marker in text for marker in _INTERRUPT) or (grok and "to interrupt" in tail)) \
            and "a turn is running" not in activity.reasons:
        activity.turn = True
        activity.reasons.append("a turn is running")
    shells = _SHELLS.search(text)
    if shells and int(shells.group(1)) > 0:
        count = int(shells.group(1))
        activity.shells = count
        activity.reasons.append(f"{count} background shell{'s' if count != 1 else ''} running")
    # Claude's "← N agents" is its agents view (every background session on the Mac), never this session's subagents
    # (2026-11-07): they come from their own transcripts, or from the agent list when it is on screen.
    count = (subagents_fn() or 0) if subagents_fn is not None and not grok else 0
    listed = _agent_rows(lines) if not grok else []
    forks = [row for row in listed if row[0] != "main"]
    if forks:
        # The agent list is the truth once it is shown (2026-10-29): the forks counting tokens are running,
        # and main is in a turn only by its own transcript, or, without one, by the strip while the box talks
        # to main. A fork's "esc to interrupt" and cmux's "working" are not main's.
        count = sum(1 for row in forks if row[2])
        main = main_turn_fn() if main_turn_fn is not None else None
        if main is None:
            talks_to_main = any(row[0] == "main" and row[1] for row in listed)
            main = any(row[0] == "main" and row[2] for row in listed) or (
                talks_to_main and any(marker in text for marker in _INTERRUPT))
        activity.turn = bool(main)
        if not main and "a turn is running" in activity.reasons:
            activity.reasons.remove("a turn is running")
        elif main and "a turn is running" not in activity.reasons:
            activity.reasons.insert(0, "a turn is running")
    if count > 0:
        activity.agents = count
        activity.reasons.append(f"{count} background agent{'s' if count != 1 else ''} running")
    tasks = _TASKS.search(text)
    if tasks and int(tasks.group(1)) > 0:
        activity.agents += int(tasks.group(1))
        activity.reasons.append(f"{tasks.group(1)} background task(s) running")
    if not found:
        activity.dialog = _dialog(lines[-40:])
        if activity.dialog == "menu" and activity.asking:
            activity.dialog = "needs"
        if activity.dialog in NEEDS_YOU or activity.dialog == "needs":
            activity.asking = True
            if "a question or a dialog is waiting for an answer" not in activity.reasons:
                activity.reasons.append("a question or a dialog is waiting for an answer")
        elif activity.dialog in _COVERED:
            activity.reasons.append(_COVERED[activity.dialog])
        elif not activity.reasons:
            activity.unclear = True
            activity.reasons.append("no prompt on screen (a menu or something Berth does not know is open)")
    elif not grok:
        activity.api_error = _api_error(lines)
    activity.busy = bool(activity.reasons)
    return activity


PROGRAMS = "programs.json"
WORKING_FRESH = 900.0  # seconds a "working" report counts without a newer one
BLOCKED_FRESH = 86400.0


def record_program(directory, event: dict) -> None:
    """Keep each pane's latest cmux program status (the watcher calls this for ``program_status.changed``)."""
    import json
    import os
    import tempfile
    from pathlib import Path

    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    workspace = str(event.get("workspace_id") or "").lower()
    if not workspace:
        return
    record = payload.get("record") if isinstance(payload.get("record"), dict) else {}
    state = record.get("state") if payload.get("event") == "report" else payload.get("event")
    path = Path(directory) / PROGRAMS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[workspace] = {"state": state or "", "at": event.get("occurred_at") or ""}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".programs.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def program_states(directory, now=None) -> dict[str, str]:
    """Each pane's program status that still counts: ``working`` under 15 minutes old, ``blocked`` under a day."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from berth.timeutil import parse_time

    try:
        data = json.loads((Path(directory) / PROGRAMS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    moment = now or datetime.now(timezone.utc)
    found = {}
    for workspace, item in data.items() if isinstance(data, dict) else ():
        state = (item or {}).get("state")
        try:
            age = (moment - parse_time(item.get("at") or "")).total_seconds()
        except (TypeError, ValueError, AttributeError):
            continue
        if (state == "working" and age <= WORKING_FRESH) or (state == "blocked" and age <= BLOCKED_FRESH):
            found[workspace] = state
    return found


def foreground(tty: str) -> list[str]:
    """The foreground process names on one terminal (``ps``'s ``+``), from the shared table (berth/proc.py)."""
    from berth import proc

    return proc.foreground(tty)


def observe(cmux, row, *, terminals: dict[str, list[str]] | None = None, lines: int = 120,
            programs: dict[str, str] | None = None) -> Activity:
    """Read one live session's pane and judge it. A pane cmux cannot read counts as busy."""
    from berth.errors import BerthError

    workspace = row["cmux_workspace_id"] or ""
    kind = row["kind"] or ""
    agent = (row["agent_name"] or "").casefold()
    try:
        screen = cmux.read_screen(workspace, lines=lines) if kind not in ("shell", "process") else ""
        names = None
        if kind in ("shell", "process"):
            ttys = (terminals if terminals is not None else cmux.terminals()).get(workspace.lower(), [])
            names = [name for tty in ttys for name in foreground(tty)]
    except BerthError:
        return Activity()  # an unreadable pane is as safe to close as a blank one
    return assess(screen, kind, agent, foreground=names, program=(programs or {}).get(workspace.lower()),
                  main_turn_fn=lambda: main_turn(row), subagents_fn=lambda: running_subagents(row))


def refresh(db, cmux, workspaces, publish=None) -> int:
    """Observe every live session and rewrite the ``runtime`` table. Returns how many are busy.

    ``publish(row, activity)`` is called for each session observed: the
    watcher passes the cmux sidebar's (``berth/cmux_sidebar.py``).
    """
    from berth.catalog import ensure
    from berth.timeutil import utc_now

    if workspaces is None:
        return 0
    present = {item.id.lower() for item in workspaces}
    rows = [row for row in db.conn.execute("SELECT * FROM berth WHERE state = 'live' AND cmux_workspace_id IS NOT NULL")
            if row["cmux_workspace_id"].lower() in present]
    terminals = None
    if any(row["kind"] in ("shell", "process") for row in rows):
        try:
            terminals = cmux.terminals()
        except Exception:
            terminals = {}
    from berth.cmux_sidebar import status_of

    seen = []
    programs = program_states(db.path.parent)
    for row in rows:
        activity = observe(cmux, row, terminals=terminals, programs=programs)
        if publish is not None:
            publish(row, activity)
        draft = ""
        if activity.draft is not None:
            draft = "saved" if activity.draft.exact else activity.draft.why
        shown = status_of(activity, row["kind"], flag=row["attention"] if "attention" in row.keys() else None)
        seen.append((row["id"], 1 if activity.busy else 0, "\n".join(activity.reasons), draft, shown.state, shown.label,
                     activity.mode or ""))
    stamp = utc_now()
    with db.immediate():
        ensure(db.conn)
        db.conn.execute("DELETE FROM runtime")
        db.conn.executemany(
            "INSERT INTO runtime (berth_id, busy, why, draft, status, label, mode, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(*item, stamp) for item in seen])
    return sum(item[1] for item in seen)
