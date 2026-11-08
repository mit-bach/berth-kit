# Architecture

Five pieces. Each has one job; the arrows are the only ways they talk.

```text
 you ──keys──▶ Berth screen (berth, ⌘⇧B) ──intents──┐
                                                    ▼
 agents ──MCP──▶ berth mcp (11 tools) ──────▶  Berth catalog (SQLite)  ◀── Berth watcher (LaunchAgent)
    │                                               ▲                         │  every 2-3 s:
    └──MCP──▶ cmux-mcp (screen, input) ──┐          │                         │  adopt, park, follow, busy,
                                         ▼          │                         │  queued closes, colors, order
                                       cmux (terminal app: workspaces, socket, CLI, sidebar)
                                         ▲                                    │
                     Claude / Grok run in panes, through cmux's wrapper ──────┘ hooks, ~/.claude/sessions
```

## cmux

The terminal. A **workspace** is a terminal (or panes) with a UUID that survives a cmux restart. Its CLI (`cmux workspace list/create/close/rename/select`, `read-screen`, `send`, `send-key`, `paste`, `top`, `events`, `config`) is how Berth and cmux-mcp act. Inside cmux, `claude` runs through cmux's Claude wrapper (`/Applications/cmux.app/Contents/Resources/bin/cmux-claude-wrapper`), whose hooks write `~/.cmuxterm/claude-hook-sessions.json`. cmux's sidebar can be replaced by a custom one (`~/.config/cmux/sidebars/`). Config: `~/.config/cmux/cmux.json` (JSON with comments).

## Berth

The catalog and everything that keeps it true. Code: `packages/berth/berth/`; manual: `packages/berth/README.md`; spec: `packages/berth/docs/BERTH-STATE-MACHINE.md` and `BERTH-CRUD.md`.

- **The catalog** (`~/.local/share/berth/berth.sqlite`). One row per session: title, Berth folder, kind (`other-agent` with agent `claude`, `grok`, `shell`, `process`, `berth` for the screen), state, conversation id, recipe, working directory, pinned, note, saved screen, events. Folders nest. Only `berth/machine.py` changes a row's state, by a fixed table of transitions, each logged.
- **States.** `live` (open in a workspace), `sleeping` (closed for now), `parked` (put away; `park_reason` says why, `cmux_quit` for auto-parked), `playing` and `pausing` (changing right now), and `background` for a headless Claude job (Berth moves those into panes; there are no background agents).
- **The watcher** (`berth watch`, LaunchAgent `com.bach.berth.watch`). Every couple of seconds it compares the catalog with cmux and makes them agree: files new workspaces (Inbox, or a folder by rule), parks a row whose workspace closed (after two looks), follows a pane that switched conversation (`/resume`, a fork, `/clear`) using Claude's own `~/.claude/sessions/<pid>.json`, reads each pane for busy, status, and drafts, runs queued closes (a session asking to restart itself), keeps names and pins in step, colors folders and workspaces, orders cmux's sidebar like the tree, and writes each session's status for the sidebar. It detects a real cmux restart (a different cmux process) and restores what was open. It runs outside cmux, so cmux must accept connections from background programs (`automation.socketControlMode = automation`; cmux's default lets in only processes started in its own terminals).
- **The screen** (`berth`, or ⌘⇧B in cmux). The tree, newest use first, with keys for every action. It records intents; the engine runs them.
- **The sidebar** (`cmux-sidebar/berth.js`, installed by `berth sidebar install`). One row per session: a dot, the name, one word (Working, Ready, Done, Needs you, Question, Permission, Waiting on N subagents, Urgent), in its folder's color. Notifications are reworded through `berth notify-hook`.
- **The CLI.** Every command takes `--json` (`berth sessions`, `wake`, `sleep`, `cycle`, `fork`, `move`, `cwd`, `rename`, `pin`, `delete`, `trash`, `check`, `status`, ...).
- **`berth mcp`**: the catalog and every Berth action as eleven MCP tools: `status`, `folders`, `sessions`, `session`, `control` (wake, sleep, park, cycle, focus, cancel), `send` (a prompt, or a compaction, after a look at what the session is doing), `create` (new or fork), `update` (title, folder, cwd, note, pin, urgent), `folder`, `delete`, `trash`. A scope (your folder, `down`, `up`, `branches`, `query`, filters) picks sessions; a scope action is a dry run until `confirm`.

## cmux-mcp

`packages/cmux-mcp/`. The MCP server agents use for the **pane**: `screen` (read what a live session shows) and `input` (type a short line, place a prompt from a file as one paste, or send a key, for example to answer a trust dialog). It runs on Berth's virtualenv and imports Berth (`BERTH_ROOT`), so it speaks Berth ids. It also still carries an older set of Berth tools (list, get, play, fork, ...); treat `berth mcp` as the Berth API and use cmux-mcp only for `screen` and `input`.

## The skills

- `berth` (`packages/berth/skill/`): sessions and folders through the Berth MCP.
- `cmux-claude` (`skills/cmux-claude/`): starting a Claude on a job (`create` with a prompt file), driving its pane (`screen`, `input`, dialogs), prompts from files (`references/prompts.md`), stop files (`scripts/wait-stop-files.py`). Its Berth section is byte-identical with `cmux-grok`'s; a test checks it.
- `cmux-grok` (`skills/cmux-grok/`): the same for Grok Build, only if they use Grok.

## A session's life

1. A workspace opens running `claude` (by hand, by `create`, or by `tools/migrate.py`). Within seconds the watcher adopts it as a `live` row, learns its conversation id from `~/.claude/sessions`, and its name from Claude's own title.
2. It is filed in a folder and named; Berth renames the workspace and types `/rename` into Claude so all three names agree.
3. `sleep`: Berth reads the pane (refusing if busy, unless confirmed), saves the recipe, screen, and draft, closes the workspace. The row is `sleeping`.
4. `wake`: Berth opens a workspace in the session's directory running `claude -r <id>` through the wrapper, in the permission mode its status strip showed when it closed (when the strip showed none, as in manual mode, with its start flags: no permission flag, so their default), and pastes the draft back once the prompt is ready.
5. cmux quits: every live row is parked `cmux_quit`. cmux comes back: Berth closes the idle panes cmux restored and wakes each of those rows.

## Files on disk

| Where | What |
| --- | --- |
| `<kit>/packages/berth/.venv` | Berth's Python (also runs cmux-mcp). |
| `~/.local/bin/berth` | link to the `berth` command. |
| `~/.local/share/berth/` | the catalog and its backups, `observer.json`, `invariant.json`, `view.json`, `selected.json`, `programs.json`, `prompts/`, `watch.log`, `watch.err`. Never edit by hand. |
| `~/Library/LaunchAgents/com.bach.berth.watch.plist` | the watcher. |
| `~/.config/cmux/cmux.json` | cmux settings: `terminal.autoResumeAgentSessions` false, the `berth` action (⌘⇧B), Berth's sidebar settings and notification hook. |
| `~/.config/cmux/sidebars/berth.js` | link to Berth's sidebar. |
| `~/.claude.json` (`mcpServers.berth`, `mcpServers.cmux-mcp`) | the MCP registrations, user scope. |
| `~/.claude/skills/berth`, `~/.claude/skills/cmux-claude` | links into the kit. |
| `~/.claude/sessions/`, `~/.claude/projects/` | Claude Code's own records, read by Berth, never written. |
| `~/.cmuxterm/*-hook-sessions.json` | cmux's hook records, read by Berth. |
| `~/.berth-onboarding/` | this onboarding's progress, decisions, survey, migration plan. |
