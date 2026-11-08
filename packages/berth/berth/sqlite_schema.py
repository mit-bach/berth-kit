"""Schema steps that are safe when two connections take them at once (2026-11-07).

Every opener checks the schema and adds what is missing: a table, a column. Two threads of the shared MCP server
(berth/mcp_http.py), or the watcher and a CLI right after an upgrade, can both see a column missing and both add
it; the second failed with "duplicate column name" or "table … already exists", and a status call answered
"catalog unreadable" (Kit Agent's flaky test, 2026-11-07). The step either happened here or just happened
there; both leave the schema right.
"""

from __future__ import annotations

import sqlite3


def schema_step(conn, sql: str) -> None:
    """One ``CREATE TABLE`` or ``ALTER TABLE … ADD COLUMN``; a concurrent opener having done it first is fine."""
    try:
        conn.execute(sql)
    except sqlite3.OperationalError as exc:
        text = str(exc).lower()
        if "already exists" in text or "duplicate column name" in text:
            return
        raise
