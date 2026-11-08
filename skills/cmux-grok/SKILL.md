---
name: cmux-grok
description: >
  Run Grok Build sessions on this Mac: start one, fork one, reopen, close, restart,
  file, rename, and pin them through the Berth MCP (the `berth` server), and drive a
  live Grok pane through cmux-mcp (see the UI, place a prompt from a file, clear the
  trust dialog), then watch stop files on disk. Use when the operator says start or
  spawn a Grok, open a grok workspace, give a Grok session a job, fork a grok session,
  wake a Grok session, put sessions in a Berth folder, type in the grok prompt, Grok
  Build TUI, /cmux-grok, or when work must run in a separate Grok session. Read the
  berth skill for scopes, bulk actions, folders, and the trash. Not Claude panes: that
  is cmux-claude. Not Pi layout verify.
user-invocable: true
metadata:
  short-description: "Grok Build sessions: Berth for the session, cmux for the pane"
---

# cmux-grok

Start a Grok Build session on a job, give it work, watch it finish. Every session is a row in Berth.

Two servers, two jobs:

- **`berth`** is the session as a whole: find, start, fork, wake, sleep, park, cycle, rename, file, pin, delete. Reach for it first, for everything that is not the pane.
- **`cmux-mcp`** is the live pane: `screen` to see it, `input` to type into it. Its source is `repo/packages/cmux-mcp` in the Berth kit. Do not call `cmux-agent-mcp`.

This file drives Grok panes, and `cmux-claude` drives Claude panes. Their sections from **Berth** to **Refuse** are the same. Do not load `cmux-claude` for a Grok pane. If both are attached, this file wins for Grok panes. A Grok row has `agent` `grok`.

Do not `find` grok on disk. Do not run `grok --help` for flags named here.

`SKILL_DIR` is the directory that holds this file. `WAIT` is `SKILL_DIR/scripts/wait-stop-files.py`.

---

<!-- berth-core: this block is the same in cmux-claude and cmux-grok. packages/cmux-mcp/tests/test_skills.py checks it. -->

## Berth

Berth is the catalog of every session on this Mac: Claude and Grok conversations, shells, and processes, in nested folders. The `berth` MCP server is how you find, start, fork, open, close, file, rename, pin, and delete sessions. Use it for everything about a session as a whole. cmux-mcp is only for the live pane: `screen` to see it and `input` to type into it.

Read the `berth` skill (`~/.claude/skills/berth/SKILL.md`, or `~/.grok/skills/berth/SKILL.md` in Grok) before anything this table does not cover: wider scopes, acting on many sessions at once, folders beyond create, the trash, or working without the `berth` tools.

A session's `state` is `live` (open in a workspace), `sleeping` (closed for now; `wake` reopens it), `parked` (put away; `wake` reopens it too), or `playing` and `pausing` while it changes. `pinned: true` keeps it first in cmux's sidebar. A session is named by its Berth `id` (or its first 4+ characters), its title, its agent session id, or `"me"`. Copy the `id` from a result. Never pass `workspace:N`, a cmux group, or a surface.

One name per session: the Berth title, the cmux sidebar title, and the agent's own session name are the same. `update` with `title` sets all three, and types `/rename` into a live session itself. Do not type `/rename`.

Berth's watcher keeps Berth and cmux in step every two seconds and orders the cmux sidebar every 30 seconds. Do not repair rows, colors, or order by hand. If something looks wrong, call `status` with `check` true and tell the operator.

## Call

Claude Code calls `mcp__berth__<tool>` and `mcp__cmux-mcp__<tool>` directly. Grok calls `use_tool` with `tool_name` `berth__<tool>` or `cmux-mcp__<tool>` and `tool_input` the JSON. Do not `search_tool` for these tools. `read_file` below is Grok's `read_file` or Claude's Read tool.

The `berth` server:

| Job | Tool | Input |
| --- | --- | --- |
| who you are and your folder; cmux and the watcher | `status` | `{}` |
| the sessions in your folder | `sessions` | `{}` |
| find a session by name, anywhere | `sessions` | `{ "query": "<words>" }` |
| your folder with its subfolders, or with your parent's sessions | `sessions` | `{ "down": 1 }`, `{ "up": 1 }` |
| one session in full | `session` | `{ "session": "<id>" }` |
| every folder path | `folders` | `{}` |
| make a folder or a subfolder | `folder` | `{ "action": "create", "path": "<Parent/Child>" }` |
| start a new session on a job | `create` | see **Spawn** |
| fork a session (`F` in Berth) | `create` | `{ "from": "<id>" }` |
| reopen a sleeping or parked session | `control` | `{ "action": "wake", "sessions": "<id>" }` |
| close one for now, or put it away | `control` | `{ "action": "sleep", "sessions": "<id>" }`, `"park"` |
| turn a live session off and on again (`c`) | `control` | `{ "action": "cycle", "sessions": "<id>" }` |
| give an open session a prompt, after looking at what it is doing | `send` | `{ "session": "<id>", "prompt_file": "<abs>" }`, or `"text"` |
| show a live session in cmux | `control` | `{ "action": "focus", "sessions": "<id>" }` |
| rename, file in a folder, pin | `update` | `{ "sessions": "<id>", "title": "<name>" }`, `"folder"`, `"pin"` |
| move or rename a folder | `folder` | `{ "action": "move", "path": "<folder>", "into": "<parent>" }`, `"rename"` |
| delete (to the trash, 30 days) | `delete` | `{ "sessions": "<id>", "confirm": true }` |
| what is in the trash | `trash` | `{}` |

## Pane

The `cmux-mcp` server, for a live session's pane. It takes the same Berth `id`.

| Job | Tool | Input |
| --- | --- | --- |
| see the pane | `screen` | `{ "id": "<id>", "lines": 20 }` |
| one key (a dialog, an overlay) | `input` | `{ "id": "<id>", "target": "prompt", "key": "escape" }` |
| a short answer, not submitted | `input` | `{ "id": "<id>", "target": "prompt", "text": "y" }` |
| shell line | `input` | `{ "id": "<id>", "target": "terminal", "text": "<line>", "submit": true }` |

A prompt for an open session goes through `berth` `send` (**Send**), not `input`. `input` takes exactly one of `text`, `path`, or `key`. `text` is one line, at most 200 UTF-8 bytes; `submit: true` presses Enter after it. `path` is an absolute file the server reads into the prompt box, without Enter. A prompt by `input` (a `path`, or `text` with `submit`) into a Claude or Grok session that is not ready is refused with what it is doing (`BUSY`); `force` types it anyway. `key` sends one key, never checked: keys answer dialogs. cmux-mcp also has older Berth tools (`list`, `get`, `play`, `pause`, `fork`, `mkdir`, `create`, and more). Do not use them: the `berth` server replaces them.

## Folders

A folder's path is its names from the top level, joined by `/`: `Work/Clients/Acme`. Inbox means "not in a folder" and holds no folders.

- "Make folder X inside Y": `folder` create with the full path `Y/X`. A bare `X` makes a top-level folder, which is wrong here.
- The operator names a folder by its last name only: that name works when it is unique; when two folders share it, the error lists both. Ask which.
- A new session goes in the folder the operator named. With none named, `create` files it in your own folder.

## Fork

A fork is `create` with `from`, for Claude and Grok sessions alike, the same as `F` in the Berth screen. The conversation is copied under a new session id; the copy opens in a new workspace in the original's folder, titled `<title> ⑂` (or `title`), and the original keeps running. It needs at least one turn of conversation (`SESSION_MISSING` otherwise: the job is a spawn).

1. Find the source with `sessions` (a `query` finds it by name).
2. `create` `{ "from": "<id>" }`. Pass `title` only when the operator named the fork.
3. To give the fork a job, `send` it a prompt on the returned id (**Prompt**, then **Send**). It already has the whole conversation.
4. To fork a set, call `create` once per source. Do not fork a fork in the same operation.

Never type `/fork` into a pane: Claude's makes a headless copy with no workspace, and Grok's takes the source's pane away.

## Open and close

- `wake` opens a sleeping or parked session in a new workspace and resumes the same conversation, without taking focus. Never type a resume command yourself.
- `sleep` closes a live session to come back soon; `park` puts it away. Both save its screen first.
- `cycle` turns a live session off and on again on the same conversation, in the same permission mode. Use it when the operator says cycle, restart, or "turn it off and on". It is also how a session loads new MCP servers, skills, or settings.
- A busy session (`busy: true`, with `busy_why`: a turn, background shells or agents, an open question) is not closed now: `sleep`, `park`, and `cycle` refuse it. `"when": "idle"` queues the close for Berth's watcher, which does it as soon as the session is idle; `"force": true` closes it now and cuts that work off, only when the operator accepts that. Text typed in a prompt box is saved at every close and put back, unsent, when the session reopens.
- To restart yourself (a new MCP server, skill, or setting), `control` `{ "action": "cycle", "sessions": "me" }` and end your turn: Berth's watcher cycles you once the turn ends. Do not ask another session to cycle you.
- A whole folder at once is `control` with a `scope` (the `berth` skill): the first call only lists what would happen.

Only `sleep`, `park`, `cycle`, and `delete` close a pane, and only on the operator's word. Never close a live agent pane to clean up.

## Send

A prompt for a session that is already open (a fork, a woken session, one between jobs) goes through `send`. Berth looks at the session first, and you decide from what it shows.

1. `send` `{ "session": "<id>", "prompt_file": "<abs>" }` (or `text`). Ready (its prompt on screen, no turn running; shells or subagents it started may still run): it is sent, and the result says `"sent": true`.
2. Not ready: nothing is sent. The result gives `status` (`working`, `needs_you`, `limited`, `overlay`, `starting`), `why`, and the end of its `screen`. Read what it is doing, then send again with `when`:
   - `"idle"`: it waits in the session's mailbox, in order behind any prompts already there, and Berth's watcher types it the moment the session is ready, within `wait` minutes (default 60). The usual choice for a new job. It never replaces another sender's prompt. `session` lists the waiting `prompts`; `control` `cancel` takes back your own.
   - `"now"`: a working Claude takes it as soon as it can, which can be in the middle of its task. Berth puts a line in front asking it to finish what it is doing first; `"as_is": true` leaves the line out, for a correction that must land now. Refused for Grok: a message stops Grok's turn.
   - `"interrupt"`: Esc stops the running turn, then the prompt goes in. Only when the operator wants it stopped, or what it is doing is wrong.
3. `needs_you` (a question, permission, or plan) is the operator's. Never answer it to get a prompt in: tell them, or `when` `idle`.
4. `refused`: someone is typing in its prompt box, maybe the operator. It is never typed over; `when` `idle` sends once the box is clear.
5. A compaction is not a prompt: **Compact**.

`send` never goes to yourself. A new session gets its first prompt from `create`.

## Compact

A compaction is Berth's, end to end. Never place `/compact` yourself, typed or pasted: a pasted `/compact <note>` reaches the session as a plain message, and on 2026-10-30 a fork read its note as its job and built what the note described.

1. Write the note to a file: the task the summary is for (TASK) and, if the session becomes another agent, its seat (SEAT: name, home, who it reports to, whose work it leaves alone); optionally SOURCES (paths) and KEEP OUT (only on purpose, with why). The summarizer decides what to keep: Berth wraps the note in its frame and refuses keep or drop lists. Up to 32 KB. No `/compact` in it.
2. Write the next prompt to a file too, if there is one.
3. One call:
   - an open or closed session: `send` `{ "session": "<id>", "compact_file": "<abs note>", "prompt_file": "<abs next prompt>" }`;
   - fork and compact: `create` `{ "from": "<id>", "title": "<name>", "compact_file": "<abs note>", "prompt_file": "<abs job>" }`. The fork is compacted before its pane opens, and the original is untouched.
4. It returns at once with `compaction.state` `queued`. Berth waits until the session is ready (your own session too, once your turn ends), compacts, checks Claude's own record of it, then sends the prompt. `session` shows `compaction.state`: `done` (with `pre_tokens` and `post_tokens`) or `failed` with `why`, Claude's own words when it declined ("Not enough messages to compact.").

## Prompt

The bytes that land in the agent's prompt are the child session's job.

Before you place those bytes (`send` or `create` with `prompt_file`), call `read_file` on `SKILL_DIR/references/prompts.md`. Do this on the same turn. An earlier read does not count.

Write the file the way `prompts.md` writes the prompt. The tool argument is the path. Do not also pass the file body as `text` or `prompt`. A short line that fits in `text` does not need a file. A job prompt does. A shell command does not use that file.

## Watch

When the job writes stop files and you are not driving the UI, watch them with `WAIT`, which is `SKILL_DIR/scripts/wait-stop-files.py`. It prints `DONE` or `FAILED`.

- Grok: `monitor({ command: "python3 <WAIT> --timeout 10800 -- <stop-file> <stop-file>", description: "Watch stop files", timeout_ms: 36000000 })`. The monitor notifies. Do not `get_command_or_subagent_output`.
- Claude Code: the Bash tool, run in the background, with `python3 <WAIT> --timeout 10800 -- <stop-file> <stop-file>`. Do not call a tool named `monitor`.

Do not `screen` in a loop to wait for a file. A finished job is the file on disk. Do not `focus` only to watch.

## Done

On `DONE`, open the stop files with `read_file`. Disk is the proof.

On `FAILED`, `session` that id. If it is not live, `wake` it: the same session, no new identity. If it is live, **Drive**. Never open a second session for the same job, and never start a second one because a list missed the first.

Leave the session open. Do not `sleep` or `park` a job that must keep running.

## Refuse

- `search_tool` for any tool in **Call** or **Pane**
- cmux-mcp's Berth tools (`list`, `get`, `play`, `pause`, `fork`, `mkdir`, `create`, `rename`, `sleep`, `wake`, `delete`, and the rest); the `berth` server does these
- `cmux-agent-mcp` tools, `workspace_list`, `workspace_create`, `workspace_close`, or `group`
- A cmux group, or `workspace:N`, `group`, or `surface` as an argument
- A folder made with a bare name when the operator said it goes inside another
- `/fork` or `/rename` typed into a pane, short or from a file
- `/compact` placed by you, typed or pasted: **Compact**
- `--fork-session`, `--resume`, `-r`, or `--session-id` typed into any command
- A second `create` because a result looked slow, or a fork of a fork in the same operation
- `create` for a session that already has a row: `wake` it
- `sleep`, `park`, `cycle`, `delete`, or a `confirm` the operator did not ask for
- Closing a live agent pane to clean up, or closing your own session unasked
- `focus` as a spectator, or `screen` as a poll for a file
- `text` or `prompt` set to the body of a prompt file, or `submit` together with `path`
- `spawn_subagent` for this job
- Editing Berth's catalog, the cmux hook files, or `~/.claude/sessions` by hand
- A new session whose title is empty

<!-- /berth-core -->

---

## Spawn

Order matters. Do not skip a step.

Name these first, on disk, before any call: the stop files (absolute), `cwd`, and the one title. A title is required: a short name for the job. If the operator did not give one, write one before `create`. When the operator named a folder, that path is the Berth folder (**Folders**).

1. `status`. If `cmux` is `down`, stop.
2. The folder the operator named, by its full path. If it is missing, `folder` create it, with the full `Parent/Child` path for a subfolder. With no folder named, leave `folder` out: the session is filed in your own folder.
3. `create` `{ "agent": "grok", "title": "<title>", "cwd": "<abs>", "folder": "<path>" }`. Keep the returned `id`. Grok takes no prompt at start.
4. `screen` with that id and `lines` 20. Clear **Dialog**. Do **Prompt**, then place the job the way **Drive** places it.
5. `session` with that id. Grok writes no session until its first prompt, so `session` is empty until the job is placed. After the job is placed, look again: `agent` is `grok` and `session` is Grok's session id.
6. Once `session` is set, `update` `{ "sessions": "<id>", "title": "<title>" }`. Grok has no name flag, so this is what names the Grok session: Berth types `/rename` into it. If `session` was still empty, the rename named only the sidebar; `update` again once it is set.
7. **Watch** the stop files, or stay in **Drive**.

`create` starts Grok with `--always-approve --no-subagents --reasoning-effort xhigh`. Never start Grok another way: not cmux-mcp's `create`, not a typed `grok` command, not `-p`, `--single`, or `--worktree`.

---

## Drive

One terminal surface. Three things you do to it. Sequential when order matters.

**See the Grok UI.** `screen` with that id. Use this when you must know what the interface is showing: prompt, slash menu, overlay, plan, permission, status strip. Pass `lines` 20. The maximum is 40. After a key or a slash, `screen` again if the next key depends on what appeared. If the first screen is blank or a startup banner, run `sleep 2` once and `screen` once more. Do not keep sleeping.

**Dialog.** The prompt is not ready while a dialog is on screen. Do not place a job, a slash, or any other text on a dialog: one letter can answer it.

- Directory trust. The screen contains `Do you trust the contents of this directory?`, with `Yes, proceed  y` and `No, quit  n`. `input` with `text` `y` and no submit, then `screen`. Grok opens a directory it trusts without asking again.

**Type in the prompt.** That is the Grok input box at the bottom of the TUI, with `Shift+Tab:mode` under it.

1. `screen` if you do not know focus.
2. If an overlay or slash menu is in the way, `input` with `key` `escape`.
3. Do **Prompt** before Grok prompt bytes.
4. A job is `berth` `send` with `prompt_file` (**Send**). It looks first, and sends only to a ready prompt unless you choose `when`.
5. A short slash command is `send` with `text`: typed on one line, into a ready prompt. A compaction is **Compact**, never a slash command you place. Never send `/fork` or `/rename` (**Fork**, **Berth**).

`escape` dismisses an overlay and returns to the prompt.

**Type in the terminal.** The surface is a shell, or Grok is not up yet.

- `input` `target` `terminal` with `text` and no submit types the line and does not run it.
- `submit` true runs the line.
- `key` for `ctrl-c`, `tab`, arrows.
- `screen` `lines` 20 is enough for the last shell lines.

---

## Refuse (Grok panes)

- `cmux-claude` for a Grok pane
- `find /` (or a home-wide find) for the grok binary
- Starting Grok any way but `berth` `create` with `agent` `grok`
- `-p`, `--single`, or `--worktree`
- Any text but `y` on the directory-trust dialog
- An `update` title before `session` is set, left as the only rename
