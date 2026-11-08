---
name: berth
description: >
  Find, open, close, start, fork, file, rename, pin, and delete sessions through the Berth MCP
  (the `berth` server). Berth is the catalog of every Claude, Grok, shell, and process
  session on this Mac, in nested folders. Use when the operator names a session or an
  agent ("the release manager", "Reviewer"), a folder ("everything in Work"),
  or says wake, idle, sleep, park, pause, cycle, restart, fork, spawn, start a session, move,
  file, rename, pin, delete, trash, restore, urgent, escalate, "needs my attention",
  "move it to another directory", "change its working directory / cwd",
  "what's running", "what's in my folder",
  "my siblings", "the parent folder", or /berth. cmux-claude and cmux-grok send you here
  for anything beyond their short Berth table.
user-invocable: true
metadata:
  short-description: "Sessions and folders through the Berth MCP"
---

# Berth

Berth is the catalog of every session on this Mac: Claude and Grok conversations, shells, and processes, filed in nested Berth folders (`Work/Billing`). Each session is a row. The `berth` MCP server reads and changes that catalog, and the Berth screen (`berth` in a terminal, ⌘⇧B in cmux) shows the same rows.

**A folder is not a directory.** A session has both, and they are independent:

- its **Berth folder** (`folder`): where it is filed in the catalog. Berth's own; it never touches the disk. `update {folder}` refiles it.
- its **working directory** (`cwd`): the absolute path on disk it runs in. For Claude, that decides the `CLAUDE.md` chain, auto-memory, project settings and hooks, and trust. `update {cwd}` changes it.

Changing one never changes the other. In this skill and in every Berth result, "folder" means a Berth folder and "directory" or `cwd` means the disk; every result that shows `folder` shows `cwd` next to it.

A session is in one state:

- `live`: open in a cmux workspace. `busy: true` (with `busy_why`) means closing it now would cut work off: a turn running, background shells or agents still running, a question or dialog open, or a command running in a shell. `draft` means text is typed in its prompt box. `status` is what cmux's sidebar shows for it (`working`, `ready`, `needs_you`, `background` for waiting on agents or shells, `btw_answered`, `urgent`, `follow_up`, ...), and `status_label` the word.
- `idle`: closed to free its memory, and in use again soon. It belongs with the live sessions: count it as part of the working set (`"state": ["live", "idle"]`). `wake` reopens it.
- `sleeping`: closed for now, its screen saved, meant to come back. `wake` reopens it.
- `parked`: paused and put away. `wake` reopens it too.
- `background` (a headless Claude job), `playing`, `pausing` (changing right now; wait and look again).

A pinned session (`pinned: true`) stays in its place in Berth and comes first in cmux's sidebar. An urgent session (`urgent: {"why", "since"}`) said it needs the operator urgently; `sessions {"urgent": true}` finds them.

## Call

Claude Code calls `mcp__berth__<tool>` directly. Grok calls `use_tool` with `tool_name` `berth__<tool>`. Do not `search_tool` for them. Every result is JSON. An error result is `{"error": {"code", "message", "details"}}`: read `details` (an `AMBIGUOUS` error lists the matches).

| Tool | What it is for |
| --- | --- |
| `status` | who you are (`you`: your session, Berth folder, and working directory), counts by state, cmux and the watcher up or down. `{"check": true}` also checks Berth against cmux. |
| `folders` | the folder tree with each folder's live, idle, sleeping, and parked counts. |
| `sessions` | the sessions in a scope, grouped by folder. The main read. |
| `session` | one session in full; `excerpt` adds the screen saved when it closed, `history` its recent events. |
| `create` | start a new Claude, Grok, or shell session in a working directory (`cwd`), or fork one (`from`; with `cwd`, a Claude fork runs in another directory; with `compact_file` and `prompt_file`, fork and compact). |
| `control` | `wake`, `idle`, `sleep`, `park`, `cycle`, `focus`, `cancel` (a queued close, and your own queued prompts). |
| `send` | a prompt to an open Claude or Grok session, after looking at what it is doing: sent when it is ready; otherwise its status and screen, and you choose to wait (`when idle`), queue (`now`), or interrupt. With `compact_file`: compact a Claude session first (**Compact**). |
| `update` | `title`, `folder` (Berth folder), `cwd` (working directory, from the next open; `cycle` to apply now), `note`, `pin`, `urgent`, `follow_up` (the server's instructions say when). |
| `folder` | `create`, `rename`, `move`, `delete`, `archive`, `unarchive` a folder. |
| `delete` | sessions to the trash (kept 30 days). |
| `trash` | `list`, `restore`, `forget`. |

That is CRUD over sessions and folders: create (`create`, `folder` create), read (`sessions`, `session`, `folders`, `status`), update (`update`, `control`, `folder`), delete (`delete`, `trash`, `folder` delete). Reading a live pane's screen and pressing keys in it are cmux-mcp's (`screen`, `input`; the cmux-claude skill); a prompt goes through `send`.

## Name a session

Every tool that takes a session takes any of: its Berth `id` or the id's first 4+ characters, its exact title (any case), title words that match only it, its agent session id, or `"me"` (you). Copy the `id` from a result when you have one. When a name matches several, the error lists them; pick by `id`. Never pass `workspace:N` or a cmux group.

## Scope

`sessions` with no arguments shows your own folder: the folder of the session you are running in. From there:

| You want | Arguments |
| --- | --- |
| my folder | `{}` |
| my folder and its subfolders | `{"down": 1}`, or `{"down": "all"}` for every level |
| my folder and my parent's own sessions | `{"up": 1}` |
| everything up to the top level, on my line | `{"up": "all"}` |
| my parent's whole family at my level (siblings too) | `{"up": 1, "branches": true}` |
| ...and one level below each of them | `{"up": 1, "branches": true, "down": 1}` |
| another folder | `{"folder": "Work/Support"}` or a unique last name `{"folder": "Support"}` |
| everything | `{"folder": "/"}` |
| one session by name, anywhere | `{"query": "slack orchestrator"}` |
| only some | add `"state": ["live"]`, `"agent": ["claude"]`, `"pinned": true`, `"busy": false` |
| more about each | `"fields": ["folder", "workspace", "last_used", "note"]` or `["all"]` (`folder` always comes with `cwd`) |

`up` adds each ancestor's own sessions only. `branches` adds the ancestors' other subfolders too. Each folder group in the result has `level` (0 where the scope starts, -1 the parent, 1 a child) and says what the scope left out: `deeper` (subfolders below it) or `beside` (an ancestor's other subfolders). Widen with `down`, `up`, or `branches` when those counts matter. Each session has `id`, `title`, `state`, `session` (the agent's own session id), and `pinned`, `busy`, `you` when true.

A result over 100 sessions has `next_offset`: pass it as `offset`.

## Do

**Who am I, where am I.** `status`. `you.folder` is your Berth folder, `you.cwd` your working directory, `you.id` your session.

**Find a session the operator named.** `sessions {"query": "<words>"}`. One match: use its `id`. Several: show the operator the titles and folders, or narrow with `folder`. None: try fewer words, then `folders`.

**Open one.** `control {"action": "wake", "sessions": "<id>"}`. It resumes the same conversation in a new workspace in its folder, without taking focus. `"focus": true` shows it. A live one is left alone (`already live`).

**Close one.** `idle` when it will be used again soon and only its memory should be freed, `sleep` to bring it back later (a cmux restart, a break), `park` to put it away. All three save its screen; the conversation stays on disk.

**Restart one.** `control {"action": "cycle", "sessions": "<id>"}`: closed and reopened on the same conversation, in the same permission mode. Cycle is how a session picks up a new MCP server, skill, or settings. A busy session is refused (`BUSY`, with why and a hint). Then either queue it, `"when": "idle"` (Berth's watcher cycles it as soon as it is idle, within `wait` minutes), or, only when cutting its work off is acceptable, `"force": true` (its turn, background shells and agents, or open question are lost).

**Restart yourself** (to load a new MCP server, skill, or settings you just changed): `control {"action": "cycle", "sessions": "me"}`, then finish your reply and end your turn. Your own pane cannot close itself, so the call is queued: Berth's watcher cycles you as soon as your turn ends, and you come back on the same conversation. Do not ask another session to do it for you. `"when": "now"` with `"force": true` cuts your current turn off three seconds after the call. `session {"session": "me"}` shows what is `queued`; `control {"action": "cancel", "sessions": "<id>"}` takes a queued close back.

**Restart everything idle** (after a change to Claude's settings, skills, or MCP servers): `control {"action": "cycle", "scope": {"folder": "/", "busy": false}}`, show the operator `would` and `skipped`, then the same with `"confirm": true`. Berth reads every pane again right before closing it and skips any that became busy, you, and the Berth screen.

**Drafts are safe.** Every close saves what is typed in the prompt box and pastes it back, unsent, when the session reopens. A draft with a pasted-text or image chip cannot be saved exactly: it blocks the close unless confirmed (`draft` says so in `sessions`).

**Many at once.** Give `scope` (the same arguments as `sessions`) instead of `sessions`:

```json
{"action": "sleep", "scope": {"folder": "Work", "down": "all", "state": ["live"]}}
```

The first call changes nothing: it returns `would` (what would happen) and `skipped` (with why). Show the operator, then call again with `"confirm": true` only if they asked for exactly that. A scope wakes closed sessions and sleeps, parks, or cycles idle live ones. It never closes a busy session, you, the Berth screen, or a process. With `"when": "idle"` the scope queues every session it picks, busy ones too, and Berth's watcher closes each as soon as it is idle.

**Give an open session a prompt.** Look before you send; `send` does the looking:

```json
{"session": "<id>", "prompt_file": "/abs/path/job.md"}
```

Ready (its prompt on screen and no turn running; shells or subagents it started may still run, since the main agent takes a prompt then): it is sent, `"sent": true`. Anything else sends nothing and returns `status` (`working`, `needs_you`, `limited`, `overlay`, `starting`), `why`, and the end of its `screen`, so you see what it is doing before you decide. Then call again with `when`:

- `"idle"`: it waits in the session's mailbox, in order behind any prompts already there, and Berth's watcher types it the moment the session is ready, within `wait` minutes (default 60). Right for a new job. It never replaces another sender's prompt. `session` lists the waiting `prompts` in order; `control` `cancel` takes back your own. (Programs use `berth send`, with a receipt; README **Delivery**.)
- `"now"`: a working Claude takes it as soon as it can, possibly before its current work is done, so Berth puts a line in front asking it to finish that first. `"as_is": true` leaves the line out: for a correction that should change what it is doing now. Grok is refused: a message stops its turn.
- `"interrupt"`: Esc stops the running turn, then the prompt goes in. Only when the operator wants the work stopped, or it is plainly going wrong.

Weigh it from the screen: a session nearly done, or doing something unrelated, waits (`idle`); a note that changes its current task goes `now` with `as_is`; only a turn that is wasting work gets `interrupt`.

`needs_you` (a question, permission, or plan) belongs to the operator: never answer one to get a prompt in. Someone typing in its box (`refused`) is never typed over. A slash command (`/compact <note>`) is one line, typed, into a ready prompt only. Never to yourself; a new session's first prompt is `create`'s.

**Start a new Claude with a job.** Write the job to a file first (the cmux-claude skill's `references/prompts.md` says how), then:

```json
{"title": "Export fix", "cwd": "/Users/you/code/app", "folder": "Work", "model": "opus", "prompt_file": "/abs/path/job.md"}
```

It opens in a new workspace in the working directory `cwd`, filed in the Berth folder `folder` (default: your own), and starts on that prompt. A title is required. `model`: see **Model**. If Claude has never been trusted in that directory, it asks first (its trust dialog): clear that with cmux-mcp (`screen`, then `input` keys; cmux-claude **Dialog**), and Claude then runs the prompt. `agent: "grok"` starts Grok (no prompt: `send` it one once it is ready, `when` `idle`). `agent: "shell"` opens a shell, or runs `command`.

**Fork.** `create {"from": "<id>"}`. The conversation is copied under a new session id and opens in the original's Berth folder, titled with ⑂; the original keeps running. `title` names it. Give the fork its new job afterwards with `send`. It needs at least one turn of conversation (`SESSION_MISSING` otherwise).

`create {"from": "<id>", "cwd": "/abs/dir"}` forks a Claude conversation into another working directory: the fork runs there from its first turn, with that directory's `CLAUDE.md`, memory, settings, and trust, and its transcript is written under that directory's project. The original stays where it was. Not for Grok (`KIND_UNSAFE`): a Grok fork always works in its parent's directory.

**Compact.** Berth does it, end to end; never place `/compact` yourself, typed or pasted. A pasted `/compact <note>` reaches the session as a plain message: on 2026-10-30 a fork read its note as its job and built what the note described.

- Write the note to a file: the task the summary is for (TASK) and, if the session becomes another agent, its seat (SEAT: name, home, who it reports to, whose work it leaves alone); optionally SOURCES (paths) and KEEP OUT (only on purpose, with why). The summarizer decides what to keep: Berth wraps the note in its frame and refuses keep or drop lists. Up to 32 KB, without `/compact`.
- A session, open or closed, or yourself: `send {"session": "<id>", "compact_file": "/abs/note.md", "prompt_file": "/abs/next.md"}`.
- Fork and compact: `create {"from": "<id>", "title": "<name>", "compact_file": "/abs/note.md", "prompt_file": "/abs/job.md"}`. The fork is compacted before its pane opens; the original is untouched. Add `"cwd": "/abs/unit"` to fork it into another working directory in the same step (its transcript goes under that directory's project, and it opens there).
- It returns at once (`compaction.state` `queued`). Berth waits until the session is ready, compacts, checks Claude's own record of the compaction, then sends the prompt. `session` shows `compaction.state`: `done` with `pre_tokens` and `post_tokens`, or `failed` with `why` (Claude's own words when it declines, such as "Not enough messages to compact.").
- From a shell: `berth compact <id> --note-file F [--then-file P] [--fork --title T --cwd DIR] [--background]` does the same and waits for it. From an agent's own shell, add `--background` (a compaction can outlast the shell tool's timeout) and follow with `berth log <id>`; compacting your own session is always backgrounded.

**Move a session to another working directory.** Re-homing an agent (preparing its unit, brief and memory, choosing between move, fork and handoff): `references/moving-sessions.md`. The call: `update {"sessions": "<id>", "cwd": "/abs/dir"}`. The directory must exist. The session keeps its conversation and its Berth folder, and opens in the new directory at its next wake or cycle; the result's `cwd_note` says when. A live one keeps running where it is until then: add `"cycle": true` to cycle it now (a busy one is not cycled, and the result says so; your own session is queued until your turn ends). After the move:

- Same conversation: Claude resumes it with `-r` and has all of it.
- Its transcript stays in the old directory's project in `~/.claude/projects`; new turns record the new directory.
- The new directory's `CLAUDE.md` chain, auto-memory, project settings and hooks, and trust apply. If Claude has never been trusted there, its trust dialog appears at the open: clear it (cmux-claude **Dialog**).
- Never `cd` inside a session and cycle it to move it: a `cd` outside the session's start directory isn't recorded, and one inside it is, so the session may or may not reopen there. Use `cwd`.

Grok sessions cannot change directory: `grok -r` resumes one only in the directory it started in. Start a new Grok session in the new directory (`create {"agent": "grok", "cwd": ...}`) and hand it the work. With a `scope`, `cwd` lists what would change first (with `skipped` Grok rows) and acts with `"confirm": true`.

**File, rename, pin, note.**

- `update {"sessions": "<id>", "folder": "Work/Support"}` refiles it in another Berth folder (`"Inbox"` is "not in a Berth folder"). Its workspace keeps running and its working directory stays the same.
- `update {"sessions": "<id>", "title": "One clear name"}` renames it everywhere: Berth, the cmux sidebar, and the agent's own name. A closed session is renamed when it next opens. Never type `/rename` yourself.
- `update {"sessions": ["<id>", "<id>"], "pin": true}` pins. `update` also takes a `scope` (with the same dry run and `confirm`), except for `title`.

**Urgent: escalate to the operator.** Only when your instructions say to (the operator decides which agents escalate, and for what). When your turn ends with something the operator must act on soon, before your final reply:

```
update {"sessions": "me", "urgent": true, "why": "<one line: what you need from them>"}
```

cmux's sidebar then shows your session red, Urgent, above every other one once your turn ends, and cmux posts a notification with your `why`. Ready alone gets lost among many ready sessions; this does not. The flag comes down when Dominik types a prompt in your box; another agent's message does not clear it. `"urgent": false` takes it back if the need goes away first. Another session can raise it for you by naming you (an orchestrator for its worker). Never raise it for a question you could answer yourself, or as a substitute for finishing.

**Folders.** `folder {"action": "create", "path": "Parent/Child"}` makes every missing level; a bare name makes a top-level folder. `rename` (`name`), `move` (`into`; empty is the top level), `delete` (its sessions and subfolders move up; nothing closes), `archive` (hides a folder with nothing live), `unarchive`.

**Delete.** `delete {"sessions": "<id>"}` lists what would go; `"confirm": true` moves it to the trash (a live one is closed first). Your own session and a Berth screen are refused, with `override` in the error's details; `"force": true` overrides that, only when the operator asked for exactly that (it lists them with a warning first; your own session is trashed, then its pane closes, ending you). `trash {}` lists the trash; `trash {"action": "restore", "item": "<id or title>"}` brings one back, parked, into Inbox, and `wake` then opens it. `forget` deletes for good and needs `confirm`.

## Model

For a new Claude, `model` is the operator's choice when they named one in this chat. Otherwise leave it out: Berth uses Claude's default from `~/.claude/settings.json`, and `opus` when that is unset or a Fable model. Never pass a Fable model unless the operator's own message in this chat asks for Fable for this job. A previous session on Fable is not that request.

## When the berth tools are missing

A session started before the `berth` server was registered does not have the tools. Cycle it to load them, or use the same Berth from a shell, adding `--json` to any command:

```text
berth sessions [--folder F] [--down N|all] [--up N|all] [--branches] [--query Q] [--state live,sleeping] --json
berth play <id> | berth pause <id> | berth idle <id> | berth sleep <id> | berth wake <id> | berth cycle <id> | berth focus <id>
berth fork <id> [--cwd <directory>] | berth move <id> <Berth folder> | berth rename <id> <title> | berth pin <id> | berth unpin <id> | berth follow-up <id|me> <why> | berth urgent <id|me> <why>
berth cwd <id> (show) | berth cwd <id> <directory> [--cycle] (set the working directory)
berth delete <id> | berth trash [list|restore|forget] [<id>] | berth mkdir <Parent/Child> | berth status | berth check
```

## Refuse

- `/fork` or `/rename` typed into any session: use `create {"from"}` and `update {"title"}`.
- `/compact` placed by you, typed or pasted: use **Compact**.
- A prompt typed into another session without looking: use `send`. `when` `interrupt` or `now` only after you read what it is doing.
- Answering another session's question, permission, or plan so a prompt can go in.
- `confirm: true` on a scope, `delete`, or `forget` that the operator did not ask for.
- Sleeping, parking, cycling, or deleting your own session (`you`) unless the operator asked for exactly that.
- A second `create` because the first result looked slow: check `sessions {"query"}` first.
- `create` for a session that already exists: `wake` it.
- Editing Berth's catalog (`~/.local/share/berth`) by hand. If Berth looks wrong, `status {"check": true}` and tell the operator.
- A Fable model the operator did not name in this chat.
- `cd` typed into a session, then a cycle, to move it to another directory: use `update {"cwd"}`.
- `cwd` on a Grok session: it cannot change directory (start a new Grok there and hand over).
- Calling a Berth folder a directory, or a directory a folder, to the operator.
