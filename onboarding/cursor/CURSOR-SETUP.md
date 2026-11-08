# Your operator's setup: what they want, and what to explore

This file is for the agents of the person this repository was given to ("your operator"). It sets out what they need, what Berth already solves, and every consideration the source operator (Dominik) could see. **Nobody has chosen a design.** Dominik said so plainly: he is not trying to decide the ideal solution, only to make sure you start with the whole problem in view. Your job is to investigate your operator's machine, measure, and bring them options. Do not build the first idea here.

## Their setup, as described to us

- 5 to 8 Cursor windows open at once.
- Each window has several Claude chats open.
- About 16 to 18 Claude sessions in total.

## What they want

1. **Turn every session off safely**, so they can update the computer (or Cursor) without killing agents mid-work.
2. **Turn them all back on afterwards, in the exact state they were working in before**, without reopening and resuming each one by hand.
3. **Cycle** sessions: off and on again, one or many.

That is exactly Berth's core: `Z` puts every session to sleep, `W` wakes them all, `c` cycles one, and a crash brings back what was open (`WHY.md`, and `TIPS.md` for how it feels to use). On cmux it is easy because cmux is one app whose workspaces Berth can list, close, and recreate. On Cursor the sessions are spread over many windows, so "bring them back" also means **open the right windows and put each session back in a window**: the same one as before, or a different arrangement on purpose. There is a lot of variation in how that could work.

## Step 0: find out what "a Claude chat in Cursor" is on their machine

Everything below depends on this, and it may be a mix:

| Kind | How to tell | What Berth's tracking gives you |
| --- | --- | --- |
| **A. Claude Code CLI in a Cursor integrated terminal** | A `claude` process whose parent chain reaches Cursor; `~/.claude/sessions/<pid>.json` with `"entrypoint": "cli"`. | Everything in `berth/claude.py`: live process to conversation id, transcripts, titles, resume, fork. |
| **B. The Claude Code extension's chat panel** (`anthropic.claude-code`, under `~/.cursor/extensions/`) | The extension is installed. Its sessions should also appear in `~/.claude/sessions` and `~/.claude/projects`, likely with a different `entrypoint` value; verify. | Probably the same conversation tracking. Reopening a conversation in a panel is a different action from typing `claude -r` in a terminal; find out what the extension offers. |
| **C. Cursor's own agent chat using a Claude model** | No `claude` process. The conversation is stored by Cursor (its SQLite state under `~/Library/Application Support/Cursor/`), not in `~/.claude`. | None of Berth's Claude tracking applies. Resuming means Cursor's chat history. |

Settle this with them first. On the source Mac, Cursor 3.25.6 has the Claude Code extension installed, and each Cursor window running it writes `~/.claude/ide/<port>.lock` with `ideName`, `pid`, `workspaceFolders`, `transport`, `runningInWindows`, and an `authToken`. **The `authToken` is a credential: never copy, print, log, or commit those files.** Read only the other keys.

## What "the exact state" can include

| Part of the state | Can it come back? | Notes |
| --- | --- | --- |
| The conversation | Yes | `claude -r <id>` in its directory (`state._claude_play_argv`). |
| Directory, session name | Yes | Name from the transcript (`identity.claude_title`); `claude -n` also sets the terminal title. |
| The window it was in | Only if you record it | See "Window assignment". |
| Its position among a window's tabs | Only if you record it | Probably low value; ask them. |
| Permission mode | On cmux, yes | Berth reads the mode from Claude's status strip when it closes a session and reopens it with `--permission-mode` (`berth/state.py`); manual mode shows nothing on the strip, so such a session reopens in the settings default (`docs/DECISIONS.md`). Needs the pane's screen. New sessions Berth starts take `permissions.defaultMode` from `~/.claude/settings.json`: Berth passes no permission flag. |
| Effort, model | From the recipe | The launch command Berth stored. |
| A turn in progress | No | It stops. Berth reads busy from each pane (`berth/activity.py`) and asks before closing a busy session; a scope action skips busy ones, or queues them to close when idle (`berth/deferred.py`). |
| Unsent text in the prompt box | On cmux, yes | Berth reads the draft from the pane before closing and pastes it back, unsent, after the wake (`identity.restore_drafts`). Needs screen reading and paste. |
| Terminal scrollback | Maybe | Cursor may restore scrollback; the program inside does not come back by itself. |

## The off, update, on sequence

This is how Berth does it on cmux, step by step, with the unknown Cursor equivalent of each step. These are questions, not answers.

1. **Snapshot.** For every session: conversation id, directory, name, launch command and flags, window, position. Berth's catalog is this snapshot: one row per session, with a recipe and a park reason. On Cursor the new field is the window.
2. **Quiesce.** Let busy agents finish, or list them. Berth knows from each pane: a turn running, background shells or subagents, an open question or permission (`berth/activity.py`).
3. **Turn off.** Stop each agent, or quit Cursor. Record that this was a planned "off for update" (Berth's `sleep` reason; `cmux_quit` is a crash), so "on" knows what to bring back.
4. **Update or restart.**
5. **Reopen the windows.** Cursor may restore its own windows (`window.restoreWindows`), or a script opens each one (`cursor <folder>`, `cursor <file>.code-workspace`, `--new-window`). Measure what Cursor restores on their machine.
6. **Turn each session back on in its window.** For kind A: a new terminal in that window running `claude -r <id>`, or type it into the terminal Cursor restored. For kind B: whatever the extension allows (find out). Berth's own lesson from cmux: the app restored panes with the agents not running; Berth closed those panes and opened fresh ones with the recipe. Decide whether Cursor's restored terminals are reused or replaced.
7. **Verify.** Every session that was on is on again, on the same conversation. Berth's `berth check` is the model: compare the catalog with what is really running, and say where they differ.

On cmux, Berth detects a quit and a restart by itself, because cmux could crash or quit at any time. Your operator's trigger is planned ("I'm about to update"), so an explicit "turn everything off" and "turn everything back on" may be all they need. Berth has both: sleep everything (`Z`, `berth sleep --all`, the MCP `control` sleep on a scope) and wake everything (`W`, `berth wake --all`).

## Window assignment

The hardest part, and the most open. Things to weigh:

- **What identifies a window?** Its folder or folders (the `workspaceFolders` in the IDE lock file), or a `.code-workspace` file. Two windows on the same folder cannot be told apart by folder alone; one workspace file per window, or a marker of your own, may be needed.
- **Which window is a running session in?** Leads to verify on their machine: the process's parent chain; for kind A, the IDE connection the CLI makes to the extension in its own window (lock file `pid` and `workspaceFolders`); environment variables VS Code-based terminals set (`TERM_PROGRAM`, and a per-window IPC socket variable in VS Code); or an extension in each window that reports its own terminals and their pids.
- **Same windows, or a new arrangement?** Options to put to them:
  1. Back exactly where it was: the row records its window, and "on" reopens that window and puts the session in it.
  2. Berth folders become windows: one Cursor window per folder, so filing a session is also choosing its window, and reorganizing is allowed.
  3. Windows do not matter: sessions come back wherever, and the catalog is how they find them.
- **A session's directory need not be its window's folder.** Decide what happens then.
- **Treat all their Cursor windows as one "cmux".** Berth sees one terminal app with many workspaces. Here the app would be "all Cursor windows together", and a workspace would be a window plus a terminal or chat tab. What a backend must offer Berth is listed in `MIGRATION.md`, "What any backend must give Berth". Shapes to explore:
  - **A small Cursor extension in every window** that exposes a local socket, the way cmux does: list its terminals with names and pids, create a terminal with a name, directory, command, and color, close one, send text, rename, focus. Berth, or a new coordinator, talks to all of them. The extension API cannot read a terminal's screen, so pause excerpts, the MCP `screen` tool, and dialog handling need another source. Claude's transcript is often a better record of what an agent was doing than its screen.
  - **tmux inside Cursor's terminals**: tmux has a server, stable pane ids, the pids in each pane, `capture-pane`, `send-keys`, and `paste-buffer`, all close to what cmux gives. It is a new layer for them to live with, and a machine restart ends tmux too.
  - **Catalog only**: Berth tracks and files every Claude conversation and produces the list and commands to resume them; a person, or one script per window, reopens them.

## cmux-specific features, and what they might become

| On cmux | On Cursor | Consider |
| --- | --- | --- |
| Sidebar order follows Berth's tree (`berth/sidebar.py`), and Berth's own sidebar shows one status word per session (`berth/cmux_sidebar.py`) | No single sidebar across windows. | Probably drop the order. The status words (Ready, Question, Permission, Working, Urgent) are what Dominik relies on most; an editor panel or the status bar could show them. |
| Each workspace colored by its folder (`palette.sync`, `berth/color.py`) | No per-chat color by default. | Things to explore: a whole-window color per folder through workspace `workbench.colorCustomizations` (title bar, activity bar, status bar), as the Peacock extension does; a terminal tab's color and icon (VS Code-style terminals take a color from the theme's ANSI set, and an extension can set it when it creates a terminal); something in the tab name. Berth's palette (`palette.compute`) makes many distinct hex colors; mapping them onto 16 ANSI colors loses its guarantee that folders differ, so a window-level color may fit better. Whether they want colors at all is their call. |
| Sidebar title = session name (`identity.sync_names`) | Terminal tab titles, extension chat tab titles, window titles. | Claude Code sets the terminal title from `-n` and `/rename`; check whether Cursor shows it (VS Code-style `terminal.integrated.tabs.title` with `${sequence}`). An extension can name the terminals it creates. Window titles come from `window.title`. The one-name rule can probably hold for terminal sessions; find out for extension chats. |
| Workspace UUIDs that survive a restart (`last_workspace_id`, observer relink) | Terminal ids do not survive a Cursor restart. | Link rows by conversation id (from `~/.claude/sessions/<pid>.json`) rather than by pane id. That is a better anchor anyway. |
| cmux hook files (`~/.cmuxterm/*-hook-sessions.json`) | None. | For Claude, `~/.claude/sessions` already covers it (Berth prefers it). Claude Code's own hooks in `settings.json` can write more if needed. |
| Restart detection by the cmux app process (`observer.tick`) | Planned off and on. | Maybe explicit commands are enough. If they also want crash recovery, the same rules apply: never infer a restart from slowness or sleep. |
| ⌘⇧B lands on the session you came from (`berth/arrival.py`) | No equivalent. | An extension command could open Berth's screen on the focused terminal's session. Worth it: it is the habit `TIPS.md` starts with. |
| `read-screen`, `send`, `paste` (MCP `screen`, `input`) | Extension `sendText` only; no screen reading. | Decide whether agents driving other agents' panes matters to them. Screen reading also gives busy, drafts, and the carried permission mode. If they matter, this decides between the extension and tmux. |

The Berth screen itself is a curses program and runs in any terminal, including a Cursor terminal.

## What carries over as is

The catalog and state machine; park, play, cycle, and restore as ideas and as code paths; Claude session tracking through `~/.claude/sessions` and transcripts; fork with `--fork-session --session-id`; folders and subfolders; names from Claude's transcripts; recency from transcript timestamps; the palette's computation. `MIGRATION.md` lists every module.

## Questions to bring to your operator

1. Which kind of Claude chat do they use: A, B, C, or a mix?
2. After an update, must each session come back in the same window, or may they be reorganized, for example one window per folder?
3. Is quitting Cursor the "off" step, or should sessions be stopped one by one first?
4. Must permission mode, effort, model, and unsent drafts come back exactly?
5. Do they want colors? Per window, per terminal tab, or none?
6. Do they want agents that start and drive other agents' sessions (the Berth MCP: create, fork, wake, sleep, cycle, file, urgent), or only off and on?
7. Which habits in `TIPS.md` would they want on Cursor (the ⌘⇧B loop, status words, pins, the trash)?
8. How often do they update, and how long may "back on" take?

## Cheap first experiments

All read-only, none of them changes their setup:

- List every running Claude: pid, `entrypoint`, `kind`, directory, conversation id, name (from `~/.claude/sessions`), and the parent process chain up to Cursor. Match each to a window through the IDE lock files' `pid` and `workspaceFolders`, reading no tokens.
- In one Cursor terminal, run `claude -r <id>` for an idle conversation and confirm it resumes.
- Quit and reopen Cursor once, and record which windows, terminals, scrollback, and extension panels come back by themselves.
- Check what a terminal tab shows after `/rename` in Claude Code.
