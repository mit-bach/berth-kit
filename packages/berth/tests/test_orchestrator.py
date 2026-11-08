"""The compaction frame, and each session's orchestrator for the reorientation hook (Agent Behavior, 2026-11-04)."""

from __future__ import annotations

import json

import pytest

from berth.compact import FRAME, check_note, plan
from berth.errors import BerthError
from berth.ops import mkdir_path
from berth.orchestrator import for_hook, hook_json, of_session, set_orchestrator
from tests.test_state import _insert


def test_a_keep_or_drop_list_is_refused_and_a_task_note_is_framed(catalog):
    for heading in ("KEEP:\n- the plan", "## Keep\n- x", "**Drop**\n- y", "- LET GO: logs", "What to keep:\n- a"):
        with pytest.raises(BerthError) as refused:
            check_note(heading)
        assert "the summarizer decides what to keep" in refused.value.message and refused.value.details["line"]
    for fine in ("TASK: review the PR.\nSEAT: Repo Agent, reports to HOMESTEAD Orch.",
                 "Keep the PR open until CI is green.", "KEEP OUT: the client's figures, because they are private."):
        assert check_note(fine) == fine
    from tests.support import write_claude_conversation

    write_claude_conversation("s-w", "/tmp")
    _insert(catalog, id="worker", title="Worker", kind="other-agent", agent_name="claude", agent_session_id="s-w",
            state="parked", cmux_workspace_id=None, cwd="/tmp")
    job = plan(catalog, "worker", note="TASK: write the release note.")
    assert job["note"] == FRAME + "\n\nTASK: write the release note." and job["frame"] == "v1"
    again = plan(catalog, "worker", note=job["note"])
    assert again["note"] == job["note"] and again["frame"] is None  # framed once, never twice


@pytest.fixture
def tree(catalog):
    """STUDIO and HOMESTEAD, each with its root orchestrator; Agents under STUDIO with its own."""
    system = mkdir_path(catalog, "STUDIO")["id"]
    stewards = mkdir_path(catalog, "STUDIO/Agents")["id"]
    homestead = mkdir_path(catalog, "HOMESTEAD")["id"]
    for berth_id, title, folder in (("sys-orch", "STUDIO Orch", system), ("hom-orch", "HOMESTEAD Orch", homestead),
                                    ("arch-orch", "Agent Architect Orch", stewards),
                                    ("steward", "Database Agent", stewards), ("mac", "Mac Developer", system)):
        _insert(catalog, id=berth_id, title=title, kind="other-agent", agent_name="claude", state="live",
                agent_session_id=f"s-{berth_id}", cmux_workspace_id=f"ws-{berth_id}", folder_id=folder)
    set_orchestrator(catalog, "STUDIO", "sys-orch")
    set_orchestrator(catalog, "HOMESTEAD", "hom-orch")
    set_orchestrator(catalog, "Agents", "arch-orch")
    return catalog


def test_the_nearest_folder_up_the_tree_names_the_orchestrator(tree):
    assert of_session(tree, "steward")["title"] == "Agent Architect Orch"
    assert of_session(tree, "arch-orch")["title"] == "STUDIO Orch"  # not itself: the folder above
    assert of_session(tree, "mac")["title"] == "STUDIO Orch"
    root = of_session(tree, "sys-orch")
    assert root == {"root": True, "peers": [{"folder": "HOMESTEAD", "id": "hom-orch", "title": "HOMESTEAD Orch"}]}
    set_orchestrator(tree, "Agents", None)  # inherit
    assert of_session(tree, "steward")["title"] == "STUDIO Orch"


def test_none_on_a_folder_stops_the_walk(tree):
    """2026-11-04, STUDIO Orch: STUDIO/BERTH's sessions report to Dominik, so the hook prints nothing for them."""
    from berth.orchestrator import NONE

    assert set_orchestrator(tree, "Agents", NONE)["orchestrator"] == "none"
    assert of_session(tree, "steward") is None and of_session(tree, "arch-orch") is None
    assert hook_json(for_hook(tree, session_id="s-steward", workspace=None, cwd=None)) == ""
    assert of_session(tree, "mac")["title"] == "STUDIO Orch"  # beside it, unchanged
    assert set_orchestrator(tree, "Agents", None)["orchestrator"] == "inherit"
    assert of_session(tree, "steward")["title"] == "STUDIO Orch"


def test_the_hook_finds_the_session_by_its_id_then_workspace_then_directory(tree):
    assert for_hook(tree, session_id="s-steward", workspace=None, cwd=None)["id"] == "arch-orch"
    assert for_hook(tree, session_id=None, workspace="WS-MAC", cwd=None)["id"] == "sys-orch"
    # An id it was given, but Berth does not know: nothing, not the shell's own session (2026-11-04, Settings Agent).
    assert for_hook(tree, session_id="unknown", workspace="WS-MAC", cwd="/tmp") is None
    with tree.immediate():
        tree.conn.execute("UPDATE folder SET default_cwd = '/tmp' WHERE name = 'HOMESTEAD'")
    assert for_hook(tree, session_id=None, workspace=None, cwd="/tmp/new-unit")["id"] == "hom-orch"
    assert for_hook(tree, session_id=None, workspace=None, cwd="/nowhere") is None
    text = json.loads(hook_json(for_hook(tree, session_id="s-steward", workspace=None, cwd=None)))
    context = text["hookSpecificOutput"]["additionalContext"]
    assert text["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Your orchestrator is Agent Architect Orch (Berth arch-orc)" in context
    assert hook_json(None) == ""


def test_the_command_line_hook_prints_nothing_and_never_fails(catalog, capsys, monkeypatch):
    from berth.cli import main

    monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"session_id": "nobody", "cwd": "/nowhere"}'))
    monkeypatch.setattr("select.select", lambda r, w, x, t: (r, w, x))
    assert main(["--catalog", str(catalog.path), "orchestrator", "me", "--hook"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["--catalog", "/nonexistent/dir/berth.sqlite", "orchestrator", "me", "--hook"]) == 0


def test_the_mcp_names_a_folders_orchestrator(tree):
    from berth.mcp import Api
    from tests.fakes import FakeCmux

    api = Api(tree.path, cmux=FakeCmux(), caller_workspace="ws-sys-orch")
    done = api("folder", {"action": "orchestrator", "path": "STUDIO/Agents", "session": "Database Agent"})
    assert done["orchestrator"]["title"] == "Database Agent" and done["folder"] == "STUDIO/Agents"
    assert of_session(tree, "mac")["title"] == "STUDIO Orch"


def test_a_root_orchestrator_gets_every_other_roots_orchestrator_never_one_as_its_own(tree, capsys, monkeypatch):
    """2026-11-06, Agent Behavior: three roots since 11-05; STUDIO Orch was told "Your orchestrator is HOMESTEAD Orch"."""
    from berth.cli import main

    uni = mkdir_path(tree, "UNI")["id"]
    _insert(tree, id="uni-orch", title="UNI Orch", kind="other-agent", agent_name="claude", state="live",
            agent_session_id="s-uni-orch", cmux_workspace_id="ws-uni-orch", folder_id=uni)
    set_orchestrator(tree, "UNI", "uni-orch")
    for me, others in (("sys-orch", {"hom-orch", "uni-orch"}), ("uni-orch", {"sys-orch", "hom-orch"})):
        found = of_session(tree, me)
        assert found["root"] is True and {peer["id"] for peer in found["peers"]} == others
    context = json.loads(hook_json(for_hook(tree, session_id="s-sys-orch", workspace=None, cwd=None)))
    text = context["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("You were just compacted. You're a root orchestrator: re-read your instructions")
    assert "HOMESTEAD Orch (Berth hom-orch)" in text and "UNI Orch (Berth uni-orch)" in text
    assert "Your orchestrator is" not in text and "\n" not in text  # one paragraph
    assert of_session(tree, "steward")["title"] == "Agent Architect Orch"  # everyone else: as before
    assert main(["--catalog", str(tree.path), "--json", "orchestrator", "sys-orch"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["root"] is True and [peer["title"] for peer in out["peers"]] == ["HOMESTEAD Orch", "UNI Orch"]
