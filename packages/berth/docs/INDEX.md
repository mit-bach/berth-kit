# Berth: where everything is

`repo/packages/berth/` is Berth's home (until 2026-10-28 it was `packages/berth/` in `repo`; that history is the tag `berth-moved-to-forge` and the branch `berth-history` there). Its code, tests, docs, and the sources for the bundles Berth is given away in live here. A few pieces live elsewhere; they are listed below so nothing has to be searched for. The README's **Installed outside FORGE** table lists what Berth puts elsewhere, with how to undo each.

## In this directory

| Path | What it is |
| --- | --- |
| `README.md` | Berth as it behaves now: the screen and its keys, folders, colors, forks, restarts, pause and play, kinds and recipes, every CLI command. |
| `docs/INDEX.md` | This file. |
| `docs/BERTH-CRUD.md` | What Berth holds and how each piece is created, read, updated, and deleted (MCP, CLI, screen, watcher); which state is a real state machine; the urgent flag; every event that moves a session in the sidebar or changes its status. |
| `docs/ACCOUNTS-AND-SIDEBAR.md` | Which Claude account each session is on and where Berth reads it; why the cmux sidebar split folders during account moves, and the fix (2026-11-03). |
| `docs/BERTH-STATE-MACHINE.md` | The spec for row state, the invariant, the quit and restart model, and the incident rules. It wins over the design doc on row state. |
| `docs/BERTH-DESIGN.md` | The original design (2026-10-05). Partly superseded by the state-machine spec and the README. |
| `docs/BERTH-CONTRACT.md` | The catalog contract: tables and columns other tools may rely on. |
| `docs/berth-desk-README.md` | The original screen ("desk") notes. |
| `docs/LOOK.md` | The screen's look, with golden frames that `tests/test_desk.py` compares against. |
| `docs/PROOF.md` | Live measurements on cmux 0.64 (event names, socket, frames). |
| `berth/` | The package. `machine.py` is the only writer of row state; `observer.py` turns cmux observations into transitions; `watch.py` is the watcher; `state.py` is pause and play; `ops.py` is fork, cycle, folders, delete; `claude.py` and `claude_sync.py` follow Claude Code's own session records; `palette.py` computes folder colors; `app.py`, `render.py`, `lineedit.py` are the screen. |
| `berth/mcp.py`, `berth/scope.py` | The Berth MCP (`berth mcp`): eleven tools, the stdio server, and the scope model (`berth sessions` on the command line). See the README's **The Berth MCP**. |
| `berth/send.py` | A prompt to a live session after a look at its pane: ready, working, needs you, starting; deliver (paste, or a typed slash command), interrupt. Used by the MCP's `send`, the watcher's queued sends, and cmux-mcp's `input` guard. |
| `tests/` | The suite. `conftest.py` keeps it off the real catalog, socket, hooks, clipboard, and `~/.claude`. |
| `launchd/README.md` | About the watcher's LaunchAgent. The plist next to it is the copy `berth install` writes beside the installed one. |
| `pyproject.toml` | The package; `berth` console script; `uv pip install --python .venv/bin/python -e '.[dev]'` for tests. |
| `handoff/` | The sources for the bundles Berth is given away in, and `build.py`, which built them from bach-coding-stack's git HEAD and needs rework since the move. See `handoff/README.md`. |
| `dist/` | Built bundles (gitignored). `berth-onboarding.zip` (sources in `handoff/claude-terminal/`): an agent-led onboarding for a Claude Code user moving from plain terminal tabs to cmux, Berth, both MCPs, and the skills. Build it with `python3.12 handoff/build.py`. The older `berth-cmux-onboarding.zip` and `berth-cursor-handoff.zip` are stale; `build.py` builds them only with `--stale`. |

## In `repo`

| Path | Why it is not here |
| --- | --- |
| `packages/cmux-mcp/` | The MCP server agents use; all 27 tools are Berth-backed. Its location is in every installed MCP registration (`claude mcp` and `~/.grok/config.toml`), so it stays put. |
| `skills/cmux-claude/`, `skills/cmux-grok/`, `skills/cmux-harness-driver/` | The skills that teach agents the MCP tools. `install.sh` copies `skills/` into `~/.grok/skills` and these three into `~/.claude/skills`; `packages/cmux-mcp/tests/test_skills.py` finds them by relative path. |
| `scripts/install_cmux_settings.py` | Sets `terminal.autoResumeAgentSessions=false` in cmux's config, which the restart model needs. Run by `install.sh`. |
| `grok/hooks/cmux-session.json` | The Grok hook config that reports Grok sessions to cmux. |
| `install.sh` | Installs the whole stack, Berth included (venv, MCP registration, skills, cmux setting, `berth install`). |

## On this Mac, outside the repo

| Path | What it is |
| --- | --- |
| `~/.local/share/berth/` | Live state, never in git: the catalog `berth.sqlite` and its `.bak-*` backups, `observer.json` (last cmux app identity), `invariant.json` (last check), `view.json` (sort), `selected.json` (the last workspace switch, for the cursor), `events.seq`, `watch.log`, `watch.err`, `watch.pid`, `watch.heartbeat`. Back up the catalog before any manual repair. |
| `~/Library/LaunchAgents/com.bach.berth.watch.plist` | The watcher's LaunchAgent (`berth install`). Restart: `launchctl kickstart -k gui/$(id -u)/com.bach.berth.watch`. |
| `~/.local/bin/berth` | The CLI and screen. Restart the screen after a code change: end the `berth` process, then `cmux send --workspace <screen workspace> -- $'berth\n'`. |
| `~/.claude/skills/cmux-*`, `~/.grok/skills/cmux-*` | Installed copies of the skills. Re-copy after editing `skills/`. |
| `~/.cmuxterm/*-hook-sessions.json` | cmux's agent hook records, read by `berth/hooks.py`. Written by cmux, not by Berth. |
| `~/.claude/sessions/`, `~/.claude/projects/` | Claude Code's own records of live processes and transcripts, read by `berth/claude.py`. |
| `~/.grok/sessions/` | Grok's session store, read for titles and resume checks. |

Running agent sessions keep the MCP code they started with: after changing cmux-mcp or Berth code the MCP imports, they need `/mcp` reconnect (Claude Code) or a new session (Grok).
