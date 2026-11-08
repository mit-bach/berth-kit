"""Memory as macOS counts it and as the sessions demand it (W1, 2026-11-06; the Performance Agent's definitions)."""

from __future__ import annotations

import json
import types

from berth import memory

PS = """\
  100     1 ttys001  /usr/bin/login
  200   100 ttys001  /Users/owner/.local/bin/claude
  210   200 ??       /Users/owner/FORGE/systems/agents/berth/.venv/bin/python
  220   200 ??       /Applications/cmux.app/Contents/Resources/bin/cmux
  230   200 ttys001  /bin/zsh
  231   230 ??       /opt/homebrew/bin/stockfish
  240   200 ??       /usr/bin/caffeinate
  300     1 ??       /bin/bash
  301   300 ??       /usr/bin/python3
  400     1 ttys002  /Users/owner/.local/bin/claude
"""
MB = 1024 * 1024
SIZES = {200: (300 * MB, 70 * MB), 210: (16 * MB, 4 * MB), 220: (6 * MB, 2 * MB), 230: (2 * MB, 2 * MB),
         231: (900 * MB, 950 * MB), 240: (2 * MB, 1 * MB), 300: (1 * MB, 1 * MB), 301: (2000 * MB, 1500 * MB),
         400: (250 * MB, 60 * MB)}


def _measure(monkeypatch):
    monkeypatch.setattr(memory, "mac", lambda: {"used_mb": 30000.0, "physical_mb": 36864.0})
    monkeypatch.setattr(memory, "_pidpath", lambda pid: "")
    monkeypatch.setattr(memory.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(memory, "_heavy_runs", lambda: {300: "CFR Search / engine sweep"})
    run = lambda *a, **k: types.SimpleNamespace(stdout=PS)  # noqa: E731
    return memory.measure({"ws-a": ["ttys001"], "ws-b": ["/dev/ttys002"]}, run=run, usage_fn=SIZES.get)


def test_each_session_is_split_four_ways_by_program_path(monkeypatch):
    found = _measure(monkeypatch)
    a = found["by_workspace"]["ws-a"]
    assert (a["claude_mb"], a["mcp_mb"], a["hooks_mb"], a["shell_mb"]) == (302.0, 16.0, 6.0, 902.0)
    assert "other_mb" not in a  # Claude's caffeinate counts as Claude (the Performance Agent, 2026-11-06)
    assert a["footprint_mb"] == 1226.0 and a["resident_mb"] == 1029.0
    assert found["by_workspace"]["ws-b"]["footprint_mb"] == 250.0
    assert found["count"] == 2 and found["sessions"]["footprint_mb"] == 1476.0  # footprint is the one sum
    assert "resident_mb" not in found["sessions"]  # resident counts shared pages: never summed


def test_a_detached_shell_is_its_own_line_named_from_the_heavy_run_slot(monkeypatch):
    found = _measure(monkeypatch)
    assert found["detached"] == [{"pid": 300, "footprint_mb": 2001.0, "who": "CFR Search / engine sweep"}]


def test_the_parts_follow_pstw_sessions_rule_exactly():
    """pstw sessions (D40): caffeinate is Claude's, cmux's hook program is hooks, /bin/zsh|bash|sh is shell, else MCP."""
    assert memory.part_of("/usr/bin/caffeinate") == "claude"
    assert memory.part_of("/Applications/cmux.app/Contents/Resources/bin/cmux") == "hooks"
    assert [memory.part_of(p) for p in ("/bin/zsh", "/bin/bash", "/bin/sh")] == ["shell"] * 3
    assert memory.part_of("/opt/homebrew/bin/bash") == "mcp"  # not one of the three: as pstw has it
    assert memory.part_of("/opt/homebrew/bin/node") == "mcp"


def test_the_mac_reads_activity_monitors_terms_from_the_kernel():
    mac = memory.mac()
    assert mac["used_mb"] == round(mac["app_mb"] + mac["wired_mb"] + mac["compressed_mb"], 1)
    assert 0 < mac["used_mb"] <= mac["physical_mb"] and mac["pressure"] in {"normal", "warn", "critical"}
    assert "cached_mb" in mac and "swap_used_mb" in mac


def test_the_watcher_stores_it_and_status_and_the_screen_show_it_named(tmp_path, monkeypatch):
    found = _measure(monkeypatch)
    memory.store(tmp_path, found, at="2026-11-06T20:00:00Z")
    assert memory.stored(tmp_path)["at"] == "2026-11-06T20:00:00Z"
    assert memory.of_workspace(tmp_path, "WS-A")["shell_mb"] == 902.0
    line = memory.mac_line({"used_mb": 30520.0, "physical_mb": 36864.0, "swap_used_mb": 13100.0, "pressure": "warn"})
    assert line == "Mac 29.8/36 GB · swap 12.8 · pressure warn"
    stamped = memory.mac_line({"used_mb": 30520.0, "physical_mb": 36864.0}, "2026-11-06T19:58:53Z")
    assert stamped.startswith("Mac 29.8/36 GB at ") and len(stamped.split(" at ")[1]) == 5  # the sample's HH:MM
    assert memory.sessions_line({"footprint_mb": 37171.2}) == "sessions 36.3 GB"
    summary = memory.summary(memory.stored(tmp_path))
    assert set(summary) == {"at", "mac", "sessions", "count", "detached"} and json.dumps(summary)
