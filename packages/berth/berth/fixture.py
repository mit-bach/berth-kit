"""Build a desk catalog and a 42-berth catalog. Session ids are fixture-session-*."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from berth.schema import CONTRACT_DDL, RUNTIME_SQL, STATUS_SQL

OLD = "2026-10-01T15:00:00Z"
NEW = "2026-10-02T21:23:00Z"
STORED = "2026-10-02T16:40:00Z"

MARK_EXCERPT = "\n".join(
    [
        "split the sample on the mark",
        "rows 1-40 stay the control",
        "the mark is the blank line",
        "waiting at the prompt",
    ]
)

SIGNER_EXCERPT = "\n".join(
    [
        "tool call still open",
        "the signer is waiting on a card",
        "the turn has not finished",
    ]
)

BERTH_COLUMNS = (
    "id",
    "folder_id",
    "title",
    "kind",
    "state",
    "protect",
    "cwd",
    "note",
    "recipe_argv",
    "recipe_display",
    "agent_name",
    "agent_session_id",
    "agent_version",
    "cmux_workspace_id",
    "cmux_surface_id",
    "cmux_pid",
    "attachments",
    "excerpt",
    "excerpt_at",
    "created_at",
    "updated_at",
    "parked_at",
    "played_at",
)


def build_contract(path: Path | str) -> sqlite3.Connection:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    if file.exists():
        file.unlink()
    conn = sqlite3.connect(file)
    conn.executescript(CONTRACT_DDL)
    conn.commit()
    return conn


def build_desk(path: Path | str) -> Path:
    file = Path(path)
    conn = build_contract(file)
    conn.execute(STATUS_SQL)
    conn.execute(RUNTIME_SQL)
    insert_folder(conn, "folder-course", None, "Course", 0)
    insert_folder(conn, "folder-agents", None, "Agent Systems", 1)
    insert_folder(conn, "folder-inbox", None, "Inbox", 2, note="not in a folder")
    week = "/desk/course"
    insert_berth(
        conn,
        id="berth-sample",
        folder_id="folder-course",
        title="sample-split",
        state="live",
        cwd=week,
        note="split the sample on the mark",
        session="fixture-session-sample",
        version="1.0.46",
        workspace="fixture-workspace-sample",
        created_at=OLD,
        played_at=OLD,
        parked_at=None,
    )
    insert_berth(
        conn,
        id="berth-mark",
        folder_id="folder-course",
        title="mark-finder",
        state="parked",
        cwd=week,
        note="waiting at the prompt",
        session="fixture-session-mark",
        version="1.0.44",
        excerpt=MARK_EXCERPT,
        excerpt_at=STORED,
        created_at=OLD,
        parked_at=STORED,
        played_at=OLD,
    )
    insert_berth(
        conn,
        id="berth-method",
        folder_id="folder-course",
        title="method-draft",
        state="parked",
        cwd="/desk/course/signal-bench",
        note="draft the methods paragraph",
        session="fixture-session-method",
        version="1.0.44",
        created_at=OLD,
        parked_at=OLD,
    )
    insert_berth(
        conn,
        id="berth-margin-week",
        folder_id="folder-course",
        title="margin-notes",
        state="parked",
        cwd=week,
        note="margin comments on the draft",
        session="fixture-session-margin-week",
        version="1.0.41",
        created_at=OLD,
        parked_at=OLD,
    )
    insert_berth(
        conn,
        id="berth-signer",
        folder_id="folder-agents",
        title="signer-lab",
        state="live",
        cwd="/desk/agents",
        note="mid-tool on the signer",
        session="fixture-session-signer",
        version="1.0.46",
        workspace="fixture-workspace-signer",
        excerpt=SIGNER_EXCERPT,
        excerpt_at=STORED,
        created_at=OLD,
        played_at=OLD,
        parked_at=None,
    )
    insert_berth(
        conn,
        id="berth-lexicon",
        folder_id="folder-agents",
        title="lexicon-run",
        state="parked",
        protect=1,
        cwd="/desk/agents",
        note="leave this session",
        session="fixture-session-lexicon",
        version="1.0.44",
        created_at=OLD,
        parked_at=OLD,
    )
    insert_berth(
        conn,
        id="berth-idle",
        folder_id="folder-agents",
        title="idle-map",
        state="parked",
        cwd="/desk/agents",
        note="idle map is a later repair",
        session="fixture-session-idle",
        version="1.0.41",
        created_at=OLD,
        parked_at=OLD,
    )
    insert_berth(
        conn,
        id="berth-fresh",
        folder_id="folder-inbox",
        title="fresh-shell",
        kind="shell",
        state="live",
        cwd="/desk/inbox",
        note="adopted from the sidebar",
        recipe_argv=json.dumps(["zsh"]),
        recipe_display="zsh",
        agent_name=None,
        session=None,
        version=None,
        workspace="fixture-workspace-fresh",
        created_at=NEW,
        updated_at=NEW,
        played_at=NEW,
        parked_at=None,
    )
    insert_berth(
        conn,
        id="berth-margin-inbox",
        folder_id="folder-inbox",
        title="margin-notes",
        state="parked",
        cwd="/desk/inbox",
        note="loose capture",
        session="fixture-session-margin-inbox",
        version="1.0.46",
        created_at=OLD,
        parked_at=OLD,
    )
    insert_berth(
        conn,
        id="berth-paper",
        folder_id="folder-inbox",
        title="paper-queue",
        state="parked",
        cwd="/desk/inbox",
        note="a paper queue",
        session="fixture-session-paper",
        version="1.0.46",
        created_at=OLD,
        parked_at=OLD,
    )
    insert_berth(
        conn,
        id="berth-hw1",
        folder_id="folder-inbox",
        title="6.S978 HW1",
        kind="shell",
        state="parked",
        cwd="/desk/inbox",
        note="shell anchor",
        recipe_argv=json.dumps(["zsh", "-l"]),
        recipe_display="zsh -l",
        agent_name=None,
        session=None,
        version=None,
        created_at=OLD,
        parked_at=OLD,
    )
    insert_berth(
        conn,
        id="berth-screen",
        folder_id="folder-inbox",
        title="Berth",
        kind="berth",
        state="live",
        cwd="/desk/inbox",
        note="this screen",
        recipe_argv="[]",
        recipe_display="",
        agent_name=None,
        session=None,
        version=None,
        workspace="fixture-workspace-berth",
        created_at=OLD,
        played_at=OLD,
        parked_at=None,
    )
    conn.execute(
        "INSERT INTO status (id, child_rss_bytes, watcher, sampled_at) VALUES (1, ?, ?, ?)",
        (7_100_000_000, "up", "2026-10-02T21:00:00Z"),
    )
    conn.execute("INSERT INTO runtime (berth_id, busy) VALUES ('berth-signer', 1)")
    _finish(file, conn)
    return file


def build_scale(path: Path | str) -> Path:
    file = Path(path)
    conn = build_contract(file)
    conn.execute(RUNTIME_SQL)
    insert_folder(conn, "work", None, "Work", 0)
    leaves: list[tuple[str, int]] = []
    for mid_index, mid in enumerate(("alpha", "beta", "gamma")):
        mid_id = f"mid-{mid}"
        insert_folder(conn, mid_id, "work", mid, mid_index)
        for leaf_index, leaf in enumerate(("one", "two")):
            leaf_id = f"leaf-{mid}-{leaf}"
            insert_folder(conn, leaf_id, mid_id, leaf, leaf_index)
            leaves.append((leaf_id, 0))
    number = 1
    live = {1, 8, 10, 20, 30}
    for leaf_id, _ in leaves:
        for _ in range(7):
            title = f"item-{number:03d}"
            insert_berth(
                conn,
                id=title,
                folder_id=leaf_id,
                title=title,
                state="live" if number in live else "parked",
                protect=1 if number == 5 else 0,
                cwd=f"/desk/work/{leaf_id}",
                note="scale row",
                session=f"fixture-session-{title}",
                version="1.0.46",
                workspace=f"fixture-workspace-{title}" if number in live else None,
                created_at=OLD,
                parked_at=None if number in live else OLD,
                played_at=OLD if number in live else None,
            )
            number += 1
    conn.execute("INSERT INTO runtime (berth_id, busy) VALUES ('item-008', 1)")
    _finish(file, conn)
    return file


def insert_folder(
    conn: sqlite3.Connection,
    folder_id: str,
    parent_id: str | None,
    name: str,
    sort: int,
    *,
    archived: int = 0,
    note: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO folder (id, parent_id, name, sort, profile_id, default_cwd, note, archived)
        VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (folder_id, parent_id, name, sort, note, archived),
    )


def insert_berth(conn: sqlite3.Connection, *, id: str, folder_id: str, title: str, **overrides) -> None:
    session = overrides.pop("session", f"fixture-session-{id}")
    version = overrides.pop("version", "1.0.46")
    workspace = overrides.pop("workspace", None)
    recipe_argv = overrides.pop("recipe_argv", None)
    recipe_display = overrides.pop("recipe_display", None)
    if recipe_argv is None:
        recipe_argv = json.dumps(["grok", "-r", session or "fixture-session"])
        recipe_display = recipe_display or f"grok -r {session or 'fixture-session'}"
    row = {
        "id": id,
        "folder_id": folder_id,
        "title": title,
        "kind": "grok",
        "state": "parked",
        "protect": 0,
        "cwd": "/desk",
        "note": "",
        "recipe_argv": recipe_argv,
        "recipe_display": recipe_display or "",
        "agent_name": "grok" if session else None,
        "agent_session_id": session,
        "agent_version": version,
        "cmux_workspace_id": workspace,
        "cmux_surface_id": None,
        "cmux_pid": None,
        "attachments": "{}",
        "excerpt": "",
        "excerpt_at": None,
        "created_at": OLD,
        "updated_at": overrides.get("created_at", OLD),
        "parked_at": OLD,
        "played_at": None,
    }
    row.update(overrides)
    if row["updated_at"] == OLD and row["created_at"] != OLD:
        row["updated_at"] = row["created_at"]
    columns = BERTH_COLUMNS
    conn.execute(
        f"INSERT INTO berth ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        [row[name] for name in columns],
    )


def _finish(path: Path, conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.close()
    os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="berth.fixture")
    parser.add_argument("which", choices=("desk", "scale"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    path = Path(args.out)
    if args.which == "desk":
        build_desk(path)
    else:
        build_scale(path)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
