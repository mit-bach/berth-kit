"""The tailnet offer adds folders. It does not close a workspace or replace a row."""

from __future__ import annotations

import json
import threading

from berth.db import Catalog
from berth.errors import BerthError
from berth.offer import fetch_snapshot, make_server, merge, plan, snapshot
from berth.ops import find_folder_path, mkdir_path
from tests.test_state import _insert

import pytest


def _source(tmp_path) -> Catalog:
    db = Catalog(tmp_path / "source.sqlite").open()
    db.ensure_inbox()
    mkdir_path(db, "Inbox/Bills", inbox_children=True)
    _insert(
        db,
        id="live-1",
        title="policy",
        state="live",
        kind="grok",
        cwd="/tmp/work",
        agent_name="grok",
        agent_session_id="sess-1",
        cmux_workspace_id="ws-1",
        folder_id=_folder(db, "Inbox/Bills"),
        argv=["grok", "-r", "sess-1"],
        display="grok -r sess-1",
    )
    _insert(
        db,
        id="screen-1",
        title="Berth",
        state="live",
        kind="berth",
        cwd="/tmp",
        cmux_workspace_id="ws-screen",
        argv=[],
        display="berth",
    )
    _set_color(db, "Inbox/Bills", "#16a085")
    return db


def _set_color(db: Catalog, path: str, color: str) -> None:
    with db.immediate():
        db.conn.execute("UPDATE folder SET color = ? WHERE id = ?", (color, find_folder_path(db, path)))


def _folder(db: Catalog, path: str) -> str:
    from berth.ops import find_folder_path

    found = find_folder_path(db, path)
    assert found is not None
    return found


def test_snapshot_exports_a_live_row_as_parked_without_the_workspace(tmp_path):
    db = _source(tmp_path)
    document = snapshot(db, host="source-mac")
    db.close()
    assert document["host"] == "source-mac"
    titles = [row["title"] for row in document["berths"]]
    assert titles == ["policy"]
    row = document["berths"][0]
    assert row["state"] == "parked"
    assert "cmux_workspace_id" not in row
    assert row["session_hint"] == "~/.grok/sessions/%2Ftmp%2Fwork/sess-1"
    assert any(item["path"] == "Inbox/Bills" and item["color"] == "#16a085" for item in document["folders"])


def test_merge_adds_the_chosen_folder_and_a_second_merge_keeps_it(catalog, tmp_path):
    source = _source(tmp_path)
    document = snapshot(source, host="source-mac")
    source.close()
    _insert(catalog, id="local-1", title="already", state="parked", kind="shell", cwd="/tmp")
    first = merge(catalog, document, folders=["Inbox/Bills"])
    assert first["add"] == 1
    assert first["added"] == ["live-1"]
    row = catalog.require_berth("live-1")
    assert row["state"] == "parked"
    assert row["cmux_workspace_id"] is None
    assert row["agent_session_id"] == "sess-1"
    assert catalog.require_berth("local-1")["title"] == "already"
    color = catalog.folder_by_id(_folder(catalog, "Inbox/Bills"))["color"]
    assert color == "#16a085"
    second = merge(catalog, document, folders=["Inbox/Bills"])
    assert second["add"] == 0
    assert second["keep"] == 1
    assert [item["id"] for item in catalog.all_berths() if item["id"] == "live-1"] == ["live-1"]


def test_merge_skips_a_session_id_that_is_already_here(catalog, tmp_path):
    source = _source(tmp_path)
    document = snapshot(source)
    source.close()
    _insert(
        catalog,
        id="other",
        title="same session",
        state="parked",
        kind="grok",
        agent_session_id="sess-1",
        cwd="/tmp/work",
    )
    report = merge(catalog, document, folders=["Inbox/Bills"])
    actions = {row["id"]: row["action"] for row in report["berths"]}
    assert actions["live-1"] == "session_kept"
    assert catalog.get_berth("live-1") is None
    assert catalog.require_berth("other")["title"] == "same session"


def test_merge_refuses_to_add_every_folder_unless_asked(catalog, tmp_path):
    source = _source(tmp_path)
    document = snapshot(source)
    source.close()
    with pytest.raises(BerthError) as caught:
        merge(catalog, document)
    assert "folders" in caught.value.message
    assert catalog.get_berth("live-1") is None


def test_an_unchosen_folder_is_not_added(catalog, tmp_path):
    source = _source(tmp_path)
    mkdir_path(source, "Inbox/Other", inbox_children=True)
    _insert(
        source,
        id="other-row",
        title="elsewhere",
        state="parked",
        kind="shell",
        cwd="/tmp",
        folder_id=_folder(source, "Inbox/Other"),
        argv=[],
        display="shell",
    )
    document = snapshot(source)
    source.close()
    merge(catalog, document, folders=["Inbox/Bills"])
    assert catalog.get_berth("live-1") is not None
    assert catalog.get_berth("other-row") is None


def test_a_local_color_is_not_replaced(catalog, tmp_path):
    source = _source(tmp_path)
    document = snapshot(source)
    source.close()
    mkdir_path(catalog, "Inbox/Bills", inbox_children=True)
    _set_color(catalog, "Inbox/Bills", "#336699")
    merge(catalog, document, folders=["Inbox/Bills"])
    assert catalog.folder_by_id(_folder(catalog, "Inbox/Bills"))["color"] == "#336699"


def test_preview_from_a_file_writes_nothing(catalog, tmp_path):
    source = _source(tmp_path)
    document = snapshot(source)
    source.close()
    path = tmp_path / "offer.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    report = plan(catalog, fetch_snapshot(str(path)), take_all=True)
    assert report["add"] == 1
    assert catalog.get_berth("live-1") is None


def test_the_offer_serves_the_snapshot(tmp_path):
    source = _source(tmp_path)
    source.close()
    server = make_server(tmp_path / "source.sqlite", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        document = fetch_snapshot(f"http://127.0.0.1:{port}/v1/snapshot")
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert document["berths"][0]["title"] == "policy"
    assert document["berths"][0]["state"] == "parked"
