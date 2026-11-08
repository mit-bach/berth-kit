"""What cmux's sidebar says about each session: one status, Berth's, in one color (2026-10-29).

cmux's own sidebar spoke with several voices for one session: the agent hook's
status line (``claude_code``: Running, Idle, Waiting; ``grok``: Idle), the
terminal's progress line (``program_status``: "claude-code · Working",
"Done"), the last notification ("Claude is waiting for your input", "Waiting
for your next prompt"), the last message, and the path. They disagreed, Claude
and Grok reported differently, and none of them knew about background shells.

Berth reads each pane (``berth/activity.py``) and says one thing:

    state          label                    color    can you talk to it
    urgent         Urgent                   red      yes, first: it said it needs you urgently
                                                     (berth/attention.py); shown once its turn ends
    api_error      API error                brown    yes: its turn ended on an API error (the API server out of
                                                     reach, overloaded, timed out); a prompt such as "continue"
                                                     picks it up again once the network is back (2026-11-03)
    follow_up      Follow up                magenta  yes: its turn ended but the work did not (a backlog,
                                                     or something for you to do); it said so, in its own
                                                     place in the list, until your next prompt
    needs_you      Question, Permission,    amber    yes: Claude stopped and asks you (multiple choice,
                   Plan to approve, Trust            a tool permission, a plan, Claude's trust dialog, a new MCP
                   folder?, New MCP server,          server; Needs you when cmux says it is blocked on
                   Needs you                         something else)
    limited        Usage limit              gray     no: stopped until the limit resets
    working        Working                  blue     no: a turn is running (with or without subagents,
                                                     and with or without a /btw open)
    btw_answered   /btw answered            cyan     yes: a side answer is open; read it, Esc closes it
    btw_answering  Answering /btw           cyan     not yet: the side question is being answered
    background     Waiting on 2 subagents,  purple   no: main's turn ended and subagents (forks: /subtask,
                   Waiting on 1 shell                a /btw fork, background agents) or shells it started
                                                     still run; it comes back by itself. With main in a
                                                     turn as well, it is Working
    running        Running <command>        blue     a shell running a command
    shell          Shell                    gray     a shell at its prompt
    menu           Menu open                gray     a list or picker you opened (/model, /resume)
    unclear        Unclear                  amber    no prompt Berth recognizes: go and look
    starting       Starting                 gray     nothing drawn yet
    ready          Ready  (Ready · draft)   green    yes
    screen      Berth                    yellow   the Berth screen (a square, not a dot: it is not a session)

It reaches cmux two ways, each rewritten only when it changes:

* A status line under the key ``berth`` (``cmux set-status``): what the stock
  sidebar shows. With ``sidebar.compactAgentStatus`` on, cmux folds away its
  own agent lines and keeps this one.
* The workspace's progress label, ``berth1|state|label|folder|mode|epoch``:
  the channel the Berth sidebar (``cmux-sidebar/berth.js``, a cmux custom
  sidebar) reads, because custom sidebars cannot read status lines. Only
  ``cmux set-progress`` writes a workspace's progress; the stock sidebar hides
  it (``sidebar.showProgress`` off), so it is never drawn as a bar. ``mode``
  is the sidebar order (``berth/sidebar.py``): in ``folders`` the Berth
  sidebar puts a folder heading over each folder's sessions. ``epoch`` is
  when it was written; the watcher rewrites one row every ``BEAT_EVERY``
  seconds, so the sidebar can tell a stopped watcher from a quiet one.

Notifications say the same words. cmux runs ``notifications.hooks`` on each
one; Berth's (``berth notify-hook``, ``notification`` below) titles it with the
session's name and says Done or Needs you, for Claude and Grok alike, where
cmux said "Claude Code", "Grok", "Completed in <folder>", "Waiting". The
reminders a minute after a turn ("Claude is waiting for your input",
"Waiting for your next prompt") are dropped: the sidebar already says Ready.

``install`` links the Berth sidebar into ``~/.config/cmux/sidebars``, writes
the settings in ``SETTINGS`` and the hook, and selects the sidebar;
``uninstall`` undoes it. Both only use cmux's documented CLI and files, so
they can be run again after a cmux update (``berth sidebar install``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from berth.errors import BerthError

CHANNEL = "berth1"
STATUS_KEY = "berth"
STATUS_PRIORITY = 100
BEAT_EVERY = 30.0
# Everything is written again this often, in case something else wrote over it.
REWRITE_EVERY = 300.0
SIDEBAR_NAME = "berth"
SIDEBAR_SOURCE = Path(__file__).resolve().parents[1] / "cmux-sidebar" / "berth.js"
SIDEBARS_DIR = Path.home() / ".config" / "cmux" / "sidebars"

# state -> (label, SF Symbol, color). Green and amber are the ones to talk to.
LOOK = {
    "needs_you": ("Needs you", "exclamationmark.circle.fill", "#FF9F0A"),
    "ready": ("Ready", "checkmark.circle.fill", "#30D158"),
    "working": ("Working", "bolt.circle.fill", "#0A84FF"),
    "background": ("Waiting", "hourglass.circle.fill", "#BF5AF2"),
    "btw_answering": ("Answering /btw", "text.bubble", "#64D2FF"),
    "btw_answered": ("/btw answered", "text.bubble.fill", "#64D2FF"),
    "limited": ("Usage limit", "exclamationmark.octagon", "#8E8E93"),
    "urgent": ("Urgent", "exclamationmark.triangle.fill", "#FF453A"),
    "follow_up": ("Follow up", "flag.fill", "#FF2DD1"),
    "api_error": ("API error", "exclamationmark.icloud.fill", "#AC8E68"),
    "menu": ("Menu open", "list.bullet.circle", "#8E8E93"),
    "running": ("Running", "play.circle.fill", "#0A84FF"),
    "shell": ("Shell", "terminal", "#8E8E93"),
    "starting": ("Starting", "circle.dotted", "#8E8E93"),
    "unclear": ("Unclear", "questionmark.circle.fill", "#FF9F0A"),
    "screen": ("Berth", "square.grid.2x2.fill", "#FFD60A"),
    "screen_twice": ("Two Berth screens: delete one", "exclamationmark.triangle.fill", "#FF453A"),
}

# The stock sidebar, quieted: one Berth line per row (cmux.json keys and values).
SETTINGS = {
    "sidebar.compactAgentStatus": True,
    "sidebar.showNotificationMessage": False,
    "sidebar.showProgress": False,
    "sidebar.showLog": False,
    # "Claude is waiting for your input" a minute after every turn: Ready already says it.
    "notifications.agentIdleReminder": False,
    # A notification moved its workspace to the top, and Berth's order put it back (berth/sidebar.py).
    "app.reorderOnNotification": False,
}
HOOK_ID = "berth"
PROVIDER_DEFAULTS = ("com.cmuxterm.app", "cmuxExtensionSidebarProviderId")
STOCK_PROVIDER = "cmux.sidebar.default"
# Said a minute after a turn ends; the sidebar's Ready says it all the time.
REMINDERS = ("Claude is waiting for your input", "Waiting for your next prompt")
SILENT = {"record": False, "markUnread": False, "reorderWorkspace": False, "desktop": False, "sound": False,
          "command": False, "paneFlash": False}


@dataclass(frozen=True)
class Status:
    state: str
    label: str
    icon: str
    color: str

    def channel(self, folder: str, mode: str, epoch: float, pinned: bool = False) -> str:
        # pinned is Berth's pin: the only one there is (berth/pins.py, 2026-10-31). The sidebar draws "Pinned" from it.
        clean = lambda text: (text or "").replace("|", "/").replace("\n", " ")
        return "|".join([CHANNEL, self.state, clean(self.label), clean(folder), clean(mode), str(int(epoch)),
                         "1" if pinned else "0"])


# What Claude asks you, by what covers its prompt box (berth/activity.py, _DIALOGS).
ASKS = {"question": "Question", "permission": "Permission", "plan": "Plan to approve", "trust": "Trust directory?",
        "mcp": "New MCP server"}


def status_of(activity, kind: str, *, urgent: bool = False, twice: bool = False, flag: str | None = None) -> Status:
    """One state for one session, from what its pane shows, and the flag it raised (berth/attention.py).

    ``flag`` is the row's ``attention``: urgent or follow_up. ``urgent=True`` is the same as ``flag="urgent"``.
    """
    dialog = getattr(activity, "dialog", None)
    if urgent:
        flag = "urgent"
    if kind == "berth":
        state = "screen_twice" if twice else "screen"
    elif flag == "urgent" and not activity.turn:
        state = "urgent"  # it said so; while its turn still runs, Working (2026-10-29)
    elif activity.asking:
        state = "needs_you"
    elif dialog == "limit":
        state = "limited"
    elif activity.turn:
        state = "working"  # a running turn outranks an open /btw (2026-10-29)
    elif dialog in ("btw_answered", "btw_answering"):
        state = dialog  # it outranks waiting on agents or shells, and ready
    elif getattr(activity, "api_error", None):
        state = "api_error"  # its turn ended on an API error: idle, and a prompt picks it up (2026-11-03)
    elif flag == "follow_up" and kind not in ("shell", "process"):
        state = "follow_up"  # its turn is over and it said there is more: instead of Ready or Waiting (2026-10-31)
    elif activity.shells or activity.agents:
        state = "background"
    elif activity.command:
        state = "running"
    elif kind in ("shell", "process"):
        state = "shell"
    elif dialog == "menu":
        state = "menu"
    elif activity.unclear:
        state = "unclear"
    elif activity.blank:
        state = "starting"
    else:
        state = "ready"
    label, icon, color = LOOK[state]
    if state == "needs_you":
        label = ASKS.get(dialog, label)
    elif state == "background":
        parts = [f"{count} {name}{'s' if count != 1 else ''}"
                 for name, count in (("subagent", activity.agents), ("shell", activity.shells)) if count]
        label = "Waiting on " + " and ".join(parts)
    elif state == "running":
        label = f"Running {activity.command}"
    elif state == "ready" and activity.draft is not None:
        label = "Ready · draft"
    return Status(state, label, icon, color)


class Publisher:
    """Writes each live session's status to cmux when it changes. One per watcher."""

    def __init__(self, cmux, clock=time.time) -> None:
        self.cmux = cmux
        self.clock = clock
        self.sent: dict[str, tuple] = {}  # lowered workspace id -> (workspace id, status, folder, mode)
        self.wrote = 0.0
        self.rewrote = clock()
        self.folders: dict[str, str] = {}

    def row(self, db, row, activity, mode: str) -> bool:
        """``activity.refresh``'s callback: one catalog row and what its pane shows."""
        from berth.ops import folder_path

        folder_id = row["folder_id"]
        if folder_id not in self.folders:
            self.folders[folder_id] = folder_path(db, folder_id)
        twice = row["kind"] == "berth" and db.conn.execute(
            "SELECT count(*) FROM berth WHERE kind = 'berth' AND state = 'live'").fetchone()[0] > 1
        status = status_of(activity, row["kind"], flag=row["attention"], twice=twice)
        return self.publish(row["cmux_workspace_id"], status, self.folders[folder_id], mode, pinned=bool(row["pinned"]))

    def publish(self, workspace_id: str, status: Status, folder: str, mode: str = "tree", *, force: bool = False,
                pinned: bool = False) -> bool:
        key = workspace_id.lower()
        wanted = (workspace_id, status, folder, mode, pinned)
        before = self.sent.get(key)
        if before is not None and before[1:] == wanted[1:] and not force:
            return False
        now = self.clock()
        try:
            if force or before is None or before[1] != status:
                self.cmux.set_status(workspace_id, STATUS_KEY, status.label, icon=status.icon, color=status.color,
                                     priority=STATUS_PRIORITY)
            self.cmux.set_progress(workspace_id, 0.0, status.channel(folder, mode, now, pinned))
        except BerthError:
            self.sent.pop(key, None)
            return False  # cmux busy or the workspace just closed: try again next look
        self.sent[key] = wanted
        self.wrote = now
        return True

    def tick(self, present: set[str]) -> None:
        """Before a round: drop workspaces that are gone, and forget folder names (they can be renamed)."""
        self.folders = {}
        if self.clock() - self.rewrote >= REWRITE_EVERY:
            self.reset()
        for key in list(self.sent):
            if key not in present:
                del self.sent[key]

    def reset(self) -> None:
        """Write everything again on the next round: cmux restarted, or it is time (``REWRITE_EVERY``)."""
        self.sent = {}
        self.rewrote = self.clock()

    def beat(self) -> bool:
        """After a round: when nothing was written for ``BEAT_EVERY`` seconds, rewrite one row's time."""
        if not self.sent or self.clock() - self.wrote < BEAT_EVERY:
            return False
        workspace_id, status, folder, mode, pinned = self.sent[min(self.sent)]
        return self.publish(workspace_id, status, folder, mode, force=True, pinned=pinned)


# notifications ----------------------------------------------------------


def notification(envelope: dict, title_of) -> dict:
    """One cmux notification, in Berth's words. ``title_of(workspace_id)`` is the session's name or None."""
    note = envelope.get("notification") if isinstance(envelope.get("notification"), dict) else {}
    agent = envelope.get("agent") if isinstance(envelope.get("agent"), dict) else {}
    body = str(note.get("body") or "").strip()
    category = agent.get("category")
    out = dict(envelope)
    if category == "idle-reminder" or body in REMINDERS:
        out["effects"] = {**(envelope.get("effects") or {}), **SILENT}
        return out
    if not agent and note.get("title") not in ("Claude Code", "Grok"):
        return out  # someone's own `cmux notify`: theirs to word
    name = title_of(str(note.get("workspaceId") or "")) or note.get("title")
    out["notification"] = {**note, "title": name, "subtitle": "Needs you" if category == "needs-permission" else "Done"}
    out["effects"] = {**(envelope.get("effects") or {}), "reorderWorkspace": False}
    return out


def catalog_titles(catalog_path: Path):
    """``title_of`` for ``notification``: the live session's title in the catalog, read-only."""
    import sqlite3

    def title_of(workspace_id: str) -> str | None:
        if not workspace_id:
            return None
        try:
            conn = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True, timeout=1)
            try:
                row = conn.execute("SELECT title FROM berth WHERE state = 'live' AND lower(cmux_workspace_id) = lower(?)",
                                   (workspace_id,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        return row[0] if row else None

    return title_of


def notify_hook(stdin, stdout, catalog_path: Path) -> int:
    """``berth notify-hook``: cmux's notification hook. Never fails: on any trouble the notification passes as it came."""
    raw = stdin.read()
    try:
        envelope = json.loads(raw)
        out = notification(envelope, catalog_titles(catalog_path)) if isinstance(envelope, dict) else envelope
        stdout.write(json.dumps(out))
    except Exception:
        stdout.write(raw)
    return 0


# install --------------------------------------------------------------


def _cmux(*args: str) -> subprocess.CompletedProcess:
    binary = shutil.which("cmux") or "/Applications/cmux.app/Contents/Resources/bin/cmux"
    env = {**os.environ, "CMUX_QUIET": "1"}
    return subprocess.run([binary, *args], capture_output=True, text=True, env=env, timeout=30)


def _backup_config() -> str | None:
    config = Path.home() / ".config" / "cmux" / "cmux.json"
    if not config.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup = config.with_name(f"cmux.json.bak-{stamp}")
    shutil.copy2(config, backup)
    return str(backup)


def _config_value(path: str):
    """A cmux.json setting as cmux resolves it (``cmux config get`` prints ``path = <json> (default)``)."""
    done = _cmux("config", "get", path)
    text = done.stdout.strip()
    if done.returncode != 0 or " = " not in text:
        raise BerthError("CMUX_REJECTED", f"cmux could not read {path}", {"output": (done.stderr or text).strip()})
    value = text.split(" = ", 1)[1]
    for suffix in (" (default)", " (Settings)", " (settings)"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise BerthError("CMUX_REJECTED", f"cmux printed {path} as something other than JSON", {"value": value}) from exc


def _set_hooks(keep_berth: bool) -> str:
    hooks = [hook for hook in (_config_value("notifications.hooks") or [])
             if not (isinstance(hook, dict) and hook.get("id") == HOOK_ID)]
    if keep_berth:
        berth = shutil.which("berth") or str(Path.home() / ".local" / "bin" / "berth")
        hooks.append({"id": HOOK_ID, "command": f"'{berth}' notify-hook", "timeoutSeconds": 5})
    done = _cmux("config", "set", "notifications.hooks", json.dumps(hooks))
    return "set" if done.returncode == 0 else (done.stderr or done.stdout).strip()


# 2026-10-31: ⌘⇧X was first a cmux "command" action, which ran in a new tab of the pane. Dominik: the key must
# change Berth's state and nothing in a terminal. It moved to berth-keys (berth/keys.py); install takes the old
# action out of cmux.json, between these comments.
DISMISS_ACTION = "berth-dismiss-follow-up"
_DISMISS_START = "// berth: dismiss follow-up (berth sidebar install adds it, uninstall removes it)"
_DISMISS_END = "// /berth: dismiss follow-up"


def without_dismiss_action(text: str) -> str:
    """cmux.json (JSONC) with Berth's ⌘⇧X action taken out; everything else, comments too, as it was."""
    start = text.find(_DISMISS_START)
    if start < 0:
        return text
    end = text.find(_DISMISS_END, start)
    if end < 0:
        return text
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return text[:line_start] + text[(line_end + 1) if line_end >= 0 else len(text):]


def _remove_dismiss_action() -> str:
    config = Path.home() / ".config" / "cmux" / "cmux.json"
    if not config.is_file():
        return "no cmux.json"
    before = config.read_text(encoding="utf-8")
    after = without_dismiss_action(before)
    if after == before:
        return "not there"
    config.write_text(after, encoding="utf-8")
    checked = _cmux("config", "validate")
    if checked.returncode != 0:
        config.write_text(before, encoding="utf-8")  # cmux would not read it: put it back
        return "refused by cmux: " + (checked.stderr or checked.stdout).strip()[:200]
    return "removed"


def install(*, select: bool = True) -> dict:
    """Link the Berth sidebar, write the quiet settings and the notification hook, and pick the sidebar. Safe to run again."""
    if not SIDEBAR_SOURCE.is_file():
        raise BerthError("NOT_FOUND", f"the Berth sidebar is missing at {SIDEBAR_SOURCE}", {})
    report: dict = {"backup": _backup_config(), "settings": {}, "sidebar": str(SIDEBARS_DIR / f"{SIDEBAR_NAME}.js")}
    for path, value in SETTINGS.items():
        done = _cmux("config", "set", path, json.dumps(value))
        report["settings"][path] = "set" if done.returncode == 0 else (done.stderr or done.stdout).strip()
    report["settings"]["notifications.hooks"] = _set_hooks(keep_berth=True)
    report["settings"][f"actions.{DISMISS_ACTION}"] = _remove_dismiss_action()
    SIDEBARS_DIR.mkdir(parents=True, exist_ok=True)
    link = SIDEBARS_DIR / f"{SIDEBAR_NAME}.js"
    if link.is_symlink() or link.exists():
        if not (link.is_symlink() and Path(os.readlink(link)) == SIDEBAR_SOURCE):
            raise BerthError("BAD_ARGUMENT", f"{link} exists and is not Berth's; move it first", {"path": str(link)})
    else:
        link.symlink_to(SIDEBAR_SOURCE)
    checked = _cmux("sidebar", "validate", SIDEBAR_NAME)
    report["validate"] = (checked.stdout or checked.stderr).strip()
    if checked.returncode != 0:
        raise BerthError("CMUX_REJECTED", "cmux refused the Berth sidebar", {"validate": report["validate"]})
    _cmux("reload-config")
    if select:
        chosen = _cmux("sidebar", "select", SIDEBAR_NAME)
        report["selected"] = chosen.returncode == 0
    return report


def uninstall() -> dict:
    """Put cmux's own sidebar, settings and notifications back. Stop the watcher too, or its status lines stay."""
    report: dict = {"backup": _backup_config(), "settings": {}}
    for path in SETTINGS:
        done = _cmux("config", "unset", path)
        report["settings"][path] = "unset" if done.returncode == 0 else (done.stderr or done.stdout).strip()
    report["settings"]["notifications.hooks"] = _set_hooks(keep_berth=False)
    report["settings"][f"actions.{DISMISS_ACTION}"] = _remove_dismiss_action()
    # cmux has no command for its own sidebar; the menu's choice lives in its defaults.
    subprocess.run(["defaults", "write", *PROVIDER_DEFAULTS, STOCK_PROVIDER], capture_output=True, timeout=10)
    link = SIDEBARS_DIR / f"{SIDEBAR_NAME}.js"
    if link.is_symlink() and Path(os.readlink(link)) == SIDEBAR_SOURCE:
        link.unlink()
        report["sidebar"] = "removed"
    _cmux("reload-config")
    return report
