"""Reopen the sessions a cmux quit closed.

After a restart the observer closes the panes cmux restored (their agents are
idle) and hands the sessions that were open to ``Restorer``. Each one is
played exactly as Enter plays it in the screen: ``play_berth``, so Claude
resumes the conversation Claude says it is on and Grok resumes its session,
each in its own workspace in its folder. Nothing takes focus. A process row
is not rerun without a person; it stays auto-parked. A failure leaves the row
auto-parked and writes a ``restore_failed`` event.

``berth restore`` does the same, now, for the last quit's sessions.
"""

from __future__ import annotations

import queue
import sys
import threading
import traceback
from pathlib import Path

from berth.db import Catalog
from berth.errors import BerthError
from berth.observer import QUIT_WINDOW_SECONDS, is_screen
from berth.timeutil import parse_time


def last_quit(db: Catalog) -> list[str]:
    """Sessions parked by the most recent cmux quit, the screen excepted."""
    rows = db.conn.execute(
        "SELECT * FROM berth WHERE state = 'parked' AND park_reason = 'cmux_quit' AND parked_at IS NOT NULL"
    ).fetchall()
    if not rows:
        return []
    newest = max(parse_time(row["parked_at"]) for row in rows)
    return [
        row["id"]
        for row in sorted(rows, key=lambda item: item["last_used_at"] or "", reverse=True)
        if not is_screen(row) and (newest - parse_time(row["parked_at"])).total_seconds() <= QUIT_WINDOW_SECONDS
    ]


def restore_rows(db: Catalog, cmux, ids: list[str], *, play=None) -> list[dict]:
    """Play each row that is still auto-parked. One at a time, no focus."""
    if play is None:
        from berth.state import play_berth as play
    results = []
    for berth_id in ids:
        row = db.get_berth(berth_id)
        if row is None or row["state"] != "parked" or row["park_reason"] != "cmux_quit":
            continue
        if row["kind"] == "process":
            results.append({"berth_id": berth_id, "result": "skipped", "why": "a process reruns only by hand"})
            continue
        try:
            play(db, cmux, berth_id, focus=False)
        except BerthError as exc:
            with db.immediate():
                db.insert_event(berth_id, "restore_failed", {"code": exc.code, "message": exc.message})
            results.append({"berth_id": berth_id, "result": "failed", "code": exc.code})
            continue
        results.append({"berth_id": berth_id, "result": "restored"})
    return results


class Restorer:
    """One worker thread with its own catalog connection. The observer queues ids."""

    def __init__(self, catalog_path: Path, cmux) -> None:
        self.catalog_path = catalog_path
        self.cmux = cmux
        self.jobs: queue.Queue[list[str]] = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def __call__(self, ids: list[str]) -> None:
        self.jobs.put(list(ids))

    def _run(self) -> None:
        db = Catalog(self.catalog_path).open()
        try:
            while True:
                ids = self.jobs.get()
                try:
                    for result in restore_rows(db, self.cmux, ids):
                        print(f"restore {result['berth_id']} {result['result']}", flush=True)
                except Exception:
                    traceback.print_exc(file=sys.stderr)
        finally:
            db.close()
