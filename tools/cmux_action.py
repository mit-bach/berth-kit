#!/usr/bin/env python3
"""Add the ⌘⇧B action that opens the Berth screen to cmux's config.

    python3 tools/cmux_action.py --check     # exit 0 when an action named berth is there
    python3 tools/cmux_action.py --dry-run   # show the text it would add, validated; change nothing
    python3 tools/cmux_action.py             # add it

cmux has no command that sets an action (``cmux config set actions.berth``
answers "isn't a setting"), so this edits ~/.config/cmux/cmux.json as text: it
keeps every comment and key and adds the one action. Before writing, it reads
the edited text back and checks it with ``cmux config doctor --path``. It
backs the file up, holds cmux's write lock (``cmux.json.cmux-write.lock``)
while it writes, writes through a symlinked cmux.json to its target, keeps
the file's permissions, and reloads cmux. An action already named ``berth`` is
left as it is. Unbinding cmux's own ⌘⇧B (Jump to Last Prompt) is a real
setting, so install.sh does that with ``cmux config set``.

The action runs Berth by its absolute path (``~/.local/bin/berth``), so it
works even when the login shell's PATH lacks ``~/.local/bin``.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # keep the kit clean of __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor import CMUX_JSON, jsonc  # noqa: E402
import inventory  # noqa: E402

BERTH = str(Path.home() / ".local" / "bin" / "berth")
ACTION = {
    "type": "workspace",
    "title": "Berth",
    "subtitle": "Go to the Berth screen",
    "keywords": ["berth", "sessions", "catalog"],
    "shortcut": "cmd+shift+b",
    "restart": "ignore",
    "newWorkspaceMenu": False,
    "workspace": {
        "name": "Berth",
        "cwd": "~",
        "layout": {"pane": {"surfaces": [{"type": "terminal", "name": "Berth", "command": BERTH}]}},
    },
}
COMMENT = (
    "  // Berth: Cmd+Shift+B switches to the open workspace named Berth, and starts\n"
    "  // one running berth when none is open (added by the Berth onboarding kit).\n"
)


def edited(text: str) -> tuple[str | None, str]:
    """(new file text, the text added); (None, "") when an action named berth exists already."""
    data = jsonc(text) if text.strip() else {}
    actions = data.get("actions")
    if isinstance(actions, dict) and "berth" in actions:
        return None, ""
    entry = '"berth" : ' + json.dumps(ACTION)
    if not text.strip():
        added = COMMENT + '  "actions" : {\n    ' + entry + "\n  }\n"
        return "{\n" + added + "}\n", added
    if isinstance(actions, dict):
        match = re.search(r'"actions"\s*:\s*\{', text)
        if not match:
            raise ValueError("cmux.json has an actions object this script cannot find")
        added = "\n    " + entry + ("," if actions else "")
        return text[: match.end()] + added + text[match.end():], added
    first = text.find("{")
    if first < 0:
        raise ValueError("cmux.json has no top-level object")
    added = "\n" + COMMENT + '  "actions" : {\n    ' + entry + "\n  },"
    return text[: first + 1] + added + text[first + 1:], added


def validated(new: str, cli: str) -> str | None:
    """None when the edit reads back with the action and cmux accepts it; else why not."""
    try:
        if jsonc(new).get("actions", {}).get("berth") != ACTION:
            return "the edited file did not read back with the action"
    except ValueError as exc:
        return f"the edited file is not valid JSON with comments ({exc})"
    if not cli:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        handle.write(new)
        candidate = handle.name
    try:
        check = subprocess.run([cli, "config", "doctor", "--path", candidate], capture_output=True, text=True, check=False)
    finally:
        os.unlink(candidate)
    if check.returncode != 0 or "validation passed" not in check.stdout:
        return f"cmux config doctor refused it:\n{check.stdout}{check.stderr}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 0 if an action named berth is present")
    parser.add_argument("--dry-run", action="store_true", help="show the text it would add, validated; change nothing")
    args = parser.parse_args()
    target = CMUX_JSON.resolve() if CMUX_JSON.is_symlink() else CMUX_JSON
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    try:
        new, added = edited(text)
    except ValueError as exc:
        print(f"{CMUX_JSON}: {exc}. Add this action to its \"actions\" by hand:\n\"berth\": {json.dumps(ACTION, indent=2)}", file=sys.stderr)
        return 1
    if args.check:
        print("present" if new is None else "missing")
        return 0 if new is None else 1
    if new is None:
        print(f"{CMUX_JSON}: an action named berth is already there; left as it is")
        return 0
    cli = inventory.which("cmux") or (str(inventory.CMUX_BIN) if inventory.CMUX_BIN.exists() else "")
    problem = validated(new, cli)
    if problem:
        print(f"not written: {problem}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"would add to {CMUX_JSON} (checked with cmux config doctor):{added}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(f"{target.name}.cmux-write.lock")
    with open(lock, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if target.exists():
            current = target.read_text(encoding="utf-8")
            if current != text:
                print("cmux.json changed while this ran; run it again", file=sys.stderr)
                return 1
            backup = target.with_name(f"{target.name}.bak-berth-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(target, backup)
            print(f"backed up {target} to {backup.name}")
        target.write_text(new, encoding="utf-8")
        fcntl.flock(handle, fcntl.LOCK_UN)
    if cli:
        subprocess.run([cli, "reload-config"], capture_output=True, text=True, check=False)
    print(f"added the berth action (⌘⇧B) to {CMUX_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
