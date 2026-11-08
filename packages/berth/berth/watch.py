"""Block and adopt each workspace that appears.

The event name was measured on cmux 0.64.25, not chosen. A retained
``cmux events --category workspace`` stream named the create frame
``workspace.created``. The payload and the top-level field both carry
``workspace_id``. Subscribe with ``cmux events --name workspace.created``
and ``--name workspace.closed``. A close parks the matching row. The
session id and the recipe stay. The workspace id is cleared.

The first start seeds the cursor at the current sequence so the retained
buffer is not replayed onto the open fleet. Later starts resume from that
cursor, which is how a restart catches a workspace created while the
watcher was down. Automatic pause stays off. This loop never closes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from berth.adopt import adopt_appearing
from berth.cmux import Cmux
from berth.db import Catalog
from berth import memory
from berth.hooks import load_hooks
from berth.place import pid_alive
from berth.timeutil import age_seconds, utc_now

# Measured names. See the module docstring.
WORKSPACE_CREATED = "workspace.created"
WORKSPACE_CLOSED = "workspace.closed"
# Kept so the screen knows which session you switched to it from (berth/arrival.py).
WORKSPACE_SELECTED = "workspace.selected"
# Each pane's progress (working, done, blocked), for busy (berth/activity.py).
PROGRAM_STATUS = "program_status.changed"
WATCH_NAMES = (WORKSPACE_CREATED, WORKSPACE_CLOSED, WORKSPACE_SELECTED, PROGRAM_STATUS)
# How often every live pane is read for busy and drafts (berth/activity.py). 25 panes take about 0.5 s.
ACTIVITY_EVERY = 3.0
LABEL = "com.bach.berth.watch"


def cursor_path(directory: Path) -> Path:
    return directory / "events.seq"


def pid_path(directory: Path) -> Path:
    return directory / "watch.pid"


def heartbeat_path(directory: Path) -> Path:
    return directory / "watch.heartbeat"


def seed_cursor(cmux: Cmux, path: Path) -> None:
    """Write the latest sequence when the file is empty, so startup does not replay."""
    if path.exists() and path.read_text(encoding="utf-8").strip():
        return
    latest = cmux.events_snapshot_seq()
    if latest is None:
        return
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_text(f"{latest}\n", encoding="utf-8")
    os.chmod(path, 0o600)


def handle_line(db: Catalog, cmux: Cmux, line: str) -> dict | None:
    text = line.strip()
    if not text:
        return None
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    if event.get("type") == "ack":
        return None
    name = event.get("name")
    workspace_id = _workspace_id(event)
    if not workspace_id:
        return None
    if name == WORKSPACE_CLOSED:
        # A hint. The observer parks once a fresh list confirms the close.
        return None
    if name == PROGRAM_STATUS:
        from berth.activity import record_program

        record_program(db.path.parent, event)
        return None
    if name == WORKSPACE_SELECTED:
        from berth.arrival import record

        record(db.path.parent, event)
        return None
    if name != WORKSPACE_CREATED:
        return None
    record = adopt_appearing(db, cmux, load_hooks, workspace_id)
    return record


def _refresh(path: Path, cmux: Cmux, stop: threading.Event) -> None:
    """A shell that later starts Grok does not emit workspace.created.

    This thread opens its own catalog connection. The connection in ``serve``
    belongs to the event thread, and SQLite refuses to share it. That refusal
    used to be swallowed, so a shell that became Grok stayed a shell.
    """
    from berth.db import Catalog
    from berth.hooks import load_hooks
    from berth.identity import refresh_identity

    from berth.invariant import Monitor
    from berth.observer import Observer
    from berth.palette import sync as palette_sync
    from berth.recency import Recency
    from berth.activity import refresh as activity_refresh
    from berth.deferred import run_due as run_deferred
    from berth.pins import sync as pins_sync
    from berth.standard import tick as standard_tick
    from berth.attention import settle as attention_settle
    from berth.cmux_sidebar import Publisher
    from berth.sidebar import Pace, read_sidebar
    from berth.sidebar import sync as sidebar_sync

    db = Catalog(path).open()
    monitor = Monitor()
    from berth.restore import Restorer

    observer = Observer(restore=Restorer(path, cmux), state_path=path.parent / "observer.json")
    recency = Recency()
    sidebar_pace = Pace()
    publisher = Publisher(cmux)
    looked = [0.0]
    try:
        # Each job on its own (berth/health.py, 2026-10-31): before, one job that kept failing skipped all the
        # jobs after it, every tick, deliveries and queued closes included, with only its first error logged.
        from berth.delivery import run_due as run_deliveries
        from berth.health import Jobs, register
        from berth.place import pid_alive as _alive
        from berth.screens import release_dead

        jobs = Jobs(path.parent)
        register(path.parent, "watch")

        def printed(kind, outcomes):
            for outcome in outcomes or []:
                print(json.dumps({kind: outcome} if kind else outcome, sort_keys=True), flush=True)

        def look_at_panes(fresh):
            mode = read_sidebar(path.parent)
            publisher.tick({item.id.lower() for item in fresh})
            activity_refresh(db, cmux, fresh, publish=lambda row, activity: publisher.row(db, row, activity, mode))
            publisher.beat()

        measured = [0.0]
        while not stop.wait(2):
            # A screen that died without quitting gives its row back first: a quit kills it at once, and the row
            # must be plain again before the observer parks it for the quit (berth/screens.py).
            jobs.run("screens", release_dead, db, _alive)
            hooks = jobs.run("hooks", load_hooks) or {}
            snapshot = jobs.run("snapshot", _snapshot, cmux)
            printed(None, jobs.run("observer", lambda: list(observer.tick(db, cmux, snapshot, hooks))))
            fresh = jobs.run("snapshot", _snapshot, cmux) if snapshot is not None else None
            jobs.run("colors", palette_sync, db, cmux, fresh)
            jobs.run("recency", recency.refresh, db, fresh, hooks)
            # An urgent flag a prompt has answered since it went up comes down (berth/attention.py).
            jobs.run("urgent", attention_settle, db, fresh)
            if jobs.run("pins", pins_sync, db, cmux, fresh):
                fresh = jobs.run("snapshot", _snapshot, cmux)
            if fresh is None:
                publisher.reset()  # cmux is down; a restarted cmux starts with no status lines
            if time.monotonic() - looked[0] >= ACTIVITY_EVERY and fresh is not None:
                looked[0] = time.monotonic()
                # One status per session in cmux's sidebar, from the same look (berth/cmux_sidebar.py).
                jobs.run("activity", look_at_panes, fresh)
            # What sessions asked for later: "cycle me", "cycle it when it is idle".
            printed("deferred", jobs.run("closes", run_deferred, db, cmux))
            # Prompts waiting in sessions' mailboxes: the next one, once its session is ready.
            printed("delivery", jobs.run("delivery", run_deliveries, db, cmux))
            jobs.run("sidebar", sidebar_sync, path, cmux, fresh, sidebar_pace)
            jobs.run("names", refresh_identity, db, cmux, hooks)
            # Every Claude session in auto at xhigh, corrected at idle (berth/standard.py).
            printed("standard", jobs.run("standard", standard_tick, db, cmux, path.parent))
            jobs.run("invariant", lambda: monitor.observe(db, _snapshot(cmux), path.parent))
            # Memory, as macOS counts it and per session, every 30 s (berth/memory.py; the Performance Agent's
            # definitions). About half a second for 75 sessions; nothing else reads it live.
            if time.monotonic() - measured[0] >= memory.EVERY and fresh is not None:
                measured[0] = time.monotonic()
                jobs.run("memory", lambda: memory.store(path.parent, memory.measure(cmux.terminals()), at=utc_now()))
            try:
                jobs.write()
            except OSError:
                pass
    finally:
        db.close()


def _snapshot(cmux: Cmux) -> list | None:
    """One workspace list, or None when cmux cannot answer."""
    if not cmux.socket_up():
        return None
    try:
        return cmux.list_workspaces()
    except Exception:
        return None


def serve(db: Catalog, cmux: Cmux) -> None:
    directory = db.path.parent
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    from berth.health import log_error

    try:
        seed_cursor(cmux, cursor_path(directory))
    except Exception:
        log_error(sys.stderr, "seeding the event cursor failed")
    pid_path(directory).write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(pid_path(directory), 0o600)
    stop = threading.Event()
    beater = threading.Thread(target=_beat, args=(directory, stop), daemon=True)
    beater.start()
    refresher = threading.Thread(target=_refresh, args=(db.path, cmux, stop), daemon=True)
    refresher.start()
    from berth.keys import run as keep_keys

    # ⌘⇧X in cmux, outside any terminal (berth/keys.py). It exits with the watcher. BERTH_KEYS=off: none (tests).
    if os.environ.get("BERTH_KEYS", "on") != "off":
        threading.Thread(target=keep_keys, args=(directory, stop), daemon=True).start()
    command = [cmux.binary, "events"]
    for name in WATCH_NAMES:
        command.extend(["--name", name])
    command.extend(
        [
            "--cursor-file",
            str(cursor_path(directory)),
            "--reconnect",
            "--no-heartbeat",
        ]
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=cmux._env(),
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                record = handle_line(db, cmux, line)
            except Exception:
                # One event that fails (a cmux timeout on wake, a locked
                # catalog) must not stop the watcher. The observer catches up.
                log_error(sys.stderr, f"an event failed: {line.strip()[:120]}")
                continue
            if record is None:
                continue
            print(_line(record), flush=True)
    finally:
        stop.set()
        if process.poll() is None:
            process.terminate()
        pid_file = pid_path(directory)
        if pid_file.exists():
            pid_file.unlink()


def watcher_state(directory: Path) -> str:
    if launchd_running(LABEL):
        return "up"
    text = _read_text(pid_path(directory))
    if text and text.isdigit() and pid_alive(int(text)):
        beat = _read_text(heartbeat_path(directory))
        if not beat:
            return "up"
        try:
            if age_seconds(beat) < 90:
                return "up"
        except ValueError:
            return "up"
    return "down"


def launchd_running(label: str) -> bool:
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    if result.returncode != 0:
        return False
    return "state = running" in result.stdout


def _beat(directory: Path, stop: threading.Event) -> None:
    target = heartbeat_path(directory)
    while not stop.is_set():
        target.write_text(utc_now() + "\n", encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        stop.wait(15)


def _workspace_id(event: dict) -> str | None:
    top = event.get("workspace_id")
    if isinstance(top, str) and top:
        return top
    payload = event.get("payload")
    if isinstance(payload, dict):
        nested = payload.get("workspace_id")
        if isinstance(nested, str) and nested:
            return nested
    return None


def _line(record: dict) -> str:
    verb = "parked" if record.get("state") == "parked" else "adopted"
    return f"{verb} {record['id']} {record['title']} {record['kind']} {record['state']}"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


