"""Berth tool rules. These tests do not open the live socket or the live catalog."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from berth.db import Catalog
from berth.model import Workspace
from berth.timeutil import utc_now
from cmux_mcp.berth_api import BERTH_ROOT, Api, ToolError, _resume_ids, resolve_berth_root
from cmux_mcp.ops import CmuxOps


class Desk:
    def __init__(self) -> None:
        self.up = True
        self.created: list[dict] = []
        self.workspaces: list = []
        self.closed: list[str] = []

    def socket_up(self) -> bool:
        return self.up

    def list_workspaces(self) -> list:
        return list(self.workspaces)

    def memory(self) -> dict:
        return {"memory_diagnostic": {"children": {"recursive_rss_bytes": 6_100_000_000}}}

    def read_screen(self, workspace_id: str, *, lines: int = 120) -> str:
        return "quiet"

    def close_workspace(self, workspace_id: str) -> None:
        self.closed.append(workspace_id)

    def create_workspace(self, **kwargs):
        item = Workspace(
            id="ws-new",
            title=str(kwargs.get("title") or ""),
            cwd=str(kwargs.get("cwd") or ""),
            ref="workspace:9",
        )
        self.workspaces.append(item)
        self.created.append(kwargs)
        return item


def _insert(db: Catalog, **overrides) -> str:
    now = utc_now()
    berth_id = overrides.get("id", "berth-1")
    fields = {
        "id": berth_id,
        "folder_id": overrides.get("folder_id", db.ensure_inbox()),
        "title": overrides.get("title", "cedar"),
        "kind": overrides.get("kind", "grok"),
        "state": overrides.get("state", "parked"),
        "protect": overrides.get("protect", 0),
        "cwd": overrides.get("cwd", "/tmp"),
        "note": overrides.get("note", ""),
        "recipe_argv": json.dumps(overrides.get("argv", ["grok", "-r", "sess"])),
        "recipe_display": overrides.get("display", "grok -r sess"),
        "agent_name": overrides.get("agent_name", "grok"),
        "agent_session_id": overrides.get("agent_session_id", "sess-1"),
        "agent_version": None,
        "cmux_workspace_id": overrides.get("cmux_workspace_id"),
        "cmux_surface_id": None,
        "cmux_pid": None,
        "attachments": "{}",
        "excerpt": "",
        "excerpt_at": None,
        "created_at": now,
        "updated_at": now,
        "parked_at": now,
        "played_at": None,
    }
    with db.immediate():
        db.insert_berth(fields)
    return berth_id


class BerthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.id().replace(".", "_"))
        # Isolated under the system temp via TemporaryDirectory in each test.

    def _api(self, root: Path) -> tuple[Api, Desk, Catalog]:
        db = Catalog(root / "berth.sqlite").open()
        desk = Desk()
        api = Api(CmuxOps.__new__(CmuxOps), catalog=db.path, cmux=desk)  # type: ignore[arg-type]
        return api, desk, db

    def test_resume_flag_ignores_reasoning_effort(self) -> None:
        self.assertEqual(_resume_ids("grok --reasoning-effort xhigh"), [])
        self.assertEqual(_resume_ids("bash -lc 'exec grok --resume sess-9'"), ["sess-9"])
        self.assertEqual(_resume_ids("grok -r sess-9 --always-approve"), ["sess-9"])
        self.assertEqual(_resume_ids("grok --resume=sess-9"), ["sess-9"])

    def test_create_refuses_a_session_that_already_has_a_row(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            try:
                _insert(db, id="already", agent_session_id="sess-9", title="kept")
                with self.assertRaises(ToolError) as caught:
                    api.create(
                        {
                            "title": "again",
                            "cwd": str(root),
                            "command": "bash -lc 'exec grok --always-approve --resume sess-9'",
                        }
                    )
                self.assertEqual(caught.exception.code, "SESSION_IN_USE")
                self.assertEqual(caught.exception.details["matches"][0]["id"], "already")
                self.assertEqual(desk.created, [])
            finally:
                db.close()

    def test_list_after_walks_the_sorted_page(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, _desk, db = self._api(root)
            try:
                _insert(db, id="b", title="b")
                _insert(db, id="a", title="a")
                _insert(db, id="c", title="c")
                first = api.list_rows({"limit": 1, "state": "parked", "sort": "name"})
                self.assertEqual(first["total"], 3)
                self.assertEqual(first["rows"][0]["title"], "a")
                self.assertEqual(first["next"], "a")
                second = api.list_rows({"limit": 1, "state": "parked", "sort": "name", "after": first["next"]})
                self.assertEqual(second["total"], 3)
                self.assertEqual(second["rows"][0]["title"], "b")
            finally:
                db.close()

    def test_status_children_is_the_short_size(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, _desk, db = self._api(root)
            try:
                body = api.status({})
            finally:
                db.close()
        self.assertEqual(body["cmux"], "up")
        self.assertEqual(body["children"], "6.1 GB")

    def test_workspace_ref_is_not_a_berth_id(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, _desk, db = self._api(root)
            try:
                with self.assertRaises(ToolError) as caught:
                    api.get({"id": "workspace:98"})
            finally:
                db.close()
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_focus_reports_cmux_down_and_writes_nothing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            desk.up = False
            try:
                _insert(db, id="row", state="live", cmux_workspace_id="ws-1")
                with self.assertRaises(ToolError) as caught:
                    api.focus({"id": "row"})
                self.assertEqual(db.require_berth("row")["state"], "live")
            finally:
                db.close()
        self.assertEqual(caught.exception.code, "CMUX_DOWN")

    def test_berth_root_is_the_sibling_package(self) -> None:
        self.assertTrue((BERTH_ROOT / "berth" / "__init__.py").is_file())
        self.assertNotIn("olduser", str(BERTH_ROOT))

    def test_rmdir_moves_berths_to_inbox_and_closes_nothing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            try:
                made = api.mkdir({"path": "Home-Notes"})
                berth_id = _insert(
                    db,
                    id="row",
                    title="cedar",
                    folder_id=made["id"],
                    state="live",
                    cmux_workspace_id="ws-cash",
                )
                removed = api.rmdir({"path": "Home-Notes"})
                self.assertEqual(removed["deleted"], "Home-Notes")
                self.assertEqual(removed["to"], "Inbox")
                self.assertEqual(removed["moved"], 1)
                row = db.require_berth(berth_id)
                self.assertEqual(row["folder_id"], db.ensure_inbox())
                self.assertEqual(row["state"], "live")
                self.assertEqual(row["cmux_workspace_id"], "ws-cash")
                self.assertIsNone(db.folder_by_id(made["id"]))
                with self.assertRaises(ToolError) as caught:
                    api.rmdir({"path": "Inbox"})
                self.assertEqual(caught.exception.code, "FOLDER_NONEMPTY")
            finally:
                db.close()
        self.assertEqual(desk.closed, [])

    def test_move_files_the_row(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, _desk, db = self._api(root)
            try:
                api.mkdir({"path": "Home-Notes"})
                berth_id = _insert(db, id="row", title="cedar")
                view = api.move({"id": berth_id, "folder": "Home-Notes"})
                self.assertEqual(view["folder"], "Home-Notes")
                self.assertEqual(view["id"], "row")
            finally:
                db.close()

    def test_pause_ignores_the_protect_mark(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            desk.workspaces = [Workspace(id="ws-1", title="cedar", cwd="/tmp")]
            try:
                _insert(
                    db,
                    id="row",
                    state="live",
                    protect=1,
                    cmux_workspace_id="ws-1",
                    kind="shell",
                    agent_name=None,
                    agent_session_id=None,
                    argv=[],
                    display="shell",
                )
                view = api.pause({"id": "row"})
                self.assertEqual(view["state"], "parked")
                self.assertEqual(desk.closed, ["ws-1"])
                self.assertEqual(db.require_berth("row")["protect"], 1)
            finally:
                db.close()

    def test_workspace_uuid_resolves_to_the_live_row(self) -> None:
        import tempfile

        workspace_id = "AABBCCDD-1111-2222-3333-444455556666"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            desk.workspaces = [Workspace(id=workspace_id, title="cedar", cwd="/tmp", ref="workspace:4")]
            try:
                _insert(db, id="row", title="cedar", state="live", cmux_workspace_id=workspace_id)
                by_uuid = api.get({"id": workspace_id.lower()})
                by_ref = api.get({"id": "workspace:4"})
                self.assertEqual(by_uuid["id"], "row")
                self.assertEqual(by_ref["id"], "row")
            finally:
                db.close()

    def test_two_live_rows_for_one_workspace_are_not_guessed(self) -> None:
        import tempfile

        workspace_id = "AABBCCDD-1111-2222-3333-444455556666"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            desk.workspaces = [Workspace(id=workspace_id, title="cedar", cwd="/tmp", ref="workspace:4")]
            try:
                _insert(db, id="one", state="live", cmux_workspace_id=workspace_id)
                _insert(db, id="two", title="other", state="live", cmux_workspace_id=workspace_id)
                with self.assertRaises(ToolError) as caught:
                    api.get({"id": "workspace:4"})
                self.assertEqual(caught.exception.code, "NOT_FOUND")
                self.assertIn("more than one", str(caught.exception))
            finally:
                db.close()

    def test_pause_refuses_the_caller_workspace(self) -> None:
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            desk.workspaces = [Workspace(id="ws-1", title="cedar", cwd="/tmp")]
            try:
                _insert(
                    db,
                    id="row",
                    state="live",
                    protect=0,
                    cmux_workspace_id="ws-1",
                    kind="shell",
                    agent_name=None,
                    agent_session_id=None,
                    argv=[],
                    display="shell",
                )
                with patch.dict(os.environ, {"CMUX_WORKSPACE_ID": "ws-1"}):
                    with self.assertRaises(ToolError) as caught:
                        api.pause({"id": "row"})
                self.assertEqual(caught.exception.code, "PROTECTED")
                self.assertEqual(desk.closed, [])
                self.assertEqual(db.require_berth("row")["state"], "live")
            finally:
                db.close()

    def test_create_files_the_new_row_into_the_named_folder(self) -> None:
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            try:
                api.mkdir({"path": "Home-Notes"})
                inbox = db.ensure_inbox()

                def fake_adopt(catalog, _cmux, _hooks, workspace_id, **_kwargs):
                    _insert(
                        catalog,
                        id="created",
                        folder_id=inbox,
                        title="judge",
                        state="live",
                        cmux_workspace_id=workspace_id,
                        kind="shell",
                        agent_name=None,
                        agent_session_id=None,
                        argv=[],
                        display="shell",
                    )
                    return catalog.require_berth("created")

                with patch("cmux_mcp.berth_api.adopt_appearing", side_effect=fake_adopt):
                    view = api.create(
                        {
                            "title": "judge",
                            "cwd": str(root),
                            "command": "echo hi",
                            "folder": "Home-Notes",
                            "focus": False,
                        }
                    )
                self.assertEqual(view["id"], "created")
                self.assertEqual(view["folder"], "Home-Notes")
                self.assertEqual(desk.created[0]["title"], "judge")
            finally:
                db.close()

    def test_missing_berth_root_falls_back_to_the_repo(self) -> None:
        missing = "/Users/olduser/projects/GROK/GROK-Workspace/cmux-dev/cmux-management/engineers/linden"
        found = resolve_berth_root({"BERTH_ROOT": missing})
        self.assertIsNotNone(found)
        self.assertTrue((found / "berth" / "__init__.py").is_file())
        self.assertNotIn("olduser", str(found))

    def test_berth_root_override_wins_when_the_tree_exists(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "tree"
            (root / "berth").mkdir(parents=True)
            (root / "berth" / "__init__.py").write_text("")
            self.assertEqual(resolve_berth_root({"BERTH_ROOT": str(root)}), root)

    def test_merge_adds_a_named_folder_and_closes_nothing(self) -> None:
        import tempfile

        from berth.offer import snapshot
        from berth.ops import mkdir_path

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            api, desk, db = self._api(root)
            source = Catalog(root / "source.sqlite").open()
            try:
                source.ensure_inbox()
                mkdir_path(source, "Inbox/Bills", inbox_children=True)  # an older catalog
                folder_id = next(row["id"] for row in source.all_folders() if row["name"] == "Bills")
                _insert(
                    source,
                    id="remote-1",
                    title="policy",
                    folder_id=folder_id,
                    state="live",
                    cmux_workspace_id="ws-remote",
                    agent_session_id="sess-remote",
                )
                path = root / "offer.json"
                path.write_text(json.dumps(snapshot(source, host="other")), encoding="utf-8")
                with self.assertRaises(ToolError) as caught:
                    api("merge", {"from": str(path)})
                self.assertEqual(caught.exception.code, "NOT_FOUND")
                report = api("merge", {"from": str(path), "folders": ["Inbox/Bills"]})
                self.assertEqual(report["added"], ["remote-1"])
                row = db.require_berth("remote-1")
                self.assertEqual(row["state"], "parked")
                self.assertIsNone(row["cmux_workspace_id"])
                self.assertEqual(desk.closed, [])
            finally:
                source.close()
                db.close()

    def test_folders_nest_move_and_rename_like_the_screen(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            api, _desk, db = self._api(Path(raw))
            try:
                made = api("mkdir", {"path": "Work/Tutors/Prime"})
                self.assertEqual(made["created"], ["Work", "Work/Tutors", "Work/Tutors/Prime"])
                self.assertEqual(api("mkdir", {"path": "Work/Tutors"})["created"], [])
                with self.assertRaises(ToolError) as caught:
                    api("mkdir", {"path": "Inbox/Bills"})
                self.assertEqual(caught.exception.code, "BAD_ARGUMENT")
                api("mkdir", {"path": "Archive"})
                moved = api("move_folder", {"path": "Work/Tutors", "into": "Archive"})
                self.assertEqual((moved["from"], moved["path"]), ("Work/Tutors", "Archive/Tutors"))
                self.assertEqual(api("move_folder", {"path": "Archive/Tutors"})["path"], "Tutors")
                with self.assertRaises(ToolError) as caught:
                    api("move_folder", {"path": "Tutors", "into": "Tutors/Prime"})
                self.assertEqual(caught.exception.code, "BAD_ARGUMENT")
                with self.assertRaises(ToolError) as caught:
                    api("move_folder", {"path": "Inbox", "into": "Work"})
                self.assertEqual(caught.exception.code, "PROTECTED")
                renamed = api("rename_folder", {"path": "Tutors", "name": "Teachers"})
                self.assertEqual(renamed["path"], "Teachers")
                paths = [item["path"] for item in api("folders", {})["folders"]]
                self.assertIn("Teachers/Prime", paths)
                with self.assertRaises(ToolError) as caught:
                    api("rename_folder", {"path": "Teachers", "name": "Work"})
                self.assertEqual(caught.exception.code, "FOLDER_EXISTS")
            finally:
                db.close()

    def test_list_is_newest_first_with_agent_and_park_reason(self) -> None:
        import tempfile

        from berth.ops import mkdir_path

        with tempfile.TemporaryDirectory() as raw:
            api, _desk, db = self._api(Path(raw))
            try:
                parent = mkdir_path(db, "Work")["id"]
                child = mkdir_path(db, "Work/Tutors")["id"]
                _insert(db, id="old", title="old", folder_id=parent)
                _insert(db, id="new", title="new", folder_id=child, kind="other-agent", agent_name="claude")
                with db.immediate():
                    db.conn.execute("UPDATE berth SET last_used_at = '2026-10-01T00:00:00Z' WHERE id = 'old'")
                    db.conn.execute("UPDATE berth SET last_used_at = '2026-10-20T00:00:00Z', park_reason = 'cmux_quit' WHERE id = 'new'")
                rows = api("list", {})["rows"]
                self.assertEqual([row["id"] for row in rows], ["new", "old"])
                self.assertEqual((rows[0]["agent"], rows[0]["park_reason"]), ("claude", "cmux_quit"))
                self.assertEqual([row["id"] for row in api("list", {"agent": "claude"})["rows"]], ["new"])
                self.assertEqual([row["id"] for row in api("list", {"park_reason": "cmux_quit"})["rows"]], ["new"])
                self.assertEqual([row["id"] for row in api("list", {"folder": "Work"})["rows"]], ["old"])
                both = api("list", {"folder": "Work", "subfolders": True})["rows"]
                self.assertEqual([row["id"] for row in both], ["new", "old"])
                with db.immediate():
                    db.conn.execute("""UPDATE berth SET attachments = '{"fork_of": "sess-0"}' WHERE id = 'new'""")
                detail = api("get", {"id": "new"})
                self.assertEqual((detail["fork_of"], detail["recipe"]), ("sess-0", "grok -r sess"))
            finally:
                db.close()

    def test_park_folder_plans_first_then_parks_the_whole_tree(self) -> None:
        import tempfile

        from berth.ops import mkdir_path

        with tempfile.TemporaryDirectory() as raw:
            api, desk, db = self._api(Path(raw))
            try:
                parent = mkdir_path(db, "Work")["id"]
                child = mkdir_path(db, "Work/Tutors")["id"]
                for berth_id, folder in (("a", parent), ("b", child)):
                    _insert(db, id=berth_id, title=berth_id, folder_id=folder, state="live", cmux_workspace_id=f"ws-{berth_id}")
                    desk.workspaces.append(Workspace(id=f"ws-{berth_id}", title=berth_id, cwd="/tmp", ref=f"workspace:{berth_id}"))
                # The live screen row is a shell titled Berth, as the watcher files it.
                _insert(db, id="screen", title="Berth", kind="shell", folder_id=child, state="live", cmux_workspace_id="ws-s")
                desk.workspaces.append(Workspace(id="ws-s", title="Berth", cwd="/tmp", ref="workspace:s"))
                plan = api("park_folder", {"path": "Work"})
                self.assertFalse(plan["confirmed"])
                self.assertEqual(sorted(item["id"] for item in plan["would_park"]), ["a", "b"])
                self.assertEqual(desk.closed, [])
                done = api("park_folder", {"path": "Work", "confirm": True})
                self.assertEqual(sorted(item["id"] for item in done["paused"]), ["a", "b"])
                self.assertEqual(sorted(desk.closed), ["ws-a", "ws-b"])
                self.assertEqual(db.require_berth("screen")["state"], "live")
                with self.assertRaises(ToolError) as caught:
                    api("delete", {"id": "screen", "confirm": True})
                self.assertIn(caught.exception.code, {"KIND_BERTH", "PROTECTED"})
            finally:
                db.close()

    def test_delete_needs_confirm_and_keeps_nothing_else(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            api, _desk, db = self._api(Path(raw))
            try:
                _insert(db, id="gone", title="gone")
                with self.assertRaises(ToolError) as caught:
                    api("delete", {"id": "gone"})
                self.assertEqual(caught.exception.code, "BAD_ARGUMENT")
                self.assertIsNotNone(db.get_berth("gone"))
                removed = api("delete", {"id": "gone", "confirm": True})
                self.assertEqual((removed["state"], removed["folder"]), ("deleted", "Inbox"))
                self.assertIsNone(db.get_berth("gone"))
            finally:
                db.close()

    def test_check_says_cmux_down_without_writing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            api, desk, db = self._api(Path(raw))
            try:
                desk.up = False
                self.assertEqual(api("check", {})["cmux"], "down")
            finally:
                db.close()

    def test_sleep_plans_then_sleeps_everything_but_the_screen(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            api, desk, db = self._api(Path(raw))
            try:
                for berth_id, title in (("a", "a"), ("b", "b"), ("screen", "Berth")):
                    _insert(db, id=berth_id, title=title, state="live", cmux_workspace_id=f"ws-{berth_id}",
                            kind="shell" if title == "Berth" else "grok")
                    desk.workspaces.append(Workspace(id=f"ws-{berth_id}", title=title, cwd="/tmp", ref=f"workspace:{berth_id}"))
                plan = api("sleep", {"all": True})
                self.assertFalse(plan["confirmed"])
                self.assertEqual(sorted(item["id"] for item in plan["would_sleep"]), ["a", "b"])
                self.assertEqual(desk.closed, [])
                done = api("sleep", {"all": True, "confirm": True})
                self.assertEqual(sorted(item["id"] for item in done["slept"]), ["a", "b"])
                self.assertEqual(api("get", {"id": "a"})["state"], "sleeping")
                self.assertEqual(db.require_berth("screen")["state"], "live")
                self.assertEqual({row["id"] for row in api("list", {"state": "sleeping"})["rows"]}, {"a", "b"})
                self.assertEqual(api("list", {"state": "parked"})["rows"], [])
                self.assertEqual(api("status", {})["sleeping"], 2)
                both = {row["id"] for row in api("list", {"state": "live,sleeping"})["rows"]}
                self.assertEqual(both, {"a", "b", "screen"})
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()


class InputLooksFirstTests(unittest.TestCase):
    """2026-10-30: a prompt typed into a working session, or into an open question, without looking."""

    WORKING = "⏺ Reading the files…\n\n" + "─" * 60 + " W ─\n❯ \n" + "─" * 70 + "\n  ⏵⏵ auto mode on · esc to interrupt\n"
    READY = "⏺ Done.\n\n" + "─" * 60 + " W ─\n❯ \n" + "─" * 70 + "\n  ⏵⏵ auto mode on (shift+tab to cycle)\n"

    def _setup(self, root: Path, screen: str):
        class Pane(Desk):
            def read_screen(self, workspace_id: str, *, lines: int = 120, scrollback: bool = True) -> str:
                return screen

        class Ops:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def input_bytes(self, **kwargs):
                self.calls.append(kwargs)
                return {"target": kwargs["target"], "submitted": kwargs["submit"]}

        db = Catalog(root / "berth.sqlite").open()
        desk, ops = Pane(), Ops()
        desk.workspaces = [Workspace(id="ws-w", title="worker", cwd="/tmp", ref="workspace:3")]
        _insert(db, id="worker", title="worker", kind="claude", agent_name="claude", state="live", cmux_workspace_id="ws-w")
        return Api(ops, catalog=db.path, cmux=desk), ops, db  # type: ignore[arg-type]

    def test_a_prompt_into_a_working_session_is_refused_with_what_it_is_doing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            api, ops, db = self._setup(Path(raw), self.WORKING)
            try:
                with self.assertRaises(ToolError) as caught:
                    api("input", {"id": "worker", "target": "prompt", "text": "next job", "submit": True})
                self.assertEqual(caught.exception.code, "BUSY")
                self.assertEqual(caught.exception.details["status"], "working")
                self.assertEqual(ops.calls, [])
                api("input", {"id": "worker", "target": "prompt", "key": "escape"})  # keys are never checked
                api("input", {"id": "worker", "target": "prompt", "text": "next job", "submit": True, "force": True})
                self.assertEqual(len(ops.calls), 2)
            finally:
                db.close()

    def test_a_ready_session_takes_the_prompt(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            api, ops, db = self._setup(Path(raw), self.READY)
            try:
                api("input", {"id": "worker", "target": "prompt", "text": "next job", "submit": True})
                self.assertEqual(len(ops.calls), 1)
            finally:
                db.close()


class PastedSlashCommandTests(InputLooksFirstTests):
    """2026-10-30: STUDIO Orch pasted "/compact <note>"; Claude sent it as a plain message and the fork built the thing."""

    def test_a_file_that_starts_with_a_slash_is_refused_with_the_way_to_compact(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            api, ops, db = self._setup(Path(raw), self.READY)
            note = Path(raw) / "compact.md"
            note.write_text("\n/compact Keep the build context.\n\nLet go of the rest.\n", encoding="utf-8")
            plain = Path(raw) / "job.md"
            plain.write_text("Build the launcher. Paths like /tmp/x are fine inside.\n", encoding="utf-8")
            try:
                with self.assertRaises(ToolError) as caught:
                    api("input", {"id": "worker", "target": "prompt", "path": str(note)})
                self.assertEqual(caught.exception.code, "BAD_ARGUMENT")
                self.assertIn("compact_file", caught.exception.details["hint"])
                self.assertEqual(ops.calls, [])
                api("input", {"id": "worker", "target": "prompt", "path": str(plain)})
                api("input", {"id": "worker", "target": "prompt", "text": "/compact keep it short"})  # typed: runs
                self.assertEqual(len(ops.calls), 2)
            finally:
                db.close()
