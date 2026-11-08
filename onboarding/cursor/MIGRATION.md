# Migration map: cmux to a setup without it

The target: Claude sessions in Cursor, in its terminals or the Claude Code extension's panels (`CURSOR-SETUP.md`, Step 0, says how to tell which). Nothing here has been ported or tried there. This file lists, module by module, what talks to cmux and what does not, so you can choose a backend and see what it must provide. Paths are under `packages/`. Current as of kit v3.1.1 (2026-11-07).

## The seam

Almost every cmux call goes through two classes:

- `berth/berth/cmux.py`: class `Cmux`. A thin CLI client (`cmux <subcommand>` with `--socket`, JSON output): list, create, close, rename, select, color, pin, and reorder workspaces; read a pane's screen; send text, keys, and pastes; `top` (the pids in each pane); the event stream; set-status and progress labels; the cmux app's process identity (for restart detection).
- `cmux-mcp/cmux_mcp/client.py` and `ops.py` (`CmuxClient`, `CmuxOps`): the two cmux-mcp tools agents still use, `screen` and `input`.

Since 2026-10-28 agents act on sessions through **Berth's own MCP** (`berth/berth/mcp.py`, `berth mcp`, eleven tools), which calls the same code as the screen and the CLI. cmux-mcp's other tools are older paths to the same functions.

A replacement backend that answers `Cmux`'s methods keeps most of Berth. The test fake `berth/tests/fakes.py` (`FakeCmux`) is the shortest complete list of what Berth expects from one.

## Module by module

| Module | Talks to cmux? | What for | Without cmux |
| --- | --- | --- | --- |
| `machine.py`, `db.py`, `schema.py`, `catalog.py`, `errors.py`, `timeutil.py`, `model.py` | No | Catalog, state machine, transitions, events, schema. | Carries over. Column names say `cmux_workspace_id`; that is just a pane link. |
| `tree.py`, `render.py`, `app.py`, `curses_ui.py`, `input.py`, `lineedit.py` | No (the screen) | Draw the tree, keys, text boxes, record intents. | Carries over; runs in any terminal. |
| `drain.py` | Through `state`/`ops` | Runs the screen's intents. | Needs a backend to act. |
| `mcp.py` | Through `ops`, `state`, `scope` | The Berth MCP: `status`, `folders`, `sessions`, `session`, `control` (wake, sleep, park, cycle, focus, cancel), `create` (new or fork), `update` (title, folder, cwd, note, pin, urgent), `folder`, `delete`, `trash`. Scopes (down, up, branches) and dry runs before bulk actions. | The tool shapes and scopes carry over unchanged; the actions need the backend. The best part to keep. |
| `send.py` | Yes (read-screen, paste, send, send-key) | A prompt only after a look at the pane: ready, working, needs you, starting; deliver; interrupt. The MCP's `send`, the watcher's queued sends, the guard on cmux-mcp's `input`. | The decision logic carries over; looking and typing need the backend. |
| `compact.py` | Partly | Compaction proved from Claude's transcript (`compact_boundary`). Live: type `/compact `, paste the note, Enter. Closed or forking: `claude -p -r <id> "/compact <note>"`. | The closed-session and fork paths carry over unchanged; the live path needs typing into the pane. |
| `scope.py`, `trash.py`, `workdir.py` | No | Which sessions an agent sees; the 30-day trash; a session's working directory, set on purpose. | Carry over. |
| `claude.py` | Partly | `~/.claude/sessions` and transcripts (portable). `process_tree`/`pane_pids` parse `cmux top` to find a pane's pids. | Portable except pane-to-pid. |
| `claude_sync.py` | Through pane pids | Keeps each live Claude row on the conversation its pane shows; files forks with their family; background jobs. | Lineage and filing carry over; "which pane shows which conversation" needs pane pids. |
| `activity.py` | Yes (`read-screen`, program status) | Busy, why, and the draft in the prompt box, read from each pane every 3 seconds; status words (Ready, Question, Permission, Working, Waiting on 2 subagents...). | Needs screen text. Without it: the transcript tail and `~/.claude/sessions` `status` give a rough busy; no drafts, no dialog detection. |
| `deferred.py` | Through `state` | Closes that run later: when a session goes idle, or a session closing itself after its turn. | Carries over given close and an idle signal. |
| `identity.py` | Yes | Name sync (Berth, workspace title, agent title, `/rename` typed in); shells promoted to agents; drafts pasted back after a wake (`restore_drafts`). | Title reading and writing are portable; typing and pasting need the backend. |
| `hooks.py` | cmux's files | `~/.cmuxterm/*-hook-sessions.json`. | For Claude, `~/.claude/sessions` covers what matters. |
| `place.py` | No | Kinds, folder rules, resume and fork argv. | Carries over. |
| `state.py` | Yes | Pause/sleep (read screen, save recipe, draft, and permission mode from the status strip, close), play (create workspace, settle). | Ordering and argv carry over; create, close, and screen reading need the backend. |
| `ops.py` | Yes, via `state` and `Cmux` | Fork, cycle, delete, rename, folders, sleep and wake all. | Fork commands are portable; opening panes needs the backend. Folder ops are pure catalog. |
| `observer.py` | Yes | Workspace lists to transitions: debounced parking, relink, restart detection by cmux app identity, restore. | Debounce ideas carry over; the rest is cmux's lifecycle. |
| `watch.py` | Yes | The event stream plus the refresh loop. | Loop shape carries over. |
| `adopt.py`, `invariant.py`, `restore.py` | Workspace list | Filing new panes; `berth check`; reopening after a quit. | Carry over given a list of panes with stable ids, and play. |
| `arrival.py` | cmux's `workspace.selected` | ⌘⇧B lands on the session you came from. | Needs "which pane was focused last"; an editor extension could report it. |
| `attention.py` | Through `cmux_sidebar` | A session marks itself urgent, with why; it clears when a prompt reaches it. | The flag carries over; showing it needs a surface. |
| `cmux_sidebar.py` | Yes | Berth's own cmux sidebar (`cmux-sidebar/berth.js`), status lines, progress labels, the notification hook that rewords notifications. | Drop, or rebuild as an editor panel. |
| `pins.py` | Yes | Pins kept in step with cmux's own workspace pin. | The catalog side carries over. |
| `sidebar.py`, `color.py`, `palette.sync` | Yes | Sidebar order and workspace colors. | Drop; `palette.compute` (pure) carries over. |
| `recency.py` | Partly | cmux's last submitted time plus transcripts and Claude's prompt history. | Transcripts alone work. |
| `figures.py` | No | Status rows for the screen. | Carries over given `activity`'s data. |
| `offer.py` | No | Catalog over the tailnet; merge as parked rows. | Carries over. |
| `install.py` | launchd | The watcher's LaunchAgent. | macOS only. |

## The skills

- `berth` (`packages/berth/skill/`): the Berth MCP's manual for agents: scopes, bulk actions, folders, the trash, urgent, working directories. Almost all portable.
- `cmux-claude` and `cmux-grok`: start and drive sessions. They share one `berth-core` block (a test checks it). Portable: Berth ids, fork through the tool, prompts from files, stop files on disk watched by `scripts/wait-stop-files.py`, never typing `/fork`. Not portable: the spawn commands (cmux's wrapper), the sidebar, and dialog handling, which reads the pane's screen.
- `references/prompts.md`: how to write a prompt for a child agent. Fully portable.

## What any backend must give Berth

In order of importance:

1. A list of open sessions (panes) with an id stable for the pane's life, a title, and a cwd.
2. Create a pane in a cwd running a command; close a pane by id.
3. The pids running in a pane (for Claude: which `~/.claude/sessions/<pid>.json` belongs to it).
4. Read the visible text of a pane: busy and status words, drafts, the permission mode a session reopens in, dialog handling, the MCP `screen`.
5. Send text, keys, and a non-submitting paste (the MCP `input`, `/rename`, putting a draft back).
6. Optional: rename, select, color, pin, reorder, an event stream, "which pane was focused", and a way to tell "the host restarted" from "the host is slow".

With only 1 to 3, Berth can still catalog, file, fork, resume, sleep and wake, and track Claude conversations; you lose busy detection, drafts, the carried permission mode, and agents driving other agents' panes.
