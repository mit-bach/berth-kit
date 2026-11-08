"""Berth's keyboard shortcuts in cmux: a small native helper, kept by the watcher (2026-10-31).

⌘⇧X dismisses the follow-up of the session selected in cmux. Dominik's rule:
the key changes Berth's state and nothing else. It never types into a terminal
or opens a tab (a cmux "command" action, tried first, ran in a new tab of the
pane). cmux's own shortcuts can only run a command in a terminal, and its custom
sidebars cannot take a key, so the key is held by ``berth-keys``
(``keys/BerthKeys.swift``): a Carbon hot key, taken only while cmux is the
frontmost app, that runs ``berth follow-up selected --dismiss`` in the background.

The watcher builds it when its source is newer than the binary, starts it, and
starts it again if it exits (at most once a minute). It runs as a child of the
watcher's LaunchAgent, not a LaunchAgent of its own, and exits when the watcher
does (its stdin closes). ``keys.json`` in the data folder says how it is, for
``berth status``; ``keys.log`` is its log.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "keys" / "BerthKeys.swift"
BINARY = ROOT / "build" / "berth-keys"
RESTART_AFTER = 60.0


def state_path(directory: Path) -> Path:
    return directory / "keys.json"


def berth_command() -> str:
    return shutil.which("berth") or str(Path.home() / ".local" / "bin" / "berth")


def build(*, source: Path = SOURCE, binary: Path = BINARY, compiler: str = "swiftc") -> str:
    """Compile the helper if the binary is missing or older than its source. Returns what it did."""
    if binary.is_file() and binary.stat().st_mtime >= source.stat().st_mtime:
        return "current"
    binary.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([compiler, "-O", "-o", str(binary), str(source)], capture_output=True, text=True,
                          timeout=600)
    if done.returncode != 0:
        raise RuntimeError(f"swiftc failed: {(done.stderr or done.stdout).strip()[:400]}")
    return "built"


class Keeper:
    """Starts the helper and starts it again when it exits. One per watcher."""

    def __init__(self, directory: Path, *, binary: Path = BINARY, argv: list[str] | None = None,
                 clock=time.time) -> None:
        self.directory = directory
        self.binary = binary
        self.argv = argv
        self.clock = clock
        self.process: subprocess.Popen | None = None
        self.started_at = 0.0
        self.starts = 0
        self.error = ""

    def tick(self) -> None:
        """Start the helper when it is not running, unless it last started less than a minute ago."""
        if self.process is not None and self.process.poll() is None:
            return
        if self.process is not None and self.clock() - self.started_at < RESTART_AFTER:
            self._write("exited, waiting to restart")
            return
        argv = self.argv or [str(self.binary), berth_command()]
        try:
            log = open(self.directory / "keys.log", "a", encoding="utf-8")
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            log.close()
            self.started_at = self.clock()
            self.starts += 1
            self.error = ""
            self._write("running")
        except OSError as exc:
            self.error = str(exc)
            self._write("not running")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                if self.process.stdin is not None:
                    self.process.stdin.close()  # the helper exits when its stdin closes
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
        self._write("stopped")

    def _write(self, state: str) -> None:
        document = {"state": state, "pid": getattr(self.process, "pid", None),
                    "starts": self.starts, "error": self.error, "at": self.clock()}
        try:
            path = state_path(self.directory)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(document) + "\n", encoding="utf-8")
            temp.replace(path)
        except OSError:
            pass


def read_state(directory: Path) -> dict:
    """What ``berth status`` says about the helper: running (and its pid is alive), or why not."""
    try:
        document = json.loads(state_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "not started"}
    pid = document.get("pid")
    if document.get("state") == "running" and isinstance(pid, int):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            document["state"] = "gone"
        except PermissionError:
            pass
    return document


def run(directory: Path, stop) -> None:
    """The watcher's thread: build once, then keep the helper running until ``stop`` is set."""
    import sys
    import traceback

    from berth.health import log_error

    try:
        build()
    except Exception:
        log_error(sys.stderr, "building berth-keys failed")
        Keeper(directory)._write("not built: see watch.err")
        return
    keeper = Keeper(directory)
    try:
        while not stop.is_set():
            keeper.tick()
            stop.wait(5)
    finally:
        keeper.stop()
