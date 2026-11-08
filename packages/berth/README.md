# Berth

Berth is the catalog of every cmux session on this Mac (Claude, Grok, shells, processes), filed in nested folders, plus the terminal screen on that catalog (`berth`) and the watcher that keeps it in step with cmux (`berth watch`). Dominik uses the screen; agents use it through the cmux-mcp server (`repo/packages/cmux-mcp`, which imports this package) and the cmux-claude, cmux-grok, and cmux-harness-driver skills. Berth owns program-to-agent prompt delivery; Slack hooks (`~/FORGE/systems/events/slack-hooks`, LaunchAgent `com.bach.slack-hooks`) is its first user, through `berth send` and `berth receipts` (**Delivery**). cmux keeps the terminal. Berth keeps the catalog.

A berth is one piece of work. It outlives the workspace. **Live** means a cmux workspace is running for that record. **Parked** means the workspace is closed and the recipe is in the catalog. **Play** focuses the live workspace, or creates one from the recipe. **Pause** writes the recipe, then closes the workspace. Capture is the point: while `berth watch` runs, every new cmux workspace becomes a live berth in a folder. Nothing closes unless you pause it.

`docs/INDEX.md` maps every file here and the pieces that live elsewhere. In the onboarding kit, its history is in `history/`.

## Install, run, test

```bash
cd repo/packages/berth
uv venv --python ~/.local/bin/python3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
ln -sfn "$PWD/.venv/bin/berth" ~/.local/bin/berth
berth install                  # writes and loads the watcher's LaunchAgent
berth                          # the screen (berth screen is the same)
.venv/bin/python -m pytest     # the suite
```

The suite has three known failures, all older than 2026-10-28: `tests/test_desk.py` (2, golden frames) and `tests/test_terminal.py::test_package_does_not_call_out`. After a code change, restart the watcher (`launchctl kickstart -k gui/$(id -u)/com.bach.berth.watch`) and the screen (end the `berth` process, then type `berth` in its pane). Running agent sessions keep the cmux-mcp code they started with until they restart.

## The Berth MCP

`berth mcp` serves Berth to agents over MCP (stdio, no dependencies): the catalog and every Berth action as eleven tools (`berth/mcp.py`). It is registered for Claude Code (user config) and Grok, so every new session has it as `berth`.

| Tool | Does |
| --- | --- |
| `status` | who is calling (its session and folder), counts by state, cmux, the watcher, drift; `check` runs Berth's consistency check |
| `folders` | the folder tree with each folder's live, sleeping, and parked counts |
| `sessions` | the sessions in a scope, grouped by folder (below); each live one with `status`, what cmux's sidebar shows for it, and `urgent` or `follow_up` when raised. `urgent` and `follow_up` filter |
| `session` | one session in full; `excerpt` adds its saved screen, `history` its recent events |
| `control` | `wake`, `sleep`, `park`, `cycle`, `focus`, and `cancel` (take back a queued close or send): named sessions, or every session a scope picks (a dry run until `confirm`) |
| `send` | a prompt to one live Claude or Grok session, after a look at its pane (`berth/send.py`): sent when it is ready; otherwise nothing is sent and the caller gets its status, why, and the end of its screen, then chooses `when` `idle` (the watcher sends it once ready, through the deferred queue), `now` (a working Claude takes it as soon as it can, with a line asking it to finish first unless `as_is`; refused for Grok), or `interrupt` (Esc on a running turn, then send). Never into a question, permission, or plan, nor over someone typing (cmux's own paste refusal is the check, since Claude's dim suggestion reads like typing). A slash command is typed on one line |
| `create` | start a new Claude (on a first prompt), Grok, or shell session in a folder; or fork one (`from`) |
| `update` | rename one session everywhere, move sessions to a folder, set the note, pin; named sessions or a scope (a dry run until `confirm`). `urgent` (with `why`) says a session needs the operator urgently; `follow_up` (with `why`) says its turn ended with work left or something for the operator; named sessions only (`berth/attention.py`) |
| `folder` | create, rename, move, delete, archive, unarchive a folder |
| `delete` | sessions to the trash (a dry run until `confirm`) |
| `trash` | list, restore, forget |

**Scopes** (`berth/scope.py`). With no arguments `sessions` shows the caller's own folder: the folder of the Berth row whose cmux workspace the agent runs in (`CMUX_WORKSPACE_ID`); outside cmux, everything. `down` adds levels of subfolders (`"all"` for every level). `up` adds ancestors, each with only its own sessions; `branches` adds the ancestors' other subfolders too, cut at the caller's level plus `down`. `query` matches title words, ids, and session ids anywhere. `state`, `agent`, and `fields` filter and widen. Each folder group has its `level` (0 start, -1 parent, 1 child) and what the scope left out (`deeper`, `beside`), so an agent knows where to widen. The default fields are `id`, `title`, `state`, `session` (plus `busy` and `you` when true); `fields` adds `agent`, `folder`, `cwd`, `workspace`, `last_used`, `created`, `note`, `park_reason`, `protect`, `fork_of`, `recipe`, or `all`.

A session is named by its Berth id or its first 4+ characters, its title, its agent session id, its workspace id, or `me`. A scope action skips the caller, the Berth screen, and processes; a named process closes or reruns only with `confirm`.

Skills: the `berth` skill (`skill/SKILL.md`, linked into `~/.claude/skills/berth` and `~/.grok/skills/berth`) teaches agents these tools: scopes, recipes, the command-line fallback, and the refusals. cmux-claude and cmux-grok (in bach-coding-stack) teach the Berth MCP for every session action and cmux-mcp only for the pane (`screen`, `input`), and send agents to the `berth` skill for the rest.

The tools are CRUD over sessions and folders: create (`create`, `folder` create), read (`status`, `folders`, `sessions`, `session`), update (`update`, `control`, `folder`), delete (`delete`, `trash`, `folder` delete). Not in the Berth MCP, on purpose: reading a live pane's screen and pressing keys in it (cmux-mcp's `screen` and `input`, which are cmux's; `input` refuses a prompt into a session that is not ready unless `force`, and points to `send`), editing a session's recipe (`berth recipe`), and moving folders between Macs (`berth offer`, `preview`, `merge`).

`berth sessions` runs the same query on the command line (`--folder`, `--down`, `--up`, `--branches`, `--query`, `--state`, `--agent`, `--fields`, `--json`), for scripts. The Berth half of cmux-mcp (`repo/packages/cmux-mcp`) still works; cmux-mcp's `screen` has no Berth MCP equivalent; `send` replaces `input` for prompts.

## Delivery: prompts from programs and agents

Berth owns program-to-agent prompt delivery (`berth/delivery.py`, 2026-10-30): how a prompt from a program, or from an agent's `send`, gets into one session's prompt box. Slack hooks (`~/FORGE/systems/events/slack-hooks`) is its first user, and the next integrations use the same primitive. Dominik's words: integrations built from the same primitives, "with invariants nothing can break".

- **Typed, not messaged.** A prompt goes into the session's prompt box and is submitted, so the agent reads it as the operator's. It is never a SendMessage.
- **A mailbox per session.** Prompts wait in order, each with its `sender`. A prompt is typed only when the session is ready: its prompt on screen, no turn running, no question, permission, plan or dialog, and nobody typing in the box (cmux refuses a paste over a draft).
- **A receipt per prompt.** Its id, and a state that only moves forward: `queued`, then `typing`, then exactly one of `delivered`, `superseded`, `cancelled`, `expired` or `failed`, with the reason. Each step is an event on the row (`delivery_*`). Receipts are kept 30 days.

The invariants, each tested (`tests/test_delivery.py`):
1. **At most once.** A prompt is claimed in one atomic update before it is typed. One left `typing` by a stopped deliverer is `failed`, never typed again.
2. **In order, one at a time.** Only a session's oldest waiting prompt is typed, at most one per tick. A new prompt never jumps ahead: the MCP's `send` to a ready session with prompts waiting says so, and `when idle` puts it behind them.
3. **Senders never clobber each other.** A prompt is replaced only by its own sender with the same `key` (`superseded`), and taken back only by its own sender.
4. **No prompt lost.** A prompt waits until `--wait` minutes (default 1440, at most 7 days), then `expired`, with the reason. A paste cmux refuses goes back to wait its turn. Once a prompt is in the mailbox its sender always gets the receipt, even if typing it at once then fails.
5. **Woken first, and after a close.** With `wake`, a sleeping or parked session is opened first. A queued close (`berth/deferred.py`) goes before queued prompts.
6. **Senders are told, never poll.** `--on-settle <command>` (a JSON list, or words split like a shell's) runs once, detached, when the receipt ends `delivered`, `expired`, `failed` or `cancelled`, with `BERTH_DELIVERY` and `BERTH_STATE` set. Not when a sender replaces its own prompt. Dominik, 2026-10-30: "There should be no cycle where every 5 minutes we check for something". Slack hooks passes `slack-hooks nudge`.

**For programs**, plain commands with JSON out (`--json`). They need no cmux environment: Berth finds cmux's CLI where cmux installs it.
```bash
berth send "Mail Amplify Orch" --text "…" --sender slack-hooks --key amplify --meta '{"rows": [12]}' --wake \
           --on-settle '["/path/to/slack-hooks", "nudge"]'   # run once when the receipt ends
berth receipt <id>                       # one prompt: state, position, why, the meta it was sent with
berth receipts --sender slack-hooks --open
berth unsend <id> --sender slack-hooks   # take back a queued prompt; only its sender can
```
`berth send` types the prompt at once when the session is ready and nothing waits ahead of it (the receipt says `delivered`). Otherwise it queues it (`queued`, with `position`), and the watcher types it each tick it can. `meta` is the sender's own; Berth keeps it and returns it with the receipt. Slack hooks puts its ledger row ids there.

A waiting receipt carries `stalled` when the watcher is the reason it waits: not ticking, or its delivery job failing, with the fix.

**For agents**, the MCP's `send`: `when: idle` puts the prompt in the mailbox under the caller's title. `session` lists a session's waiting `prompts` in order. `control cancel` takes back the caller's own.

Until 2026-10-30 a held prompt took the session's single queued slot, and any new request replaced it, whoever sent it. Slack hooks worked around that by matching its prompts by their first words, and by guessing that a prompt which disappeared had been typed. Held sends from then moved into the mailbox when the catalog opened.


**Prompts to a busy session (2026-11-05, Dominik: "you can send prompts when the agent is busy").** `berth send ... --busy` also types the prompt while a Claude session is working, where Claude queues it, with a line before it asking the agent to finish its current work first (the MCP's `send` `when: now` does the same). It still goes in order, one at a time, with its receipt and `--on-settle`, and never into a question, a permission, a plan, a menu or a box holding a draft. Grok (a prompt mid-turn would stop it) and slash commands wait for a ready prompt; the receipt's `busy` says which applies. Without `--busy` nothing changed. The 2026-11-04 "message" mode (`--message`, `berth inbox --hook`) was taken out the same day on Dominik's word ("there should be no such thing a a “message”"); the `delivery.kind` column stays for MCP servers started before. A session in the trash answers `send` with `NOT_FOUND`, and whatever was waiting for it fails (`the session was deleted`) at the next tick rather than expiring. `berth receipt` and `berth unsend` take the id's first 4+ characters.

## Config, data, secrets

- **Config:** nothing to edit. The sort the screen's `s` picks is stored in `view.json` with the data. Berth needs cmux's `terminal.autoResumeAgentSessions` off, which the kit's `tools/install.sh cmux` sets.
- **Data:** `~/.local/share/berth/` (mode 0700): the catalog `berth.sqlite` (0600) and its `.bak-*` backups, `observer.json`, `invariant.json`, `view.json`, `selected.json` (the last workspace switch), `prompts/` (first messages for sessions the MCP's `create` starts, kept a day), `programs.json` (each pane's latest cmux program status, for busy), `events.seq`, `watch.log`, `watch.err`, `watch.pid`, `watch.heartbeat`. FORGE's rule would put it in `~/.berth/`; moving the live catalog waits for Dominik's OK.
- **Secrets:** none.

## Installed outside FORGE

> **In the onboarding kit:** this table is Dominik's Mac. What the kit installs, and how to undo each piece, is `docs/UNINSTALL.md` at the kit's top level.

| What | Where | Undo |
| --- | --- | --- |
| The `berth` command | `~/.local/bin/berth` → `.venv/bin/berth` (symlink) | `rm ~/.local/bin/berth` |
| The watcher, a LaunchAgent running `berth watch` (the hub's recorded exception; `berth install` writes it, a copy is in `launchd/`) | `~/Library/LaunchAgents/com.bach.berth.watch.plist` | `launchctl bootout gui/$(id -u)/com.bach.berth.watch`, then delete the file |
| The shared MCP server, a LaunchAgent running `berth mcp --http --port 4792` on 127.0.0.1 (Dominik's yes, 2026-11-07: "Make it a background server"; `berth install --mcp` writes it, a copy is in `launchd/`). It serves `/berth` and `/cmux-mcp` to the sessions registered for it. Health: `berth mcp --check` (and `shared MCP server` in `berth status`). Logs `~/.local/share/berth/mcp-http.log` and `.err`. Measured at install: 0.0% CPU, 19 MB | `~/Library/LaunchAgents/com.bach.berth.mcp.plist` | `berth install --mcp --uninstall`; sessions registered for it then lose the berth and cmux-mcp tools until they're registered back on stdio |
| Data | `~/.local/share/berth/` | stop the watcher, then delete the folder (this deletes the catalog) |
| The Berth MCP (`berth mcp`) for Claude Code | `~/.claude.json` → `mcpServers.berth` | `claude mcp remove berth -s user` |
| The Berth MCP for Grok | `~/.grok/config.toml` → `[mcp_servers.berth]` | delete that table |
| The `berth` skill | `~/.claude/skills/berth` and `~/.grok/skills/berth` → `skill/` (symlinks) | `rm` both links |
| cmux-mcp for Claude Code runs this `.venv`'s Python with `BERTH_ROOT` set to this folder | `~/.claude.json` → `mcpServers.cmux-mcp` | `claude mcp remove cmux-mcp -s user` |
| cmux-mcp and auto-mode for Grok both run this `.venv`'s Python; cmux-mcp also gets `BERTH_ROOT` | `~/.grok/config.toml` → `[mcp_servers.cmux-mcp]`, `[mcp_servers.auto-mode]` | delete those tables (auto-mode then needs a Python of its own) |
| ⌘⇧B in cmux goes to the Berth screen, starting `berth` if none is open, and the cursor lands on the session you pressed it from; cmux's own ⌘⇧B (Jump to Last Prompt) is unbound | `~/.config/cmux/cmux.json` → `actions.berth`, `shortcuts.bindings.jumpToLastPrompt` | delete the action; `cmux config unset shortcuts.bindings.jumpToLastPrompt` |
| ⌘⇧X in cmux dismisses the follow-up of the session selected in cmux. `berth-keys` (`keys/BerthKeys.swift`, built into `build/` on the watcher's start) holds the key only while cmux is the frontmost app and runs `berth follow-up selected --dismiss` in the background: nothing is typed and no tab opens. It is a child of the watcher, not a LaunchAgent of its own; it needs no Accessibility permission (`berth/keys.py`) | `~/.local/share/berth/keys.json` (state, in `berth status` as `keys`), `keys.log` | stop the watcher; delete the two files |
| The Berth sidebar in cmux (`berth sidebar install`) | `~/.config/cmux/sidebars/berth.js` → `cmux-sidebar/berth.js` (symlink), selected in cmux's defaults (`cmuxExtensionSidebarProviderId`) | `berth sidebar uninstall` |
| cmux's own rows quieted, notifications in Berth's words (`berth sidebar install`; it backs up `cmux.json` first) | `~/.config/cmux/cmux.json` → `sidebar.compactAgentStatus`, `sidebar.showNotificationMessage`, `sidebar.showProgress`, `sidebar.showLog`, `notifications.agentIdleReminder`, `app.reorderOnNotification`, `notifications.hooks` (id `berth`) | `berth sidebar uninstall` |
| The old path, for processes started before the move (temporary) | `repo/packages/berth` → this folder (symlink) | `rm` it once `ps -axwwE -o command \| grep -c '[B]ERTH_ROOT=~/work/bach-coding-stack/packages/berth'` prints 0: the MCP servers started before the move carry the old path in their environment, not on their command line |

cmux-mcp and auto-mode importing this folder and running on its `.venv` is a path into another project. It goes away when they move to FORGE.

## What you see

One terminal, one tree. Each row is labeled Grok or Shell. Inbox is the root whose note is "not in a folder". Enter on a parked Grok row records play. Enter on a live Grok row records focus. Enter on a Shell row records play and says it opens a shell. `p` on a quiet live row records pause. A busy row, or a process, shows the excerpt and waits for `y`. The screen records that intent. It does not close a workspace and it does not create one.

This process runs the intent while the screen is open. Play calls play. Pause calls pause. A confirm passes force, so a busy pause you accepted does close. Focus selects the live workspace. Success deletes the intent row. `p`, `S`, and `c` on a busy session (a turn, background work, an open question, or a screen Berth cannot read) first show why it is busy and wait for `y`, which closes it anyway; `n` leaves it (2026-10-29: before, `c` and `p` recorded the close unconfirmed and the engine refused it, so a busy or unclear session could not be cycled from the screen). `c` with marks asks once when any marked session is busy. Any other failure writes an event of kind `intent_failed` and leaves the row.

`n` creates a folder inside the folder under the cursor (`a/b` makes both levels, a leading `/` makes a root; Inbox holds no folders). `m` moves the marked berths onto a folder, or, on a folder with nothing marked, moves that folder under another (`/` is the root; a folder cannot go inside itself). `P` pins or unpins (below). Those write the catalog directly. `v` marks sessions; Enter with marks opens every marked parked session, Claude, Grok, or shell, and the first takes focus (a process still needs its own Enter and confirm). `f` cycles all, live, live + sleeping, sleeping, and parked. `s` switches the order between **last used** (the default) and by name. `p` on a folder confirms once, then parks every live session in that folder and all of its subfolders (the Berth screen stays).

Columns line up on every row, measured in terminal cells (a wide character takes two). Every row keeps the three-cell color swatch, blank when a folder has no color (Inbox). The name column is as wide as the longest name in the whole tree, indent included, folded folders too, so folding never moves it; it narrows, and long names end in `…`, only when the screen cannot hold it next to the state, flag, and kind columns and the start of a note. The note takes the rest, and the folder path shows on the right only when the note still has room. `tests/test_columns.py` checks this at every width from 40 to 240 columns, and reads the real screen back cell by cell through a terminal emulator (pyte, a dev dependency).

Every text box (new folder, rename folder, rename session, search) edits like a prompt box (`berth/lineedit.py`). Every key types, `j`, `k`, `q`, and space included. ← → move; ⌥← ⌥→ move by word; ⌘← ⌘→ (or ctrl-a ctrl-e, Home End) go to the ends. Shift with any of those selects, and typing or a paste replaces the selection. Backspace and Delete work; ⌥⌫ (ctrl-w) deletes a word and ⌘⌫ (ctrl-u) everything before the cursor, ctrl-k everything after. ctrl-c copies the selection to the Mac clipboard (with nothing selected it cancels), ctrl-x cuts, ctrl-v pastes, ctrl-z undoes, ctrl-y redoes. ⌘V and Wispr Flow arrive as one bracketed paste; line breaks become spaces. The mouse works as in Claude Code's and Grok Build's prompt: a click puts the cursor there, a drag selects (and copies), a double click selects a word, a triple click everything, and Backspace, Delete, or typing then act on that selection. Shift-drag still makes a plain terminal selection. In search, ↑ ↓ still walk the list. The keys follow readline, which is also what Ghostty, and so cmux, sends for the Mac shortcuts.

**Idle (2026-11-05, Dominik: "something in between live and sleeping").** An idle session is closed to free its memory, and you will use it again soon. Its pane closes and its recipe and screen are saved as for sleep (`park_reason` `idle`), but it stays with the live sessions: `f` has `live + idle` and `idle`, the row and the header say `idle`, and every agent's `sessions` shows it as `idle` with no filter. `i` makes the session under the cursor idle (with marks, the marked ones; on a folder, every live session in it, after a y); on a closed session `i` makes it idle without opening it, and `S` or `p` turn an idle one sleeping or paused. Enter opens it. A cmux restart leaves idle sessions idle; `W` wakes only sleeping ones. `berth idle <name>`, the MCP `control` `idle` (with `when: idle` to wait until it is not busy) and `wake` do the same. The screen's top bar no longer shows each account's usage (Dominik, 2026-11-05); the account view's bands still do.

A closed session is idle, sleeping or parked. Sleeping means "closed for now": its pane is closed and its recipe and screen saved exactly as a pause does, but it is meant to come back. `S` puts the session under the cursor to sleep (with marks, the marked ones; on a folder, every live session in it and its subfolders, after a y). `Z` puts every live session to sleep except the Berth screen, after a y: the step before an update or a cmux restart. `W` wakes every sleeping session, each in its own workspace in its folder, without taking focus; Enter wakes one. On a closed session, `S` makes it sleeping and `p` makes it parked (paused, put away), and nothing opens. A cmux quit or crash puts what was open to sleep, and Berth wakes those by itself when cmux is back, as before; sessions you put to sleep yourself stay asleep until `W`. Sleeping rows stay in the tree, marked `sleeping`, and `f` has a sleeping filter; the parked filter shows paused rows only. `berth sleep <name>` / `--all`, `berth wake <name>` / `--all`, and the MCP `sleep` and `wake` tools do the same.

Deleting never loses a session. Every removal (`d`, `berth delete`, the MCP `delete`, and the rows Berth folds into another) copies the row into the trash first (`berth/trash.py`), where it stays 30 days after it was deleted. ctrl-t opens the trash: what was deleted, when, how many days it has left, and the folder it was in. Enter brings one back, parked, into Inbox, under its own id with its session, recipe, note, and history; Enter on it in the tree then reopens the conversation if Claude or Grok still has it. `D` deletes one for good, after a y. `berth trash`, `berth trash restore <id or title>`, `berth trash forget <id or title>`, and the MCP `trash` and `untrash` tools do the same. Deletions from before the trash existed were rebuilt from their delete events (title, kind, session, folder when recorded; no note or excerpt), so the last 30 days are in it too.

`c` cycles a live session: it turns it off and on again. The engine pauses it (the recipe and last screen are saved, the workspace closes), then plays the same session in a new workspace in the same folder, without taking focus. Claude resumes with `claude -r`, Grok with `grok -r` and its flags. A turn in progress stops. With marks, `c` cycles every marked live session. A process asks first, because its command runs again, and marks skip it. `berth cycle <id or query> [--confirm]` and the cmux MCP `cycle` tool do the same.

Switch to the screen from a session (⌘⇧B, or a click on Berth in the cmux sidebar) and the cursor lands on that session's row, opening its folder if it is folded, within half a second (`berth/arrival.py`). The watcher keeps cmux's last `workspace.selected` in `selected.json`; the screen acts on a switch to its own workspace once, only while it is under ten seconds old, and only in the tree. A text box, the trash, or a filter that hides that session leave the cursor where it was.

### Busy, drafts, and closing safely

Busy is read from each pane, not from hook files (`berth/activity.py`, 2026-10-28). The watcher looks at every live pane every three seconds and stores busy, why, and whether a draft is typed in the prompt box in the `runtime` table; the screen, the MCP (`busy`, `busy_why`, `draft` on each session, and a `busy` filter), and every close use it. A session is busy when:

- a turn is running: cmux's program status for the pane is `working` (the progress Claude reports to the terminal, repeated every few seconds of a turn, kept in `programs.json`; a report older than 15 minutes is stale), or Claude's status strip says `esc to interrupt`, or Grok's says `send a message to interrupt`. The strip alone misses a turn while you type the next message: Claude hides `esc to interrupt` then;
- background work it started is still running: Claude's strip counts shells (`· 3 shells`), and its subagents' own transcripts say they run (`<session>/subagents/agent-*.meta.json` without `finished`, its transcript written in the last 10 minutes). Not the strip's `← N agents`: since Claude Code 2.1.31x that is its agents view, every background session on the Mac. A stale background job put "Waiting on 1 subagent" on 34 idle sessions after a hard sleep (2026-11-07);
- a question, a permission, or a folder-trust dialog waits for an answer (`blocked`, or on screen);
- in a shell, a command is running (a foreground process on its terminal that is not the shell);
- the pane shows something Berth does not recognize as a prompt (a blank pane is not busy).

Every close (`p`, `S`, `c`, `d`, `berth pause`, the MCP) reads the pane again right before closing. A busy session is closed only when confirmed (`y` in the screen, `--confirm`, the MCP's `confirm`); a scope action skips it. What is typed in the prompt box is saved on the row (`attachments.draft`, plus a `draft_saved` event as a second copy) and pasted back, unsent, once the session is open, idle, and its prompt empty (`identity.restore_drafts`); it never pastes over other text, and keeps a draft it could not put back as `draft_unrestored`. A draft holding pasted text or an image chip, or taller than the screen, cannot be read exactly, so it blocks a close unless confirmed. A Claude session also reopens in the permission mode its strip showed (`--permission-mode`), so a cycle comes back as it went down.

Some closes run later, in Berth's watcher (`berth/deferred.py`, the `deferred` table), because launchd keeps the watcher outside every pane:

- **Yourself.** A session cannot close its own pane: the process asking (its MCP server, or `berth cycle` in its shell) dies with the pane before a cycle reopens it. So `control` on `"me"` (and `berth cycle` of its own pane) is queued: the watcher cycles, sleeps, or parks the session as soon as its turn ends. `"when": "now"` with `"force": true` does it a few seconds after the call, cutting the turn off. (2026-10-29: Settings Agent asked to be cycled to load a new MCP server, and Berth could only refuse with `PROTECTED`.)
- **When idle.** `"when": "idle"` queues a close for any session; the watcher reads its pane each tick and acts once it is idle, within `wait` minutes (default 60). On a scope it queues every session the scope picks, busy ones too, each closed as it goes idle.
- **Force.** `"force": true` closes a busy session (or one whose draft cannot be saved exactly) now, cutting off its turn, background work, or open question. With `when idle`, it acts at the deadline anyway. Outcomes are events on the row: `deferred_done`, `deferred_gave_up`, `deferred_failed`, `deferred_dropped`; the MCP's `session` shows a pending one as `queued`.

To restart everything that is safe to restart (after changing Claude's settings, skills, or MCP servers): the MCP's `control` with `{"action": "cycle", "scope": {"folder": "/", "busy": false}}`, then again with `"confirm": true`.

### Pinned sessions

A session carries one mark: pinned (`P` in the screen, `berth pin`, the MCP's `update` with `pin`; `berth/pins.py`). A pinned session stays where it is in the tree, marked 📌, and comes first in cmux's sidebar, under "Pinned", colors unchanged. The pin is Berth's alone (2026-10-31, Dominik's decision). Berth's sidebar order puts pinned sessions first, and Berth's sidebar draws the Pinned group from the pin each row's status carries. cmux's own workspace pin is never set any more: cmux keeps pinned workspaces ahead of unpinned ones and orders only inside each group, so its rule, not Berth's, used to decide part of the order, and two copies had to be kept in step. Pin or Unpin in the sidebar's menu still works: Berth reads a pin made in cmux as a request, turns its own pin over, and clears cmux's. A pinned session that closes and opens again is pinned again. The ! (protect) and * (new) marks are gone (2026-10-28); the `protect` column is no longer shown or set.

### Order

By default the tree is sorted by when you last used each session, newest first, at every level. Folders and sessions are interleaved: a folder is as recent as the newest session anywhere inside it, so a subfolder with a newer session sits above an older session in its parent, and the top-level folders are sorted the same way. Each session row shows how long ago it was used (`now`, `43m`, `2d`). The watcher keeps `last_used_at`: the newest of when Berth played or focused it, cmux's last submitted message in the pane, the agent's last hook event, and the newest message in the Claude transcript or Grok's `last_active_at`. For Claude, the transcript counts a slash command you ran and a message you queued while it worked, its forks' transcripts count (what you said to a fork, and the fork's work), and so does Claude's prompt history (`~/.claude/history.jsonl`, everything typed into its box). 2026-10-29: a prompt sent with `/subtask`, which Claude runs in a fork, reached none of the first four, so the session did not move. It never moves backward.

The cmux sidebar follows the same order. It is flat, so it lists the live sessions in the order a walk down the Berth tree meets them: a folder's sessions stay together, and the folder holding your most recently used session comes first. The watcher looks every 30 seconds, not every tick, so rows do not move under the pointer, and reorders only when cmux's order differs, with one atomic `cmux reorder-workspaces`; a new sort from `s` applies at once. Pinned workspaces stay above the rest, in Berth's order among themselves; workspaces Berth does not hold go last. `s` in the screen switches both the screen and the sidebar (stored in `view.json` next to the catalog). `o` switches how the sidebar is ordered (stored in `view.json` too): following the tree (the default, as above), or folder by folder, where every folder at any depth stands on its own, ordered by its own newest session, with its sessions newest first. Folder by folder, activity in a subfolder does not move its parent's sessions, so a top-level folder like BERTH no longer jumps every time something under HOMESTEAD is used. The tree in the screen keeps its order either way; the header says `sidebar by folder` while that order is on. Dragging a workspace in the sidebar does not stick: Berth puts it back where the tree says. `q` quits from the tree.

## Requirements

- macOS
- Python 3.12 or newer
- cmux 0.64 or newer, running, with the CLI on `PATH`
- The cmux socket. On this machine that is `~/.local/state/cmux/cmux.sock`. The CLI doc still mentions `/tmp/cmux.sock`. Berth uses the socket that exists, or `CMUX_SOCKET_PATH` when that is set.

Berth talks to cmux only through the `cmux` binary. It does not edit `~/.grok/config.toml`. It does not signal a Grok process.

A checkout also keeps a copy of the agent plist at `launchd/com.bach.berth.watch.plist`.

Tests need the dev extra (`uv pip install --python .venv/bin/python -e '.[dev]'`).

## Capture

`berth watch` blocks. It subscribes to `workspace.created` and `workspace.closed` (the names measured on cmux 0.64.25). A create adopts that workspace only. A close parks the matching row, clears the workspace id, and leaves the Grok session id and the recipe.

The first start records the current event sequence and does not replay the retained buffer; a later start resumes from the cursor. Events are only hints. Every tick the observer (`berth/observer.py`) also compares the catalog with `cmux workspace list`, and adopts any open workspace that no row knows about once it has seen it on two ticks in a row, so its hook file has time to land. That includes workspaces that were already open when the watcher first started, and ones created while it was down.

`berth adopt` catalogs the workspaces you name, or every workspace when you pass no `--workspace`. It closes nothing. A second adopt of the same cmux workspace UUID does not insert a second row. It creates a root folder for each current cmux group that does not already have one, files members there, and moves a live Inbox row into that folder when its workspace is a member. A row already in another folder stays. A parked row stays.

`berth adopt` does at once what the observer does within two ticks, and also files rows by cmux group.

## Folders

A new workspace is filed immediately:

1. If its cmux group name matches a root folder, that folder wins. Adopt creates the root folder when the name is missing. Play does not create a cmux group and does not add the workspace to a group. The folder stays in the catalog. Inbox stays ungrouped.
2. Otherwise the longest folder `default_cwd` that prefixes the workspace cwd wins.
3. Otherwise the berth goes to **Inbox**.

`/tmp` matches Inbox unless you set a folder's `default_cwd` to a prefix of `/tmp`.

```bash
berth mkdir "UNI/6.S978"                # makes UNI too if it is missing
berth move <id> "UNI/6.S978"
berth move-folder "UNI/6.S978" Archive  # now Archive/6.S978, with its sessions
berth move-folder "Archive/6.S978"      # back to the top level
berth rename-folder "6.S978" "6.S978 Fall"
```

Folders nest to any depth. Inbox means "not in a folder" and holds no folders. In the screen, `n` makes a folder inside the one under the cursor, `m` on a folder picks it up to drop elsewhere, and `r` renames it. Agents do the same through the cmux MCP: `mkdir` with a `Parent/Child` path, `move_folder`, `rename_folder`, `rmdir`, and `park_folder` (every live session in a folder and its subfolders, after a plan).

### Colors

Folder colors are computed, not chosen. The watcher recomputes them from the folder tree on every tick, stores them on the folder rows, and sets each live workspace's cmux color to its folder's color (Inbox is uncolored). Adding, removing, or moving a folder re-balances every color. `berth colors` prints the tree with its colors and how far apart they are.

- Every folder, at every depth, gets an equal slot of the hue wheel. Slots run in tree order, so a family is one contiguous hue region with the parent in its middle, and a family's arc is exactly its share of all folders: a folder with 100 subfolders gets 101 slots and a folder with one subfolder gets 2. One empty slot separates top-level families. Adding a folder anywhere moves every slot.
- Within its slot, each folder's lightness and hue are optimized in OKLab so that the closest two folders anywhere are as far apart as possible. Folders that sit next to each other in the sidebar order, a parent and its child, and adjacent siblings are separated first. Every color is as vivid as sRGB allows at its lightness, never gray. The optimizer is deterministic (the same tree always gives the same colors) and cached, so it runs only when the tree changes.
- Distances are measured in OKLab. 0.08 reads as different at a glance; 0.02 is about the smallest difference you can see side by side. `berth colors` reports the closest pairs: adjacent roots, parent and child, adjacent siblings (all kept at or above 0.08), the closest siblings in one family, and the closest two folders anywhere (`any two`). Past about 250 folders only the separation of related folders is guaranteed, and a family of hundreds runs out of colors that differ at a glance.

The method combines equal tree-order hue slots (instead of the per-level splitting of Tennekes and de Jonge's "Tree Colors", which narrows at every level) with repulsion-energy spreading and maximin polishing (Glasbey et al. 2007, iwanthue) inside those slots. See `berth/palette.py`.

`berth rmdir <path>` deletes that folder. Berths in it move to the parent folder. A root folder's berths move to Inbox. Child folders move up one level, and a child whose name already exists there is merged into that folder. No berth row is deleted and no workspace is closed. Inbox returns `FOLDER_NONEMPTY`. `berth archive` hides a folder only when the subtree has no live berth (`FOLDER_LIVE` when it does).

## Claude sessions and forks

Berth trusts Claude Code's own records over the cmux hook. Claude writes `~/.claude/sessions/<pid>.json` for each running process with the conversation it is really on. The hook only knows the id a process had when it started, which goes stale after `/resume`, a fork, or `/clear`, sometimes to an id that never gets a transcript. Every tick the watcher (`berth/claude_sync.py`):

- points each live Claude row at the conversation its pane is showing: the pane's outermost interactive Claude, or the background job the pane is attached to. A `claude -p` that the pane's Claude runs through its Bash tool is a Claude process under the same pane but not the pane (2026-10-29: such runs had parked the pane's row as `replaced` and adopted the scratch conversation, once per run);
- corrects a row whose id was never a conversation in place, and parks a real conversation the pane left as `replaced`;
- gives each Claude background job (`claude --bg`, or a fork Claude runs in the background) a `bg` row. Enter attaches to it with `claude attach`;
- files new rows with their family. A fork's transcript starts with the same message as its parent's, so it lands in the parent's folder, not Inbox.

Play refuses a Claude id with no transcript (`SESSION_MISSING`) instead of opening a workspace that exits at once, and runs in the transcript's own directory, unless a working directory was set on purpose (**Working directory**, below). `berth check` reports a parked Claude row with no conversation as `dead_session`, and the watcher writes on that row's note that its conversation is gone and it cannot reopen (a note you wrote is left alone). A play, or any other screen action, that fails in a way that cannot clear by itself is tried once: the screen's message line says what failed and why, and Enter tries again.

To fork from Berth: `F` on a Claude or Grok row in the screen, `berth fork <id or query> [--title ...] [--cwd <directory>]`, or the Berth MCP's `create` with `from` (and `cwd` to fork a Claude conversation into another working directory). Berth picks the fork's session id first and hands it to the agent, so the new row knows its conversation before the pane opens:

- Claude: `claude -r <id> --fork-session --session-id <new> -n "<title> ⑂"`.
- Grok: `grok -r <id> --fork-session -s <new>` plus the flags the source plays with. Grok has no name flag, so Berth types `/rename` once the fork is on screen.

The fork opens in a new workspace in the same Berth folder, and the original is untouched. A parked fork resumes itself; one whose conversation was never written forks its parent again under the same id. Agents fork through the same path: the `cmux-claude` and `cmux-grok` skills tell them to call the MCP `fork` tool and never to type `/fork` into a pane. Claude's in-app `/fork` makes a headless background copy with no workspace, named after the first words of its prompt. Berth still lists one: it becomes a `bg` row named `<parent> ⑂`, and its note says whether the job is running or stopped. Berth keeps no background agents: Enter on a `bg` row stops the background copy (`claude stop`, which keeps the conversation) and resumes the conversation as a normal session in a pane, and `berth check` reports any running background job as `background_job`.

## Working directory

A session has a Berth folder and a working directory, and they are independent. The Berth folder (`folder`) is where it is filed in the catalog and never touches the disk; `berth move` and the MCP's `update {folder}` change it. The working directory (`cwd`) is the absolute path on disk it runs in, which for Claude decides the `CLAUDE.md` chain, auto-memory, project settings and hooks, and trust. Nothing in Berth calls one by the other's name, and every MCP result that shows `folder` shows `cwd` next to it.

Before 2026-10-29 nothing could set a session's directory: every Claude play copied the transcript's last recorded `cwd` onto the row, and Claude records a new one only when it runs somewhere new. Now (`berth/workdir.py`):

- `update {"sessions": ..., "cwd": "/abs/dir"}` (MCP) or `berth cwd <id> /abs/dir` (CLI) sets it. The directory must exist. The row keeps the choice in `attachments.cwd_set`, and an event `cwd_set` records from and to.
- The next play (wake, or a cycle's reopen) opens the workspace there and resumes the same conversation (`claude -r`). Play keeps the chosen directory over the transcript's last `cwd` until Claude has recorded a turn there (`cwd_confirmed`); a hook re-pointing the row does not undo it either.
- A live session keeps running where it is until then. `"cycle": true` (MCP) or `--cycle` (CLI) cycles it now; a busy one is not cycled and the result says so, and a session's own pane is queued for the watcher (`berth/deferred.py`). Results carry `cwd_note`: set, effective at the next open, or open there.
- A scope (`update {"scope": ..., "cwd": ...}`) lists what would change first and acts with `confirm`.
- `create {"from": ..., "cwd": ...}` (MCP) or `berth fork <id> --cwd /abs/dir` forks a Claude conversation into that directory: the fork opens there and its transcript is written under that directory's project. Without `cwd`, a fork works in the source's directory, as before.
- Claude's transcript of a moved session stays in the old directory's project in `~/.claude/projects`; Berth finds it there (`identity.claude_transcript` looks in every project).

Grok is refused (`KIND_UNSAFE`): `grok -r <id>` resumes a session from any directory, with or without `--cwd`, but it keeps working in the directory it started in, and a Grok fork lands in its parent's (tested by script 2026-10-29). Moving the closed session's files from `~/.grok/sessions/<old>/<id>` to `~/.grok/sessions/<new>/<id>` did make it work in the new directory, but that rewrites Grok's own store, so Berth does not do it. To move Grok work, start a new Grok session in the new directory and hand it over. Agents other than Claude are refused too, until tested; shells and processes simply open in the new directory.

## One Berth screen, and deletes that can always be overridden

There is one Berth screen, and it lives in the workspace named "Berth" (`berth/screens.py`, 2026-10-30). Dominik ran `berth` in a workspace titled "~" while the main screen ran. Two screens drained one intent queue, the extra one was filed as a shell ("Running python") and then under two names, and it could not be deleted. His words: "it should be impossible to create two Berth sessions."

- **One at a time.** The screen holds an exclusive lock on `screen.lock` in the data folder for its whole life (the OS lets go when it dies). A second `berth` is refused, says where Berth is open, and switches there.
- **Its home.** ⌘⇧B goes to the workspace named "Berth". `berth` typed anywhere else goes there instead, and starts the screen if that pane sits at a shell prompt. If no workspace is named "Berth", the one it was typed in becomes home: renamed "Berth" in cmux and in the catalog, so the names never differ.
- **The MCP never creates a screen:** `create` with the command `berth` is refused, pointing at `control focus` (or `wake`) on the row titled Berth.
- **Two anyway** (a race, a screen started before this) is the `two_screens` violation. `berth check` and the watcher report it with the way out, the screen's header shows "⚠ 2 Berth screens", and cmux's sidebar shows every screen red ("Two Berth screens: delete one").
- **Intents name their screen.** Every intent records the screen that wrote it (`detail.by_workspace`), and the drain judges "the caller" by that screen.

No delete is impossible. His words: "I should never be in a case where it's impossible to delete something from Berth, even if it's a safety check." A Berth screen and the caller's own workspace are protected, and each surface overrides that after a warning:
- **The screen:** `d` on a protected row says why. `!` deletes it anyway, `y` deletes only the unprotected rows, `n` cancels.
- **The MCP:** `delete` refuses with `override` in its details. The same call with `force` lists the protected rows and a warning, and with `force` and `confirm` deletes them.
- **The CLI:** `berth delete --force`.

Deleting your own pane trashes the row first and closes the pane last. A deleted row can come back from the trash.

## Claude accounts: one config folder each

Claude Code keeps one login per config folder, so each of Dominik's Claude accounts gets one: `~/.claude`, and for example `~/.claude-personal`, which links to `~/.claude` for everything but the login (2026-11-03, for the Accounts Agent; `berth/configdir.py`).

- Berth records the folder on each Claude row (`attachments.config_dir`; none means `~/.claude`) and starts the session with `CLAUDE_CONFIG_DIR` set to it: `create` with `config_dir`, every open and cycle, a fork (it inherits its source's folder), a headless compaction.
- A new Claude session (`create` with no `config_dir`) starts on the **default account**'s folder: `default_account` in Settings Agent's account-switch `config.json` (homestead since 2026-11-04; set with `claude_account.py default-account <slot>`). The folder is recorded on the session, so changing the default later never moves it. `config_dir: "default"` still means `~/.claude`. Sessions that never had a folder stay on `~/.claude`; forks keep their source's.
- To move a session to the other account: the MCP's `update` with `config_dir` and `cycle` true, or `berth config-dir <id> <folder> --cycle`. The cycle waits in the idle queue until the session is safe to close. `default` moves it back to `~/.claude`.
- `sessions` with `fields: ["config_dir"]` and `session` show each Claude session's folder.
- Which **account** a session is on comes from Settings Agent's account-switch files (`berth/accounts.py`): `sessions` with `fields: ["account"]` (`account_stale` when it opened on `~/.claude` before the last switch), the Berth screen's agent column (`Claude hom`, `*` when stale), and each account's 5h/7d usage in the screen's header. **The account view** (2026-11-05): `a` in the Berth screen groups it by account, each account a colored band with its own copy of the folders it has sessions in and every row shaded in its color; `a` again goes back. The screen's alone: cmux's sidebar never changes. Details: `docs/ACCOUNTS-AND-SIDEBAR.md`.
- Berth reads what Claude writes from every `~/.claude-*` folder that holds Claude's files, not only `~/.claude`: running sessions (`sessions/`), transcripts (`projects/`) and prompt history (`history.jsonl`). A folder linked to `~/.claude` is read once.
- A moved session resumes its conversation from the new folder's `projects/`, so that folder must link `projects/` to `~/.claude/projects`. Without the link, `claude -r` there finds no conversation.

## The session standard: auto mode, xhigh effort

Every Claude session runs in permission mode auto (never bypass) at effort xhigh (Dominik, 2026-10-31; Settings Agent owns the standard and the settings behind it, Berth watches and corrects; `berth/standard.py`).

- **Read:** the mode from each pane's status strip, every tick (`runtime.mode`); the effort from the `effort` on Claude's last reply in its transcript; and `effortLevel` plus each `modelSettings.<model>.effortLevel` in `~/.claude/settings.json`.
- **Report:** `berth standard` lists each session off the standard and each setting below xhigh. `berth check` and the watcher's drift show them too (`off_standard`, `settings_effort`).
- **Correct (on unless `berth standard --correct off`):** only a session that is ready, holds no draft, is not the workspace selected in cmux, and has not written to its transcript for a minute. A session in bypass is set to reopen in auto and cycled through the idle queue; one below xhigh gets `/effort xhigh` typed through its mailbox (sender `berth-standard`). Each at most once an hour per session. A setting below xhigh goes to Settings Agent's mailbox once. Plan mode or accept-edits is reported, not changed: someone chose it.

## Watcher health, and old code

The watcher's tick runs a dozen jobs: the observer, colors, recency, urgent flags, pins, reading the panes and the sidebar statuses, queued closes, the prompt mailbox, the sidebar order, names, and the invariant check. Each runs on its own (`berth/health.py`, 2026-10-31). Before, they shared one `try`, so a job that kept failing skipped every job after it, every tick, deliveries included, and only its first error was logged. Now a failing job is logged once when its error changes. The others run regardless, and `watch-health.json` in the data folder records each job's last success, error and failure count.

The watcher, the screen and every session's `berth mcp` run the code they started with. Each records a stamp of the code on disk when it starts (`processes/<pid>.json`); Berth processes started before that are found by their command line. `berth status` (and the MCP's `status`, under `health`) lists:
- failing jobs;
- whether the watcher is ticking;
- every process on old code, by the session it belongs to, with the fix (cycle that session, `q` then `berth` in the screen, or restart the watcher).

The screen's header says "⚠ new code: q, then berth" when its own code is old.


**Who reads this.** `slack-hooks status` shows a Berth line from `berth --json status` (`health.watcher_ticking`, `health.failing_jobs.delivery`) and `berth receipts --open` (`_delivery_line` in its `cli.py`, which Berth owns). If those fields change, change that function too.

**If the watcher is ever event-driven.** cmux's program-status events are the cleaner source. Claude's turn-end (Stop) hooks would also work, but such a hook must print nothing to the agent, never block, and exit 0. Dominik, on agents seeing it: "I don't want there to be any 'stop hook feedback'".

## Compaction

Berth compacts a Claude session itself (`berth/compact.py`), because agents placing their own `/compact` kept failing. A pasted `/compact <note>` is shown as a `[Pasted text]` chip and sent as a plain message: on 2026-10-30 STUDIO Orch's fork read its compaction note as its job and built the thing. Typing `/compact ` and pasting the note behind it is refused by cmux ("holds text someone is typing"), and one typed line is too short for a real note. All three were replicated on a throwaway pane the same day.

- **A live session:** Berth waits until it is ready, types `/compact ` (cmux's typing guard refuses if someone is typing there), pastes the note behind it with `--force`, checks the box reads `/compact …`, and presses Enter. Claude expands the chip into the command's arguments; a 4,140-byte, 40-line note arrived whole.
- **A closed session, or a fork before its pane opens:** `claude -p --settings '{"disableAllHooks": true}' -r <id> "/compact <note>"`, or `-r <source> --fork-session --session-id <fork>` in the same call. No prompt box at all, and the source of a fork is untouched. No hook runs in it, and `BERTH_COMPACTING=1` keeps Berth's own hooks silent besides (2026-11-06): the reorientation hook had started a whole turn inside the run, and a fork did its first job there until the 1200 s limit killed it. So a session compacted this way never gets the reorientation text; whoever asked for the compaction (its orchestrator, as a rule) gives it its next prompt.
- **The proof** is Claude's own record: one more `compact_boundary` in the transcript after the start. When Claude declines ("Not enough messages to compact.") it writes that reply instead, and Berth fails at once with it.
- **Then** Berth waits for the session to be ready and sends the next prompt, if one was given (`berth/send.py`).

The MCP's `send` with `compact_file` (an open or closed session, or the caller itself) and `create` with `from` and `compact_file` (fork and compact, with `cwd` to fork into another working directory in the same step) start a detached worker (`berth compact-run <job>`) and return at once. Progress is in the row's `attachments.compaction` (`queued`, `waiting`, `compacting`, `opening`, `sending`, `done`, `failed`), which `session` shows, and in its events (`compact_queued`, `compact_done`, `compact_failed`, `prompt_sent`). Jobs and their logs are in `~/.local/share/berth/compact/`. cmux-mcp's `input` refuses a prompt file that starts with `/`, pointing at these.

Tested live on 2026-10-30, on a throwaway haiku pane: in the pane (70,650 to 18,396 tokens, then the follow-up answered), fork and compact (the fork answered from exactly what its note kept), through the MCP's detached worker, and on a sleeping session (compacted closed, woken, answered).


**The frame (2026-11-04, Agent Behavior's text).** Every note Berth compacts with is wrapped in a frame: the summarizer writes the summary for the task the note gives and decides itself what to keep. A note gives TASK and, if the session becomes another agent, SEAT; optionally SOURCES and KEEP OUT (with why). A note with a keep or drop heading (`KEEP:`, `## Drop`, `**Let go**`, `What to keep:`) is refused (`BAD_ARGUMENT`, with the line), since agents' keep/drop lists scored 1.2 of 3 against Dominik's own task-only notes (2.8). The job file records `"frame": "v1"`. Dominik's own `/compact` never goes through Berth.

## Orchestrators

A folder can name its orchestrator (`folder.orchestrator`): the MCP's `folder` with action `orchestrator`, `path`, and `session`, or `berth orchestrate <folder> <session|none|inherit>`. `none` is an answer: that folder's sessions report to Dominik, so the walk stops there and the hook prints nothing (STUDIO/BERTH). `inherit` clears a folder. A session's orchestrator is the one named by the nearest folder up its tree, not counting itself (`berth/orchestrator.py`). A root orchestrator (the orchestrator of a top-level folder) has none: it gets its peers, every other top-level folder's orchestrator, so a new root joins on its own (2026-11-06; `--json` gives `{"root": true, "peers": [{folder, id, title}, …]}`), and its hook is one paragraph telling it to send each its compaction report. `berth orchestrator <id|me>` answers it. `berth orchestrator me --hook` is Claude's `SessionStart` hook after a compaction: it finds the session by the hook's `session_id` (an id Berth does not know answers nothing), or, given no id, by its workspace or directory, prints four lines naming the orchestrator (Agent Behavior's text), or nothing, and never fails.

## When cmux restarts

Everything that was open comes back. When cmux quits, the watcher parks every session as auto-parked, keeping its recipe and folder. When cmux relaunches, it closes the panes cmux restored (their agents are idle; cmux's own resume would use stale session ids), relinks and starts the Berth screen, and a few seconds later reopens every session that was open, one at a time, exactly as Enter does, without taking focus. The sidebar goes back to tree order. A process is not rerun by itself, and a session that fails to reopen stays in the auto-parked filter. `berth restore` reopens the last quit's sessions by hand. A restart is only a new cmux app process; a Mac going to sleep, waking, or cmux being slow to answer is not, and parks and closes nothing. Berth never closes a pane that is running an agent unless you pause or delete that session. A restart while the watcher itself was down, or a reboot, is caught the next time it starts. See the 2026-10-22 incident in docs/BERTH-STATE-MACHINE.md.

## Pause and play

Pause order:

1. The workspace you are running the command in is refused (`PROTECTED`). A pin does not refuse pause.
2. The screen is read (`cmux read-screen --scrollback --lines 120`) and the hook record is classified. Busy is a label. It does not refuse pause.
3. Claude is quiet when `agentLifecycle` is `idle` or the hook event is `Stop` or `SessionStart`, even when `lastBody` holds the last answer. `PreToolUse` while the lifecycle is `running` is busy. Grok is quiet when the hook says waiting, idle, or needs-input, or the PID is dead.
4. A process berth refuses with `KIND_UNSAFE` unless you pass `--confirm` or `--force`. Play of a process refuses unless you pass `--run-command`.
5. The recipe and the excerpt are committed with state `pausing`.
6. Then, and only then, `cmux workspace close`.
7. Success sets `parked` and clears the workspace id and the PID. Failure sets `live` again and returns `CMUX_REJECTED`.

Play order:

1. A live berth whose workspace is still open is focused. A second workspace is not created.
2. A live berth whose workspace is gone is treated as parked.
3. State moves from `parked` to `playing` in one transaction. A second play sees `playing` and returns `IN_PROGRESS`.
4. A `playing` row older than two minutes with no workspace is stale. Reconcile, and the next play, return it to `parked` before creating anything.
5. Kind `grok` requires the session id on disk under `~/.grok/sessions/`. A missing id returns `SESSION_MISSING` and leaves the berth parked. Berth does not start a fresh Grok and call it the same berth. `berth new` is the blank session, and it is a different id.
6. A second live berth with the same session id returns `SESSION_IN_USE`.
7. `cmux workspace create` uses the cwd, the title, and the recipe. Failure returns the berth to `parked`.

`berth reconcile` repairs `pausing` and `playing`, and a vanished live workspace becomes parked with its recipe kept. The watcher repairs a cut-off pause on its own (2026-11-05): a row still `pausing` 90 s on while its pane is open goes back to `live` (`close_did_not_happen`), and a queued cycle or close waits through `pausing` and `playing` instead of being dropped, so it runs once the row is live again. Pass `--no-adopt` to skip cataloging workspaces that have no berth. Reconcile does not close anything.

## Kinds

| Kind | How it is chosen | Play |
| --- | --- | --- |
| `grok` | The winning hook for the workspace is `~/.cmuxterm/grok-hook-sessions.json`. The session id is taken from that file. A dead Grok record does not win over a live Claude record on the same workspace. | `exec ~/.grok/bin/grok -r <session id>` plus the flags stored on the recipe (`--always-approve`, `--no-subagents`, `--reasoning-effort` when they are there). A shell row does not launch grok. |
| `other-agent` | The winning hook is another `*-hook-sessions.json` file, such as `claude-hook-sessions.json`. The live PID wins when two files name one workspace. A shell whose foreground process is Codex, Antigravity (`agy`), or Cursor is this kind too. | `exec <binary> -r <session id>` for Claude. Codex is `codex resume <id>`. Antigravity is `agy --conversation <id>`. Cursor is `agent --resume <id>`. Model, effort, name, and permission flags are not copied. |
| `process` | No hook, and the surface command is not a shell and not an agent. | Runs that command again. Needs `--run-command`. |
| `shell` | No hook, and no recorded command, or the command is a shell, or an agent command with no session id. | Opens a shell at the cwd. Does not pretend to resume a REPL or a dev server. |

The hook body's text is used only to decide busy versus quiet. It is not stored. The catalog stores the session id, not the transcript.

A hook whose process said `SessionEnd` and is gone is ignored. A headless `grok -p` or `claude -p` run from a pane's shell (an agent running a check) writes a hook under that pane's workspace; it does not take over the session the pane is really showing (2026-10-23). A resume recipe drops one-shot flags (`--fork-session`, `-s`/`--session-id`, `-p`), so a paused fork resumes instead of forking again.

`berth recipe <id> --session <id>` sets a Grok session id when the hooks never wrote one. Play still checks that the session exists.

### cmux's sidebar and notifications

(`docs/BERTH-CRUD.md` lists everything that changes a session's place and status, and what Berth holds.)

cmux's sidebar spoke with several voices for one session, and Claude and Grok used different words (2026-10-29). Claude's agent hook wrote Running, Idle, or Waiting; Grok's wrote Idle. The terminal's progress added "claude-code · Working" or "Done". The last notification added "Claude is waiting for your input" or "Waiting for your next prompt", the last message added a paragraph, and the path added a line. They disagreed with each other: a Claude whose turn ended with two shells still running showed both Running and Waiting. None of them counted background shells.

Now the sidebar is Berth's (`cmux-sidebar/berth.js`, a cmux custom sidebar, and `berth/cmux_sidebar.py`). Each row has a dot, the session's name, and one word, read from the same look at the pane that decides busy (above):

| Color | Word | Meaning |
| --- | --- | --- |
| red, first under Urgent | Urgent | the session said it needs you urgently (`update` with `urgent` and `why` in the Berth MCP; the skill says agents do it only when their instructions say so). Shown once its turn ends; it comes down only when you type a prompt into that session's box, or the session sets it false (another agent's message, or a prompt an agent typed there, never clears it; 2026-11-04) (`berth/attention.py`). The screen marks it `!` |
| brown | API error | Claude's turn ended on an API error: the API server out of reach (no network, DNS), overloaded, or timed out (2026-11-03; `berth/activity.py`, `_api_error`). Only Claude's last message counts, so an old error above newer output does not. It is idle: a prompt such as "continue" picks it up once the network is back. While Claude retries inside a running turn, it is Working |
| magenta, in its place | Follow up | something only you can do blocks the session: a sign-in, a password, a decision its written instructions reserve for you (2026-10-31; `update` with `follow_up` and a required `why`, or `berth follow-up <id|me> <why>`). The `why` is what you do, as an action; Berth refuses a question, an approval, or a confirmation (`berth/attention.py`, `check_follow_up`), since agents decide those themselves. One need, one flag: a follow-up that matches one already up is refused (`DUPLICATE`), and the error gives the agent its closing line, "Follow-up: see <that session>". Its whole cost in an agent is one `update` call and one closing line ("Follow-up: <action>" or "No follow-up."), set out in two sentences of the Berth MCP's server instructions and nowhere else (2026-10-31). It replaces Done or Ready, stays in the row's own place with no notification, and comes down only when you type a prompt into that session's box, or the session sets it false (another agent's message, or a prompt an agent typed there, never clears it; 2026-11-04). Urgent outranks it. The screen marks it `»`. ⌘⇧X in cmux dismisses the follow-up of the session you are on, outside any terminal (`berth/keys.py`): it comes down, and Berth refuses the same one from that session again (`DISMISSED`), so it stops coming back; a different need still can |
| amber | Question | Claude stopped and asks you a multiple-choice question (AskUserQuestion) |
| amber | Permission | a tool needs your permission |
| amber | Plan to approve | Claude wrote a plan and asks whether to go ahead |
| amber | Trust folder?, New MCP server | Claude's startup questions |
| amber | Needs you | cmux says Claude is blocked on something else it showed you |
| amber | Unclear | no prompt Berth recognizes: go and look |
| cyan, blinking | Answering /btw | a side question you asked with /btw is being answered |
| cyan, filled and bold | /btw answered | its answer is open; read it, Esc closes it. While a turn runs, the row says Working instead; once the turn ends, the /btw shows over Waiting and Ready |
| green, filled and bold | Done | its turn ended and you have not looked yet (cmux's unread) |
| green ring | Ready, Ready · draft | idle, waiting for your next message (with something typed in the box) |
| blue, blinking | Working, Running `<command>` | a turn is running, with or without subagents; in a shell, a command |
| purple (blinking while subagents run) | Waiting on 2 subagents, Waiting on 1 shell | main's turn ended but subagents it started (a `/subtask` fork, `f` on a `/btw` answer, a background agent) or shells still run; it comes back by itself. If main is in a turn as well, it is Working. Main's turn is read from its own transcript (forks write their own), since the strip's "esc to interrupt" belongs to whichever agent the prompt box talks to and cmux's program status says working for any of them |
| gray | Shell, Menu open, Starting, Usage limit | a shell at its prompt; a list you opened (/model, /resume); nothing drawn yet; stopped until the usage limit resets |
| yellow square | Berth | the Berth screen (a square, not a dot: it is not a session) |

A line on top counts them (2 need you, 1 /btw, 23 ready, 2 working, 3 waiting). What covers Claude's prompt box is told apart by the words on it, taken from Claude Code's own strings (`_DIALOGS` in `berth/activity.py`); a list with none of them is a menu you opened, unless cmux says Claude is blocked on it. A working dot blinks on and off once a second. In the folder-by-folder order (`o`) a heading names each folder, as in the screen; pinned sessions come first under Pinned. A click selects the workspace; right-click pins, marks read or unread, or closes it. A workspace Berth does not hold shows cmux's own agent status in the same colors.

The watcher writes each session's state every three seconds, only when it changes, in two places: a status line under the key `berth` (`cmux set-status`), which cmux's own sidebar shows if you switch back to it, and the workspace's progress label, `berth1|state|word|folder|order|time`, which is what the Berth sidebar reads, because a custom sidebar cannot read status lines and nothing else writes a workspace's progress. cmux's own sidebar hides progress (`sidebar.showProgress` off), so it never shows as a bar. The watcher rewrites one row's time every 30 seconds even when nothing changes; if the newest time is over 90 seconds old, the sidebar says the watcher is not writing statuses and dims them.

Notifications use the same words. cmux runs Berth's hook (`berth notify-hook`) on each one: a finished turn is titled with the session's name and says Done, Claude and Grok alike (cmux said "Claude Code" or "Grok" and "Completed in <directory>"); a permission says Needs you. The reminders a minute after a turn ("Claude is waiting for your input", "Waiting for your next prompt") are dropped, since the sidebar says Ready all along, and so is cmux moving a notified workspace to the top, which Berth's order undid 30 seconds later. Notifications that are not from Claude or Grok pass unchanged, and if the hook fails, cmux delivers the notification as it was.

The sources underneath are cmux's and stay as they were: Claude's and Grok's cmux hooks (through `cmux-claude-wrapper`), the terminal's program status (which Berth also records, `programs.json`), and cmux's notifications. Berth only reads and rewords them, through cmux's documented CLI, settings, custom-sidebar folder, and notification hooks, so a cmux update does not overwrite any of it. After one, `berth sidebar install` (safe to run again) puts back anything that changed; `berth sidebar uninstall` returns to cmux's own sidebar and settings (stop the watcher too, or its status lines stay).

## Commands

Every command accepts `--json` and then prints one JSON object. Failure prints the error object and exits non-zero.

```json
{ "error": { "code": "BUSY", "message": "...", "details": {} } }
```

Codes: `BUSY`, `PROTECTED`, `SESSION_MISSING`, `IN_PROGRESS`, `NOT_FOUND`, `NOT_LIVE`, `CMUX_DOWN`, `CMUX_REJECTED`, `FOLDER_NONEMPTY`, `FOLDER_LIVE`, `KIND_UNSAFE`, `SESSION_IN_USE`, `BAD_TRANSITION`, `COMPACT_FAILED`.

Row state changes only through `berth/machine.py`. Its table, and the invariant, are in `docs/BERTH-STATE-MACHINE.md`.

| Command | Effect |
| --- | --- |
| `berth` | Open the screen on the catalog. |
| `berth screen` | The same screen. `--dump` prints one frame and does not run intents. |
| `berth status` | Parks a live row whose workspace is gone, then prints the live count, the open workspace count, the parked count, watcher up or down, and child RSS when `cmux memory --all` answers. |
| `berth check` | Read-only. Compares the catalog with `cmux workspace list` and prints each invariant violation (docs/BERTH-STATE-MACHINE.md). Exits 1 on drift. The watcher runs the same check every tick and writes `invariant.json` next to the catalog. |
| `berth tree` | Nested folders and berths. |
| `berth adopt` | Live records. Closes nothing. `--workspace <uuid>` adopts one. |
| `berth watch` | Blocks. Adopts each workspace that appears. Parks a row when its workspace closes. |
| `berth install` | Installs the LaunchAgent. Prints the plist path. |
| `berth sidebar install` | Links and selects the Berth sidebar in cmux, quiets cmux's own rows, and adds the notification hook, after backing up `cmux.json`. Safe to run again, for example after a cmux update. `berth sidebar uninstall` undoes it. |
| `berth notify-hook` | cmux runs it on each notification (JSON in, JSON out). Never fails. |
| `berth play <id or query>` | State machine above. `--background` leaves focus where it is. |
| `berth pause <id or query>` | Recipe flushed before close. Busy and a pin do not block it. `--folder <path> --confirm` pauses the live rows in that folder. The caller workspace stays. |
| `berth focus <id>` | Select. `NOT_LIVE` when parked. |
| `berth new --folder <path> --title <text>` | New parked grok berth, then play. |
| `berth mkdir <path>` | Folders, including parents. |
| `berth move <id> <folder>` | Refile in another Berth folder. The working directory stays. |
| `berth memory` | Memory now, from the Performance Agent's definitions (`berth/memory.py`): the Mac as Activity Monitor counts it (Memory Used = app + wired + compressed; cached files apart; swap; pressure) and each session's footprint split into Claude (with its caffeinate), hooks (cmux's hook program), shell work (under `/bin/zsh`, `/bin/bash` or `/bin/sh`) and MCP (anything else), exactly as `pstw sessions` splits it, with resident (shared included, never summed) beside it. Detached shell runs are their own lines. The watcher stores one every 30 s in `memory.json`; `berth status` and the screen's top bar show it as `Mac 28.4/36 GB · swap 12.3 · pressure warn · sessions 33.6 GB`. Replaced `children`, cmux's summed RSS (2026-11-06). |
| `berth mcp --http [--port 4792]` | One shared MCP server for every session (`berth/mcp_http.py`): `/berth` and, when cmux-mcp is on `PYTHONPATH`, `/cmux-mcp`. Stateless; the session is named by the `X-Berth-Workspace` header (`${CMUX_WORKSPACE_ID}` in the registration). Refuses `Origin`, a missing header, or a foreign `Host`. Plan and test results: `docs/SHARED-MCP-SERVER-PLAN.md`. Not installed until Dominik's yes. |
| `berth whoami` | The Berth session this process runs in: `{id, title, folder, session, agent, state, workspace, via}`, found by Claude's session id (`CLAUDE_CODE_SESSION_ID`), else its open workspace. `NOT_FOUND` (exit 1) when it is in none. For a program run from an agent's shell that needs "me" (2026-11-06, Slack watch). |
| `berth send <id> --text T --sender S [--key K] [--meta JSON] [--wake] [--busy] [--wait MIN] [--on-settle CMD]` | Give a session a prompt from a program: typed into its prompt box once it is ready (with `--busy`, a Claude session's while it works too), in order, with a receipt (**Delivery**). |
| `berth receipt <id>` / `berth receipts [--sender S] [--session Q] [--open]` / `berth unsend <id> --sender S` | Read a prompt's receipt, list them, or take back a queued one (its sender only). |
| `berth compact <id> --note-file F [--then-file P] [--fork --title T] [--background]` | Compact a Claude session with that note, checked on disk, then send the next prompt (**Compaction**). Waits for it unless `--background`. |
| `berth cwd <id> [<directory>] [--cycle]` | Show the working directory, or set it: the session opens there at its next wake or cycle (`--cycle`: now). Not Grok. |
| `berth note <id> <text>` | |
| `berth pin <id>` / `berth unpin <id>` | Pin or unpin: first in cmux's sidebar, 📌 in the screen. |
| `berth excerpt <id>` | |
| `berth reconcile` | Section 10 of the design. |
| `berth log <id>` | |
| `berth rename <id> <title>` | Renames the berth, the live workspace, and a live Grok, Claude, Codex, Antigravity, or Cursor session (`/rename`). A rename inside the session, or on the workspace title, comes back to the other two. A shell is not typed into. `/rename` goes only into an idle pane with an empty prompt box (2026-10-31): mid-turn, or over someone's draft, it waits, and the watcher types it on the first idle tick. `berth check` reports any of the three names that differ (`name_mismatch`: Berth vs cmux; `session_name`: Berth vs the agent's own name, with Claude's ai-title not counting as a name). | 
| `berth delete <id> [--force]` | Moves the row to the trash. A live row's workspace is closed first. A Berth screen and the workspace the command runs in are refused unless `--force`; your own workspace is trashed first and closed last. Does not delete Grok session files. |
| `berth colors` | Read-only. The computed folder colors and the distinguishability report. |
| `berth recipe <id> --session <id>` | Attach a Grok session id. |
| `berth rmdir <path>` | Delete the folder. Berths move to the parent or Inbox. Nothing closes. |
| `berth offer` | Print this Mac's folders and parked rows. `--serve` listens on 127.0.0.1:4791. |
| `berth tailscale` | Publish that offer on the tailnet. The public internet is not opened. |
| `berth peers` | Other machines on the tailnet. |
| `berth preview --from <peer>` | Compare a remote offer with this catalog. Writes nothing. |
| `berth merge --from <peer> --folder <path>` | Add those folders as parked rows. Repeat `--folder`. `--all` adds every folder. A row or session id that is already here stays. Nothing closes. Session files are not copied. |
| `berth archive <path>` | Hide a folder with no live berth. |

A query is an exact id, else an exact title, else one unique substring of the title or the folder path. An ambiguous substring prints the matches and does not act.

`berth pause` with no id, inside a cmux workspace, uses `CMUX_WORKSPACE_ID`.

`--catalog <path>` points at a different sqlite file. `--db` is the same flag. Tests use that. `BERTH_CATALOG` is the same override. The screen opens `~/.local/share/berth/berth.sqlite` when neither is set.

## What this version does not do

Profiles are not in this version. A folder may store `profile_id`. Play ignores it.

There is no command that pauses every workspace on the machine.

## Tests

From this directory, on Python 3.12:

```bash
python3 -m pytest
```

The tests use a temporary sqlite file and a fake cmux. They do not open the live socket.

## Safety

- The recipe is committed before `workspace close`.
- Two plays cannot both create a workspace.
- A missing Grok session is not replaced with a blank one.
- Berth does not kill processes by walking the process table. Close goes through the socket. If the socket is down, pause fails with `CMUX_DOWN`.
- The catalog stores no copy of `~/.grok/config.toml` and no hook body.
