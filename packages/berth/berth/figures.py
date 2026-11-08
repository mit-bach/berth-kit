"""Write the status and runtime rows the screen draws.

The screen does not call cmux and does not read hook files. While it is open,
this process stores the child RSS and whether the watcher is up. Which live
sessions are busy is the watcher's (berth/activity.py). Nothing here
closes a workspace or changes ``berth.state``.
"""

from __future__ import annotations

from berth.catalog import ensure
from berth.db import Catalog
from berth.model import HookRecord
from berth.timeutil import utc_now


def refresh_figures(
    db: Catalog,
    *,
    rss_bytes: int | None,
    watcher: str,
    hooks: dict[str, HookRecord],
    pid_alive_fn,
) -> None:
    # Busy is the watcher's now: it reads each pane (berth/activity.py) into ``runtime``.
    del hooks, pid_alive_fn
    with db.immediate():
        ensure(db.conn)
        db.conn.execute(
            """
            INSERT INTO status (id, child_rss_bytes, watcher, sampled_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              child_rss_bytes = excluded.child_rss_bytes,
              watcher = excluded.watcher,
              sampled_at = excluded.sampled_at
            """,
            (rss_bytes, watcher, utc_now()),
        )
