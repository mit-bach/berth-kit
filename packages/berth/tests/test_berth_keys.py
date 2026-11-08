"""⌘⇧X outside the terminal: berth-keys, kept by the watcher (2026-10-31)."""

from __future__ import annotations

import json
import time

from berth.keys import Keeper, build, read_state


def _helper(tmp_path):
    """A stand-in helper that, like berth-keys, runs until its stdin closes."""
    script = tmp_path / "helper.sh"
    script.write_text("#!/bin/sh\ncat > /dev/null\n")
    script.chmod(0o755)
    return [str(script)]


def test_the_keeper_starts_the_helper_restarts_it_after_a_minute_and_stops_it(tmp_path):
    clock = [1000.0]
    keeper = Keeper(tmp_path, argv=_helper(tmp_path), clock=lambda: clock[0])
    keeper.tick()
    first = keeper.process
    assert first.poll() is None and read_state(tmp_path)["state"] == "running"
    keeper.tick()
    assert keeper.process is first and keeper.starts == 1  # running: left alone
    first.kill()
    first.wait()
    keeper.tick()
    assert keeper.process is first and read_state(tmp_path)["state"] == "exited, waiting to restart"
    clock[0] += 61
    keeper.tick()
    assert keeper.process is not first and keeper.starts == 2 and keeper.process.poll() is None
    second = keeper.process
    keeper.stop()  # closes its stdin: the helper exits by itself
    assert second.wait(timeout=5) == 0 and read_state(tmp_path)["state"] == "stopped"


def test_status_says_gone_when_the_helper_died_without_a_word(tmp_path):
    (tmp_path / "keys.json").write_text(json.dumps({"state": "running", "pid": 999999}))
    assert read_state(tmp_path)["state"] == "gone"
    assert read_state(tmp_path / "nothing")["state"] == "not started"


def test_the_helper_is_built_when_its_source_is_newer(tmp_path):
    source = tmp_path / "BerthKeys.swift"
    source.write_text("// source\n")
    binary = tmp_path / "build" / "berth-keys"
    compiler = tmp_path / "swiftc"
    compiler.write_text('#!/bin/sh\n# -O -o <binary> <source>\necho built > "$3"\n')
    compiler.chmod(0o755)
    assert build(source=source, binary=binary, compiler=str(compiler)) == "built" and binary.is_file()
    assert build(source=source, binary=binary, compiler=str(compiler)) == "current"
    time.sleep(0.01)
    source.write_text("// changed\n")
    assert build(source=source, binary=binary, compiler=str(compiler)) == "built"
