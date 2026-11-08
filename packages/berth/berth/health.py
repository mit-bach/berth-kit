"""Is each part of Berth working, and is it running the code on disk? (2026-10-31)

Two weaknesses of the watcher, found when Dominik asked what was wrong with it as a whole:

* **One job could stall the rest.** The watcher's tick ran a dozen jobs (adopt and
  park, colors, pins, the sidebar, names, queued closes, the prompt mailbox, ...) in
  one ``try``. A job that kept failing skipped every job after it, every tick, and
  only its first error was logged: a broken pin sync silently stopped deliveries.
  ``Jobs`` runs each on its own, keeps each one's health, and writes it to
  ``watch-health.json``, which ``berth status`` and the MCP's ``status`` read.
* **Old code ran unseen.** The watcher, the screen and every session's ``berth mcp``
  run the code they started with; much of 2026-10-30's "still broken" was an old
  process. ``code_stamp`` names the code on disk, each process records the stamp
  it started with (``register``), and ``report`` lists who runs old code, by the
  session that owns them, so the operator knows what to restart or cycle.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
HEALTH_NAME = "watch-health.json"
PROCESSES = "processes"
STALE_SECONDS = 90  # a watcher that wrote no health for this long is not ticking
_stamp_cache: list = [0.0, ""]


def code_stamp(*, fresh: bool = False) -> str:
    """A short name for the Berth code on disk: every module's path, size and modification time."""
    if not fresh and time.monotonic() - _stamp_cache[0] < 10 and _stamp_cache[1]:
        return _stamp_cache[1]
    digest = hashlib.sha1()
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            info = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(PACKAGE)}:{info.st_size}:{info.st_mtime_ns}\n".encode())
    _stamp_cache[:] = [time.monotonic(), digest.hexdigest()[:12]]
    return _stamp_cache[1]


STARTED_WITH = code_stamp(fresh=True)


def changed_since_start() -> bool:
    return code_stamp() != STARTED_WITH


# --- the watcher's jobs ---------------------------------------------------------

def log_error(stream, what: str) -> None:
    """One dated line, then the traceback (2026-11-03: watch.err's tracebacks had no dates)."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{stamp} berth watch: {what}", file=stream, flush=True)
    traceback.print_exc(file=stream)
    stream.flush()


class Jobs:
    """Each job on its own: one that fails is logged and recorded, and the others still run."""

    def __init__(self, directory: Path, *, clock=time.time, stream=None):
        self.path = Path(directory) / HEALTH_NAME
        self.clock = clock
        self.stream = stream or sys.stderr
        self.state: dict[str, dict] = {}
        self.written = 0.0

    def run(self, name: str, fn, *args, **kwargs):
        """Run one job. Returns its result, or None when it failed."""
        now = self.clock()
        item = self.state.setdefault(name, {"ok_at": None, "error": None, "error_at": None, "failures": 0})
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # a job must never take the others down
            message = f"{type(exc).__name__}: {exc}"
            if message != item["error"]:
                log_error(self.stream, f"{name} failed: {message}")
            item.update(error=message, error_at=now, failures=item["failures"] + 1)
            return None
        item.update(ok_at=now, error=None, failures=0)
        return result

    def failing(self) -> dict[str, dict]:
        return {name: item for name, item in self.state.items() if item["error"]}

    def write(self, *, every: float = 10.0) -> None:
        """Write the health file: at once when a job's health changed, else every ``every`` seconds."""
        now = self.clock()
        shape = json.dumps({name: bool(item["error"]) for name, item in self.state.items()}, sort_keys=True)
        if shape == getattr(self, "_shape", None) and now - self.written < every:
            return
        self._shape, self.written = shape, now
        body = {"at": now, "pid": os.getpid(), "code": STARTED_WITH, "jobs": self.state}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
        temp.replace(self.path)


# --- who runs which code -------------------------------------------------------

def register(directory: Path, kind: str) -> None:
    """Record this process (``watch``, ``screen``, ``mcp``) and the code it started with."""
    folder = Path(directory) / PROCESSES
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{os.getpid()}.json").write_text(json.dumps(
            {"pid": os.getpid(), "ppid": os.getppid(), "kind": kind, "code": STARTED_WITH,
             "started": time.time()}), encoding="utf-8")
    except OSError:
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _session_of(ppid: int) -> str | None:
    """The Claude session an MCP server belongs to: its parent's record in a Claude config folder's sessions/."""
    from berth.claude import sessions_dirs

    for directory in sessions_dirs():
        try:
            return json.loads((directory / f"{ppid}.json").read_text(encoding="utf-8")).get("sessionId")
        except (OSError, ValueError):
            continue
    return None


def report(db, directory: Path, *, now: float | None = None) -> dict:
    """What ``status`` says: the watcher's failing jobs, and every running process on old code."""
    now = time.time() if now is None else now
    directory = Path(directory)
    current = code_stamp(fresh=True)
    out: dict = {"code": current}
    try:
        health = json.loads((directory / HEALTH_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        health = None
    if health is not None:
        out["watcher_ticking"] = now - float(health.get("at") or 0) < STALE_SECONDS
        out["failing_jobs"] = {name: {"error": item["error"], "failures": item["failures"]}
                               for name, item in (health.get("jobs") or {}).items() if item.get("error")}
    old: list[dict] = []
    folder = directory / PROCESSES
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not _alive(int(item.get("pid") or 0)):
            path.unlink(missing_ok=True)
            continue
        if item.get("code") == current:
            continue
        entry = {"kind": item.get("kind"), "pid": item.get("pid")}
        if item.get("kind") == "mcp":
            session = _session_of(int(item.get("ppid") or 0))
            row = db.conn.execute("SELECT title FROM berth WHERE agent_session_id = ?", (session,)).fetchone() \
                if session else None
            entry["session"] = row[0] if row is not None else None
            entry["fix"] = "cycle that session"
        elif item.get("kind") == "screen":
            entry["fix"] = "q in the Berth screen, then berth"
        else:
            entry["fix"] = "launchctl kickstart -k gui/$(id -u)/com.bach.berth.watch"
        old.append(entry)
    out["old_code"] = old + _unregistered(db, {int(path.stem) for path in folder.glob("*.json")} if folder.is_dir() else set())
    return out


def _unregistered(db, known: set[int], *, ps=None) -> list[dict]:
    """Berth processes started before they registered (before 2026-10-31): their code is older than any stamp."""
    import subprocess

    try:
        table = ps() if ps is not None else subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True,
                                                            text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in table.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or int(parts[0]) in known or int(parts[0]) == os.getpid():
            continue
        words = parts[2].split()
        berth_at = next((i for i, word in enumerate(words[:3]) if word.rsplit("/", 1)[-1] == "berth"), None)
        if berth_at is None:
            continue
        rest = words[berth_at + 1:]
        if rest[:1] == ["mcp"]:
            session = _session_of(int(parts[1]))
            row = db.conn.execute("SELECT title FROM berth WHERE agent_session_id = ?", (session,)).fetchone() \
                if session else None
            found.append({"kind": "mcp", "pid": int(parts[0]), "session": row[0] if row is not None else None,
                          "fix": "cycle that session", "code": "from before 2026-10-31"})
        elif not rest or rest[:1] == ["screen"]:
            found.append({"kind": "screen", "pid": int(parts[0]), "fix": "q in the Berth screen, then berth",
                          "code": "from before 2026-10-31"})
    return found
