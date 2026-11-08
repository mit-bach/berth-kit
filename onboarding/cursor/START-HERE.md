# START HERE: Berth kit for Cursor (for agents)

Paths below are relative to the kit: the repository's top folder.

You are an agent picking up Berth, the system Dominik built to run twenty to thirty agent sessions at once. Read this file first, all of it. It says why you have this, what is in the kit, what to read next, how to run the tests, what must not break, and how Berth maps onto a setup where Claude runs in Cursor instead of cmux.

Kit v3.1.2, built 2026-11-07 (Berth as of 2026-11-07). Tested on macOS 27, Python 3.12, cmux 0.65.2, Claude Code 2.1.31x, Grok Build 1.0.67. Your operator is not Dominik, and this is not a one-to-one install. Expect to adapt, not copy.

## 0. Why you have this

Your operator runs Claude in Cursor: several windows, several Claude chats in each. They want to turn every session off safely before an update and back on afterwards in the state it was in, ideally in the same windows, without resuming each one by hand, and to restart a single session. Berth does all of this, and more, for agents running in cmux, a terminal app built for agents. Nobody has designed the Cursor version. That is your work, with your operator: explore, measure, and bring them options before you build.

Read `docs/WHY.md` next (why Berth exists; a session is a conversation, not a pane), then `onboarding/cursor/CURSOR-SETUP.md` (their setup, the first question to settle, what "the same state" can include, window assignment, what becomes of cmux-only features, questions for them, cheap experiments). Then come back here.

## 1. If you read an earlier version of this kit

**Since version 1 (2026-10-27)**, which described an older Berth:

- **Agents act through Berth's own MCP** (`berth mcp`: `status`, `folders`, `sessions`, `session`, `control`, `create`, `update`, `folder`, `delete`, `trash`), with scopes ("my folder", the levels below and above) and a dry run before every bulk action. cmux-mcp is now only `screen` and `input`, for looking at and typing into another session's pane. The new `berth` skill is the MCP's manual.
- **Sleep and wake.** A session can sleep (closed for now, meant to come back) or be parked (put away). `Z` sleeps everything, `W` wakes everything: the off-update-on routine your operator wants, already built.
- **Busy, drafts, and the permission mode** are read from each pane: Berth asks before closing a busy session, saves unsent text in the prompt box and pastes it back after a wake, and reopens a Claude session in the permission mode its status strip showed.
- **Closes can wait**: until a session is idle, or, for a session restarting itself, until its own turn ends.
- **Pins, a 30-day trash, urgent** (a session says it needs the operator), **a working directory set on purpose**, and **Berth's own cmux sidebar** with one status word per session.
- **⌘⇧B from any session lands on that session in the Berth screen.** `TIPS.md` is how Dominik actually uses all of this day to day.

**Since v2.0.0 (2026-10-29):**

- **Look before prompting.** The Berth MCP's `send` reads a session's pane before a prompt goes in. Ready: it sends. Busy: nothing is sent, and the caller sees what the session is doing and chooses to wait (`when idle`, delivered by the watcher), queue it with a note to finish first (`now`), or interrupt (Esc, then send). It never types into an open question, permission, or plan, or over someone typing. cmux-mcp's `input` refuses a prompt into a session that is not ready (`berth/send.py`).
- **Compaction done by Berth.** A pasted `/compact <note>` is sent as a plain message, so agents' own compactions kept failing. `berth compact`, and the MCP's `send` or `create` (fork, then compact) with `compact_file`, type the command, paste the note behind it, and prove it from Claude's transcript (`berth/compact.py`). A closed session, or a fork before its pane opens, is compacted with `claude -p -r`: no pane needed, which is the part that carries over to Cursor as is.
- **One Berth screen.** A second `berth` is refused and goes to the open one; two screens is a reported violation. A protected delete (a Berth screen, your own workspace) can be overridden: `d` then `!` in the screen, `delete` with `force` and `confirm` in the MCP.
- **Permission mode from settings.** Berth no longer starts Claude with `--dangerously-skip-permissions`; a new session takes `permissions.defaultMode` from `~/.claude/settings.json`.

Every earlier file is replaced. If an agent wrote `MY-SETUP.md` from an earlier version, its facts about the operator's Mac still hold; recheck the parts these changes touch.

## 2. What is in this kit

| Path | What it is | Read it when |
| --- | --- | --- |
| `START-HERE.md` | This file. | First. |
| `README.md` | At the top of the kit: the prompts people send to start you ("Cursor", or "Update"). | Already done. |
| `TIPS.md` | Written for your operator, not you: the habits that make Berth fast on cmux. For you, it is the list of what is worth carrying over. | Early. |
| `docs/WHY.md`, `docs/ARCHITECTURE.md`, `docs/WAYS-OF-WORKING.md` | Why Berth exists; its pieces and a session's life; how Dominik works with it. | Second and third. |
| `onboarding/cursor/CURSOR-SETUP.md` | Your operator's setup and goal, and every consideration for building it on Cursor. No design is chosen. | Fourth. |
| `onboarding/cursor/MIGRATION.md` | Every cmux dependency, module by module, and what any replacement backend must give Berth. | Before you plan anything. |
| `docs/DECISIONS.md` | The choices built into Dominik's install, with options. Its last column numbers the stages of his onboarding kit for cmux users; ignore it here. | When you weigh defaults. |
| `docs/INVARIANTS.md` | The rules that must not break, each from a real failure. | Before you change code. |
| `docs/HISTORY.md`, `docs/OPEN-ITEMS.md` | How Berth got here; what is unfinished, including the 3 known failing tests. | Before you change code. |
| `docs/TROUBLESHOOTING.md`, `docs/UNINSTALL.md` | Dominik's install: when something is off, and every piece it puts outside the kit. | If you install any of it. |
| `tools/` | The cmux kit's installer, checker, survey, and migration scripts. `tools/inventory.py` is read-only and lists every running Claude conversation and the terminal app it runs in; useful in Step 0. Do not run `install.sh` here unless your operator decides to try cmux. | Step 0. |
| `packages/berth/` | Berth: code, tests, `README.md` (the full manual), `skill/` (the `berth` skill), `docs/` (`INDEX.md` maps the code; `BERTH-STATE-MACHINE.md` is the spec). | Throughout. |
| `packages/cmux-mcp/` | The cmux-mcp server (`screen`, `input`, and older tools). | Before touching pane driving. |
| `skills/cmux-claude/`, `skills/cmux-grok/` | The skills that start and drive sessions; they share one `berth-core` block. `references/prompts.md` is how to write a prompt for a child agent. | Before porting skills. |

## 3. Reading order

1. This file.
2. `docs/WHY.md`, `docs/ARCHITECTURE.md`.
3. `onboarding/cursor/CURSOR-SETUP.md`.
4. `TIPS.md`.
5. `packages/berth/README.md`, then `packages/berth/docs/BERTH-STATE-MACHINE.md`.
6. `onboarding/cursor/MIGRATION.md`, `docs/INVARIANTS.md`, `docs/OPEN-ITEMS.md`.
7. Code, in this order: `berth/machine.py` (the only writer of state), `berth/db.py`, `berth/observer.py`, `berth/watch.py`, `berth/state.py` (sleep, pause, play), `berth/activity.py` (busy and drafts from the pane), `berth/send.py` and `berth/compact.py` (prompts and compaction), `berth/ops.py`, `berth/claude.py` and `berth/claude_sync.py` (Claude session truth), `berth/mcp.py` (the agents' interface), `berth/deferred.py`, `berth/identity.py`.
8. The skills: `packages/berth/skill/SKILL.md`, then `skills/cmux-claude/SKILL.md`.

## 4. Run the tests

From the kit's top level:

```bash
python3.12 -m venv .venv            # or: uv venv --python 3.12 .venv
.venv/bin/pip install -e "packages/berth[dev]"
(cd packages/berth && ../../.venv/bin/python -m pytest -q -o addopts=)
(cd packages/cmux-mcp && BERTH_ROOT="$PWD/../berth" ../../.venv/bin/python -m pytest -q -o addopts= tests)
```

Expected: Berth 601 passed, 3 failed; cmux-mcp 55 passed. The 3 failures are old and known (`docs/OPEN-ITEMS.md`). The tests never touch a real cmux, catalog, clipboard, or `~/.claude`.

## 5. Ground rules

- **Look before you build.** Until your operator chooses a design, install nothing from this kit and change none of their settings.
- **Never stop, restart, resume, or type into another session**, and do not quit Cursor, without asking first.
- **`~/.claude/ide/*.lock` files hold a login token** (`authToken`). Read the other fields only; never print, copy, or save the token.
- **Do not copy what conversations say** into notes. Ids, names, directories, and windows are enough.

## 6. What carries over, in short

`onboarding/cursor/MIGRATION.md` has the detail. The catalog and state machine, the Berth MCP's tools and scopes, Claude session tracking through `~/.claude/sessions` and transcripts, fork with a preset session id, folders, the trash, names from Claude's transcripts, recency from transcripts, the palette's computation, and the screen (a curses program; it runs in a Cursor terminal) all carry over. What needs a new backend: listing panes with stable ids, opening and closing them, the pids in a pane, and reading and typing into a pane. Reading the pane is what gives busy, drafts, and the carried permission mode. The clean seam is the `Cmux` class in `berth/cmux.py`; options to explore (tmux in Cursor's terminals, a small Cursor extension per window, or catalog only) are in `onboarding/cursor/CURSOR-SETUP.md`.

## 7. What is not in this kit

- Secrets of any kind. The bundle was searched for them before zipping, and for the source operator's own addresses.
- Dominik's live state: his catalog, sessions, transcripts, hook files, logs.
- Build output: `.venv`, `__pycache__`, `.pytest_cache`, the LaunchAgent plist.
- The rest of Dominik's setup, and the skills that serve only his company's work.
