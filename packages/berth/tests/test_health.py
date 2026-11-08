"""Each watcher job on its own, and who runs old code (berth/health.py, 2026-10-31)."""

from __future__ import annotations

import io
import json
import os

import berth.health as health
from berth.health import Jobs, register, report


def test_a_failing_job_never_stops_the_jobs_after_it_and_is_logged_once(tmp_path):
    """Before, the watcher's tick was one try: a broken pin sync skipped deliveries every tick."""
    log = io.StringIO()
    jobs = Jobs(tmp_path, clock=lambda: 1000.0, stream=log)
    ran = []

    def broken():
        raise RuntimeError("pins broke")

    for _ in range(3):
        jobs.run("pins", broken)
        jobs.run("delivery", lambda: ran.append("delivery") or ["typed"])
    assert ran == ["delivery"] * 3  # deliveries kept running
    assert log.getvalue().count("pins failed: RuntimeError: pins broke") == 1  # once, not every tick
    jobs.write()
    written = json.loads((tmp_path / "watch-health.json").read_text())
    assert written["jobs"]["pins"]["failures"] == 3 and written["jobs"]["delivery"]["error"] is None
    assert jobs.run("pins", lambda: True) is True and not jobs.failing()  # recovers


def test_status_names_failing_jobs_and_whoever_runs_old_code(catalog, tmp_path, monkeypatch):
    monkeypatch.setattr(health, "_unregistered", lambda *args, **kw: [])  # not this Mac's processes
    directory = catalog.path.parent
    jobs = Jobs(directory, stream=io.StringIO())
    jobs.run("sidebar", lambda: 1 / 0)
    jobs.write()
    register(directory, "screen")  # this process, as if it were a screen started on older code
    monkeypatch.setattr(health, "code_stamp", lambda fresh=False: "newer-code")
    seen = report(catalog, directory)
    assert seen["watcher_ticking"] is True
    assert "ZeroDivisionError" in seen["failing_jobs"]["sidebar"]["error"]
    [old] = seen["old_code"]
    assert old["kind"] == "screen" and old["pid"] == os.getpid() and "q in the Berth screen" in old["fix"]
    monkeypatch.setattr(health, "code_stamp", lambda fresh=False: health.STARTED_WITH)
    assert report(catalog, directory)["old_code"] == []  # current code: nothing to restart


def test_a_dead_process_is_forgotten(catalog, monkeypatch):
    monkeypatch.setattr(health, "_unregistered", lambda *args, **kw: [])  # not this Mac's processes
    directory = catalog.path.parent
    folder = directory / "processes"
    folder.mkdir(exist_ok=True)
    (folder / "999999.json").write_text(json.dumps({"pid": 999999, "kind": "mcp", "code": "old"}))
    assert report(catalog, directory)["old_code"] == [] and not (folder / "999999.json").exists()


def test_the_screen_header_says_when_its_code_is_old(desk, monkeypatch):
    from tests.support import make_app

    monkeypatch.setattr(health, "changed_since_start", lambda: True)
    app = make_app(desk, width=140)
    assert "new code: q, then berth" in app.text().splitlines()[0]


def test_berth_processes_from_before_registering_are_found_by_their_command(catalog, monkeypatch):
    table = "\n".join([
        "  111   110 /Users/you/FORGE/systems/agents/berth/.venv/bin/python /Users/you/.local/bin/berth mcp",
        "  222     1 /Users/you/FORGE/systems/agents/berth/.venv/bin/python /Users/you/.local/bin/berth",
        "  333     1 /Users/you/FORGE/systems/agents/berth/.venv/bin/python /Users/you/.local/bin/berth watch",
        "  444     1 /bin/zsh",
    ])
    monkeypatch.setattr(health, "_session_of", lambda ppid: "s-orch" if ppid == 110 else None)
    from tests.test_state import _insert

    _insert(catalog, id="orch", title="Mail Amplify Orch", kind="other-agent", agent_name="claude",
            agent_session_id="s-orch", state="live", cmux_workspace_id="ws-orch")
    found = health._unregistered(catalog, set(), ps=lambda: table)
    assert [(item["kind"], item["pid"]) for item in found] == [("mcp", 111), ("screen", 222)]  # not the watcher
    assert found[0]["session"] == "Mail Amplify Orch" and found[0]["fix"] == "cycle that session"
