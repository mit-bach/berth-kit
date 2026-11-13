# Troubleshooting

Start with `python3 <kit>/tools/doctor.py`: it names what is missing and the fix. Then:

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `berth` says a Berth screen is already open, and switches there | only one screen may run | that is the one; ⌘⇧B goes there too |
| "2 Berth screens" in the header or the sidebar | a second screen from an older Berth | `d` then `!` on the extra one's row |
| A delete is refused: `PROTECTED` | the row is a Berth screen, or the workspace you are in | in the screen `d` then `!`; an agent calls `delete` again with `force` and `confirm` (the refusal says so) |
| A prompt from an agent did not go in | the session was busy, had a question open, or someone was typing | the agent's `send` result says which; `when idle` sends it once the session is ready |
| `berth` not found | `~/.local/bin` is not on PATH | add `export PATH="$HOME/.local/bin:$PATH"` to the shell profile; open a new shell |
| Nothing gets cataloged; `watch.err` says `Access denied - only processes started inside cmux can connect` | cmux's socket mode is its default, `cmuxOnly`, which locks out the watcher (a LaunchAgent) | `cmux config set automation.socketControlMode automation` (`docs/DECISIONS.md`, "cmux socket access"); the watcher reconnects on its next tick |
| `berth status` says the watcher is down | the LaunchAgent is not loaded, or crashes | `berth install`; read `~/.local/share/berth/watch.err`; `launchctl kickstart -k gui/$(id -u)/com.bach.berth.watch` |
| A cmux workspace is not in Berth | the watcher was down, or it opened a second ago | wait a few seconds; `berth adopt` files every open workspace (closes nothing) |
| `berth check` reports drift | something changed while the watcher was down, or a rename in flight | look again in ten seconds; restart the watcher; report what remains. Never edit the catalog by hand |
| A Claude row shows the wrong conversation, or `(unnamed)` | the pane switched conversation; the watcher follows within a tick | wait; if it stays wrong, `berth log <id>` (its events) and the watcher log |
| `wake` fails with `SESSION_MISSING` | Claude has no transcript for that id (deleted, or never had a turn) | the row cannot reopen; Berth notes it on the row. Delete it, or start a new session |
| `wake` fails with `SESSION_IN_USE` | the conversation is open elsewhere | find it (`sessions {"query": "<session id>"}`); close the other one first |
| `BUSY` on sleep, park, or cycle | a turn, background work, a question, or an unreadable screen | wait, or queue it (`"when": "idle"`), or `"force": true` only if losing that work is fine |
| A new session sits at "Do you trust the files in this folder?" | Claude was never trusted in that directory | answer it (cmux-claude skill **Dialog**: `screen`, then `input` keys) |
| The `berth` or `cmux-mcp` tools are missing in a chat | the session started before they were registered | cycle it (`berth cycle <id>`, then end the turn), or start a new chat; check `claude mcp get berth` |
| cmux-mcp tools act on the wrong Berth | `BERTH_ROOT` in its registration points elsewhere | `tools/install.sh agents` again; `doctor.py --stage 3` |
| The sidebar shows cmux's own rows, or says the watcher is not writing statuses | a cmux update reset its settings, or the watcher is down | `berth sidebar install` (safe to repeat); restart the watcher |
| ⌘⇧B does nothing | the action is missing from `cmux.json`, or another app took the shortcut | `python3 tools/cmux_action.py --check`; `tools/install.sh berth` |
| After a cmux restart nothing came back | the watcher was down at the quit | `berth restore` reopens the last quit's sessions |
| `migrate.py apply` says WAIT | that conversation still runs in its old tab | `/exit` in that tab, then apply again |

## Logs and state

- `~/.local/share/berth/watch.log`, `watch.err`: the watcher.
- `berth log <id>`: one session's events (or `session {"session": "<id>", "history": true}`).
- `~/.local/share/berth/invariant.json`: the last check.

## Run the tests

```bash
cd <kit>
python3.12 -m venv /tmp/berth-test-venv      # or: uv venv --python 3.12 /tmp/berth-test-venv
/tmp/berth-test-venv/bin/pip install -q -e "packages/berth[dev]"
(cd packages/berth && /tmp/berth-test-venv/bin/python -m pytest -q -o addopts=)
(cd packages/cmux-mcp && BERTH_ROOT="$PWD/../berth" /tmp/berth-test-venv/bin/python -m pytest -q -o addopts= tests)
```

`-o addopts=` matters: Berth's `pyproject.toml` already passes `-q`, and a second one hides the summary line. Expected: Berth 607 passed, 3 failed; cmux-mcp 55 passed. The tests never touch the real catalog, cmux, the clipboard, or `~/.claude`. Use a throwaway virtualenv as above, or the installed one (`packages/berth/.venv`).

## After changing code, a skill, or an MCP server

- The watcher: `launchctl kickstart -k gui/$(id -u)/com.bach.berth.watch`.
- The screen: `q`, then ⌘⇧B.
- Agent sessions: cycle them; they keep the MCP code they started with.
