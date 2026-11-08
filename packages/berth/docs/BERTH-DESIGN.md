# Berth design

Berth is the catalog and the screen for cmux workspaces. This file is the design contract. A later build follows it. This directory does not implement it.

The words below have one meaning each.

| Word | Meaning |
| --- | --- |
| cmux | The macOS app at `/Applications/cmux.app`, version 0.64.25 (106) on the day of this design. |
| workspace | One sidebar row in cmux. The CLI calls it a workspace. The sidebar looks like a tab. |
| group | A cmux sidebar section. It has one level. Its header is an anchor workspace, and that anchor is a real terminal. |
| berth | One catalog record for one piece of work. The record outlives the cmux workspace. |
| folder | A directory in the Berth catalog. Folders nest. A folder is not a cmux group. |
| live | The berth has a cmux workspace running now. |
| parked | The cmux workspace is closed. The recipe is in the catalog. |
| recipe | The cwd, the argv, the agent session id, and the screen excerpt required to play the berth again. |
| play | Focus the live workspace, or create one from the recipe. |
| pause | Write the recipe, then close the workspace. |
| profile | The named set of MCP servers a folder allows when a Grok berth plays. |
| excerpt | The last screen lines saved at pause, so the place the work stopped is readable while the process is gone. |

## 1. What the desk has to do

You keep workspaces open because closing one drops the place you stopped. The sidebar is then a long list, and the machine holds a process tree for every row.

The desk has to do four things.

1. Keep a nested list of the work, including work whose terminal is closed.
2. Show, in that list, enough of the stopped screen that closing the terminal is safe.
3. Start that terminal again with the same Grok session, in the same directory.
4. Leave cmux's own sidebar as the short list of terminals that are actually running.

A deeper sidebar inside cmux, by itself, does not do this. The processes would still be running. Hibernation inside cmux, by itself, does not do this either. On this machine the Grok hooks do not report the idle state hibernation requires, and a hibernated tab is still a sidebar row.

## 2. The shape

Berth is one local program and one local database. The program talks to cmux only through `cmux` and the Unix socket. It does not link against cmux, and it does not patch the app.

```text
You
  │  keyboard in one cmux workspace, or `berth` from a shell
  ▼
Berth
  │  catalog in ~/.local/share/berth/ (mode 0700)
  │  recipes, excerpts, folders, profiles
  ▼
cmux socket
  │  workspace create, close, select, read-screen, rename
  ▼
A live workspace, or no workspace
```

One Berth workspace stays live. It runs the Berth screen, not Grok. That screen is the nested list. Every other workspace exists only while its berth is live.

cmux remains the terminal. Berth does not draw terminals, does not read keystrokes into agent sessions, and does not replace the sidebar for the work you are in.

### What Berth owns

- The folder tree.
- The stable id of each berth. cmux refs such as `workspace:32` change. Berth ids do not.
- The recipe and the excerpt.
- The profile named by the folder.
- The protect flag, the note, and the event log.
- The decision to close a workspace. cmux performs the close.

### What cmux owns

- The live terminal, the scrollback of a running surface, and the sidebar order of live rows.
- Grok's own session files under `~/.grok/sessions/`.
- Hooks, notifications, and the socket.
- Groups, if you still want a one-level grouping of the live rows. Berth does not need them.

## 3. Five ways to build it

You asked which of these it is. Each row is a real surface that exists on this Mac or in the cmux project today.

| Shape | What it would be | What it costs | Fit |
| --- | --- | --- | --- |
| Fork of [manaflow-ai/cmux](https://github.com/manaflow-ai/cmux) | Nested groups and a pause button inside the Swift app. | `Workspace.swift` on main is about 15,000 lines. The app is at 0.64.25 and moves quickly. A fork has to re-take groups, hibernation, hooks, and restore on every upstream merge. Nested groups still leave the process running unless pause is also built. | Wrong place for the catalog. |
| Interpreted custom sidebar | A `.js` file in `~/.config/cmux/sidebars/`, rendered as native controls. Documented in [custom-sidebars.md](https://github.com/manaflow-ai/cmux/blob/main/docs/custom-sidebars.md). | The runtime can read `data.workspaces()` and call `cmux(...)`. It has no filesystem, no network, and no timers. It cannot list parked berths, and it cannot store a recipe. | Good later as a tiny "live RSS" panel. Cannot be the desk. |
| ExtensionKit sidebar | A compiled Swift extension. cmux documents this beside the interpreted sidebars. | The extension runs in the macOS App Sandbox. Launching a helper has a separate, narrow path. You would ship and sign a second app, and track private extension APIs. | A later native panel, after the catalog has earned trust. |
| Obsidian | A plugin, or a folder of notes. | You already keep the written record in the vault. A plugin is a second control surface, and play would shell out to cmux anyway. Notes do not know which PID is still alive. | A generated mirror of the catalog. The mirror is for reading. The terminal screen is for play and pause. |
| External catalog (this design) | `berth` in one terminal, sqlite on disk, `cmux` for every mutation. | One program you own. It breaks when the socket methods it calls break, which is a small surface: list, create, close, select, read-screen, rename, group list. | This is the build. |

Two existing projects cover a nearby job and stop short of this one.

- [drolosoft/cmux-resurrect](https://github.com/drolosoft/cmux-resurrect) (`crex`) saves a named layout and restores that layout. It is a snapshot of a whole day. Berth is a list of individual sessions you park one at a time while the app stays open.
- [STRML/cmux-restore](https://github.com/STRML/cmux-restore) and [sanghun0724/cmux-claude-skills](https://github.com/sanghun0724/cmux-claude-skills) resume Claude Code after cmux restarts. cmux now does that itself with hooks. Neither one parks a Grok session during the day.
- [johnlindquist/cmux-extensions](https://github.com/johnlindquist/cmux-extensions) installs command-palette actions and scripts into `cmux.json`. That installer is a reasonable way to add "Pause this workspace" later. It is not a catalog.

cmux Vault searches old Codex, Claude Code, OpenCode, and Pi transcripts. The Vault page does not list Grok. Berth stores the Grok session id and an excerpt so search works for the sessions you park. Registering Grok with Vault is a separate, optional index. It is not the desk.

## 4. The screen

The screen is a full-terminal program in one cmux workspace. No browser. No HTML. The workspace command is the Berth binary, so the row costs a shell, not a Grok process and not an MCP stack.

```text
Berth                                          6 live   7.1 GB children
▸ UNI / 6.S978 / HW1
    reservoir          live    180 MB   selected in cmux
    delimiter          parked  2h       "split the sample on the mark"
    writers            parked  1d       excerpt: waiting at the prompt
▸ Personal Systems
    cedar-signatures   live    210 MB   Running
    dspy-docs          parked  5d
▸ Inbox
    (unnamed shells and anything adopt could not place)
```

The screen shows parked rows and live rows in the same tree. cmux's sidebar shows the live ones only.

### Keys

| Key | Action |
| --- | --- |
| j / k | Move |
| h / l | Collapse or open a folder |
| Enter | Play. If the berth is live, focus it. |
| p | Pause. The screen shows the excerpt it is about to save, then closes the workspace. |
| Space | Show the stored excerpt. Does not play. |
| / | Search title, note, folder, cwd, and excerpt. |
| n | New folder under the cursor's parent. |
| m | Move the cursor's berth into a folder. |
| ! | Toggle protect. A protected berth does not pause. |
| r | Reconcile. Compare the catalog with `cmux workspace list`. |

Play from the Berth screen focuses the new or existing workspace. `berth play --background` creates it and leaves focus on Berth.

### Command palette

cmux actions in `~/.config/cmux/cmux.json` can run a program. Two actions are enough as a bridge:

- Pause this workspace. Runs `berth pause --cmux-workspace "$CMUX_WORKSPACE_ID"`.
- Open Berth. Focuses the Berth workspace, or creates it when it is missing.

The palette is a shortcut. The tree still lives in the Berth screen. [johnlindquist/cmux-extensions](https://github.com/johnlindquist/cmux-extensions) shows the marker-comment way to add those actions without rewriting the rest of `cmux.json`.

### Obsidian mirror

`berth export` writes a generated Markdown tree into a vault folder you name. Each berth is one note: title, folder, state, cwd, session id, note, and excerpt. The file says it is generated. You can link a note from your own project notes. You do not edit the generated file to pause or play.

A later, optional URL `berth://play/<id>` can be handled by a small registered scheme. The export does not depend on that scheme.

### What the cmux sidebar is for after this

The sidebar is the live set. Berth does not try to draw parked rows there.

Do not create a cmux group per folder. A group header is an anchor workspace, and an anchor is a terminal. Six groups today means six header terminals, three of them still named Group 1, Group 2, and Group 3. Encoding a deep tree as groups would add a terminal per folder and would still be one level deep. The [workspace group command](https://cmux.com/docs/workspace-groups) has create, rename, collapse, pin, add, and remove. It has no parent field.

You may keep one cmux group called Now for the live rows if you like the visual break. Berth can place a newly played workspace into that group. The folder path is still the catalog's path, not the group name.

## 5. Pause and play

### Identity

A berth id is a UUID minted by Berth. The catalog also stores the last cmux workspace UUID, surface UUID, and PID. Those are hints for reconcile. They are not the identity. `workspace:N` refs are never stored as identity.

Grok's session id is the resume key for a Grok berth. cmux hooks already write it to `~/.cmuxterm/grok-hook-sessions.json` with the cwd, the surface id, the workspace id, the PID, and a launch command. Official session restore uses `grok -r <id>` ([session restore](https://cmux.com/docs/session-restore)).

### Kinds

| Kind | Pause | Play |
| --- | --- | --- |
| grok | Safe when the hook says the agent is waiting at a prompt, or idle, or the PID is already dead. | `grok -r <session id>` in the saved cwd, with the saved flags (`--always-approve`, `--reasoning-effort`, and the rest of the argv that was actually running). |
| other-agent | Same rule, using that agent's resume command when cmux hooks have a session id (Claude, Codex, Pi, and the others in the session-restore table). | That agent's resume argv. |
| shell | Pause stores cwd and excerpt. The shell's running program is gone after close. | Opens a shell at the cwd. Does not claim to restore a REPL, a vim buffer, or a dev server. |
| process | A recorded command such as a dev server. | Runs that command again. Pause asks for confirmation, because the live process dies. |

A workspace with several surfaces is still one berth in v1. The primary surface is the agent terminal. Other surfaces are attachments on the recipe: a browser URL, a second cwd. Play restores the primary terminal. It opens a browser surface afterward when the recipe has a URL. It does not rebuild an arbitrary split tree in v1. `cmux new-workspace --layout` exists for a later version. The recipe stores the layout JSON when cmux returns it, so the later version has the data.

### Pause sequence

Pause is two writes and one cmux call. The order is the safety property.

1. Refuse when the berth is protected. The Berth workspace is protected. The workspace that is running the pause command is protected.
2. Read the hook record for that workspace. Read the screen with `cmux read-screen --scrollback --lines 120`.
3. Classify the agent. Waiting at a prompt, idle, or a dead PID is quiet. A live PID whose last hook body is a tool call, a permission request, or empty-while-running is busy.
4. When the agent is busy, stop and show the excerpt. Continue only with an explicit confirm, or with `--force` from a script.
5. Write the recipe and the excerpt. Set state to `pausing`. Flush the database.
6. `cmux workspace close <id>`.
7. When close succeeds, set state to `parked` and clear the live PID and workspace id.
8. When close fails, set state back to `live` and return the cmux error.

A crash after step 5 and before step 7 leaves `pausing`. Reconcile looks at the workspace id. If the workspace is gone, the berth becomes `parked`. If the workspace is still there, the berth becomes `live`. The recipe from step 5 stays. Nothing is closed twice.

Pause of a berth that is already parked returns the parked record. It does not look for a workspace to close.

### Play sequence

1. When the berth is `live` and that workspace id is still in `cmux workspace list`, select it and return. Do not create a second one.
2. When the berth is `live` but the workspace is gone, treat it as parked and continue. Reconcile records the miss.
3. Set state to `playing`.
4. Build argv from the recipe. For Grok, the session id is required. If the session directory under `~/.grok/sessions/` has no matching id, stop with `SESSION_MISSING` and leave the berth parked. Do not start a fresh Grok and call it the same berth.
5. `cmux workspace create` with the cwd, the title, `--focus` as requested, and `--command` set to the resume argv. Pass `--group` only when a Now group is configured.
6. On success, store the new workspace id and surface id, set state to `live`.
7. On failure, set state to `parked` and return the error. Do not retry inside the same call.

Play of a live berth is safe to repeat. Two concurrent plays of the same parked berth must not create two workspaces. The database claims the berth by moving `parked` to `playing` in one transaction. The second caller finds `playing` and returns `409` with code `IN_PROGRESS`. It does not start a second create. A `playing` row older than two minutes with no workspace id is stale, and reconcile returns it to `parked` before a new play.

The idempotency key is the berth id plus the intent `play`. The client does not mint a new key per keypress. Hold-to-repeat on Enter hits the same key.

### What "the same session" includes

Play resumes the Grok conversation. It does not restore unsaved editor buffers, background shells inside that session, or MCP memory. Grok's session file is the memory of the conversation. The excerpt is the memory of the screen at the moment of pause, which covers the gap before the resumed TUI paints.

The resume argv uses the `grok` binary on `PATH` at play time. Live processes on this Mac are a mix of grok 1.0.41, 1.0.44, and 1.0.46. A pause followed by a play moves that berth onto the current binary. Record the binary version in the recipe so the screen can show "parked on 1.0.41, will resume on current."

### Adopt

`berth adopt` is the first run. It does not close anything.

For each workspace from `cmux workspace list`:

- Skip the Berth workspace once it exists.
- Match `~/.cmuxterm/grok-hook-sessions.json` on workspace UUID.
- Create a berth in state `live` with the title, cwd, session id, and launch argv.
- Place it in a folder by a rule you can re-run: an existing group name wins; otherwise the longest configured cwd prefix wins; otherwise Inbox.
- Mark the focused workspace protected for the duration of adopt, so a later bulk pause cannot take the workspace you are looking at unless you clear protect.

The current sidebar already shows why the folder rule matters. The group "6.S978 HW1" held 5 workspaces, and several more workspaces with cwd `.../6.S978/HW1` sat outside the group (`reservoir`, `chuck`, `writers`, `chunk-review`, `delimiter directed`, `delimiter judgment`). Adopt puts those in one folder. You move them after, in the tree, without closing them.

Shells with no hook record become kind `shell`, state `live`, folder Inbox.

Adopt writes an event per workspace. A second adopt matches on cmux workspace UUID and does not duplicate a live berth.

## 6. Folders

A folder has an id, a parent id, a name, a sort index, an optional note, an optional default cwd, and an optional profile id.

Depth is unlimited. The operations are create, rename, move, and archive. Archive hides the folder and its parked berths from the default tree. It does not close a live berth. You cannot archive a folder that still has a live berth. You move or pause those first.

Deleting a folder removes that folder. Berths in it move to the parent folder. A root folder's berths move to Inbox. Child folders move up one level. A child whose name is already there is merged into that folder. No berth row is deleted. No workspace is closed. Inbox cannot be deleted. Closing work by accident is the failure this system exists to prevent.

The tree order is the catalog order, not the cmux sidebar order. Dragging a live row in cmux does not rewrite the folder. Moving a berth in Berth does not reorder unrelated cmux rows.

A folder can store a default resume flag set for new Grok berths created inside it (reasoning effort, `--no-subagents`, approve mode). Play of an existing berth uses the recipe's argv, not the folder default. The folder default applies to `berth new`.

## 7. Memory

### Cause

The cmux process is not the 5 GB. On 2026-10-02 `cmux memory --all` reported an app footprint of 699 MB and 7.68 GB of child RSS. A process walk under the cmux pid found the same shape. The table is in [EVIDENCE.md](EVIDENCE.md).

Each live Grok workspace starts Grok and then starts the MCP servers configured for every session. In `~/.grok/config.toml` the enabled servers are browser-use, chrome-devtools, overleaf, and cmux-mcp. Overleaf is one coursework project, and it still starts inside ADP sessions, Fog of War sessions, and Chrome sessions. chrome-devtools and browser-use do the same.

Under the cmux process tree, the large groups were:

| Group | RSS | Processes |
| --- | ---: | ---: |
| grok | 2.7 GB | 40 |
| chrome-devtools-mcp | 2.0 GB | 130 |
| browser-use | 1.1 GB | 86 |
| overleaf-mcp | 1.0 GB | 64 |
| cmux-agent-mcp | 0.46 GB | 43 |
| zsh and login shells | 0.35 GB | 88 |

`cmux-agent-mcp` is the retired 82-tool server. It is not one of the four servers in the current Grok config. Old workspaces are still running it. A pause and a later play, on the current config, does not start it.

A second sample of CPU on that same tree was about 1.2 cores, spread across many Grok processes and one busier shell. This is a standing cost, not a single runaway process. RSS also moved between samples (about 7.3 GB and about 8.3 GB). Use `cmux memory --all` as the number to compare after a change.

Chrome-CDP.app was 3957 MB in its own tree, with zero shared processes. That browser stays when every cmux workspace closes. It is a separate decision.

`terminal.rendererRealization` reclaims the Metal surface of an off-screen terminal and keeps the process. It is on by default. It does not explain this RSS, and turning it off would not fix it.

### Why cmux hibernation is not doing this job

[Agent hibernation](https://cmux.com/docs/configuration) kills an idle background agent and resumes it when you open the tab. Routine hibernation requires all of these: the feature enabled, a restorable agent, lifecycle `idle`, an off-screen terminal, a live-terminal count above `maxLiveTerminals`, and quiet output through the idle window and the confirmation window. The documented example is `enabled: false`. No `agentHibernation` key was present in `~/.config/cmux` or under Application Support. The CLI only accepts `cmux agent-hibernation <on|off>`.

The hook file makes the idle requirement fail even if you turn the feature on. Of 105 Grok hook records:

| runtimeStatus | Records | What the last body usually said |
| --- | ---: | --- |
| needsInput | 53 | 51 said "Waiting for your next prompt" |
| running | 39 | 14 also said "Waiting for your next prompt"; 18 had a dead PID |
| idle | 10 | a finished turn |

Hibernation's own rule excludes `needsInput` and `running`, including during memory pressure. A prompt that is waiting for you is stored as `needsInput`. The sessions you would want to freeze are the ones the feature is required to leave alone. The sticky `running` state on dead PIDs matches the earlier sidebar-chip finding: Stop often fails to mark the session idle.

Hibernation also keeps the sidebar row. You asked to close the row and still get it back. Those are different features. A corrected idle signal plus hibernation is a useful backstop for a session you forgot. It is not the folder tree.

### Profiles

Pause is the large saving, because it removes the whole child tree. A berth you leave live should still avoid starting Overleaf and Chrome DevTools when that folder does not use them.

The constraint from Grok's config rules:

- `disabled_mcp_servers` in `~/.grok/config.toml` applies to every Grok process. Berth must not flip that list to play one berth.
- A project `.grok/config.toml` can replace a server of the same name. It does not suppress a server that exists only in the user file. A repo file cannot say "do not start overleaf."
- The config reference does not document an environment variable for `disabled_mcp_servers`.

The profile mechanism, once a one-session test confirms it:

A profile is a directory under `~/.local/share/berth/profiles/<name>/`. Its `config.toml` is a copy of the user config with `[mcp_servers.*]` reduced to the allowlist, and with `disabled_mcp_servers` set to the complement so a plugin server of a denied name does not start. `sessions`, `hooks`, and the auth files `grok inspect` names are symlinks to `~/.grok`, not copies. Play sets `GROK_HOME` to that directory for that workspace command only.

This is a build gate, not an assumption. `GROK_HOME` is documented as the config root. Session files have to resolve through that same root, or resume will not find `grok -r <id>`. If a test resume with `GROK_HOME` set fails to open the session, profiles wait. Pause still ships. The gate test is one parked berth, one profile that allows only `cmux-mcp`, and a resume that loads the same session id.

Do not copy tokens into the profile directory. Symlink the files. The catalog stores the profile name, never the file contents.

Suggested starting profiles, as names you can change:

| Profile | Allows | Use |
| --- | --- | --- |
| lean | cmux-mcp | Default for agent desks that do not drive a browser. |
| browser | cmux-mcp, chrome-devtools, browser-use | Chrome and page work. |
| writing | cmux-mcp, overleaf | The coursework project that needs Overleaf. |
| full | the four current user servers | Explicit opt-in. |

New folders use `lean` until you set another profile. Existing live sessions keep whatever they already started. The profile applies on the next play.

Plugin MCP processes that are not in the user `[mcp_servers]` table (the walk found `mcp-proxy-for-aws` under cmux) need a named decision inside the profile. If the test shows they still start from plugins, the profile's copied config drops that plugin entry too. Record the result of the gate test in the profile file as a comment, so the next change does not rediscover it.

### Caps are warnings

Berth can show "8 live, 6.4 GB children" in the header by reading `cmux memory`. A threshold you set (live count, or child RSS) turns that header into a prompt: these berths have been waiting at a prompt for more than a day. The prompt lists them. It does not close them.

Automatic pause stays off. The reason you keep 30 workspaces is that a silent close feels like losing the work. The excerpt and the recipe are what make an explicit pause feel reversible. An automatic close can exist later, as an opt-in, and only for berths that are quiet, unprotected, and off-screen. It is not part of the first build.

## 8. Records

The database is one sqlite file, `~/.local/share/berth/berth.sqlite`, directory mode `0700`, file mode `0600`. It is not a git repo. Excerpts live in the same database. A 120-line excerpt is small. Session transcripts stay in Grok's own files. Berth stores the session id, not a copy of the transcript.

### Folder

| Field | Type | Notes |
| --- | --- | --- |
| id | UUID | Stable. |
| parent_id | UUID or null | Null is a root. |
| name | string | Unique among siblings. |
| sort | integer | Order under the parent. |
| profile_id | string or null | Name of a profile. Children inherit when null. |
| default_cwd | path or null | Used by `berth new`. |
| note | string | Short. |
| archived | bool | Hides the subtree when the subtree has no live berth. |

### Berth

| Field | Type | Notes |
| --- | --- | --- |
| id | UUID | The identity. |
| folder_id | UUID | Required. |
| title | string | Sidebar title. You can rename either side. A rename in Berth calls `cmux rename-workspace` when live. |
| kind | grok, other-agent, shell, process | |
| state | parked, playing, live, pausing | |
| protect | bool | Pause refuses. |
| cwd | path | |
| note | string | One line, the reason the berth exists. |
| recipe_argv | string array | Exact argv. Not a shell string. |
| recipe_display | string | The same argv, joined for the screen. |
| agent_name | string or null | `grok`, `claude`, `codex`, … |
| agent_session_id | string or null | Required for kind grok. |
| agent_version | string or null | From the live process at pause. |
| cmux_workspace_id | UUID or null | Last known. Cleared when parked. |
| cmux_surface_id | UUID or null | |
| cmux_pid | integer or null | |
| attachments | object | Optional browser URL, extra cwds, raw layout JSON. |
| excerpt | string | Last screen text. |
| excerpt_at | time | |
| created_at, updated_at, parked_at, played_at | time | |

### Event

Append-only. One row per adopt, play, pause, failed close, reconcile correction, move, and rename. Fields: id, berth id, time, kind, detail JSON. This is the audit when a workspace disappears and you want to know whether Berth closed it.

### Errors

Every failed command prints one object and exits non-zero.

```json
{
  "error": {
    "code": "BUSY",
    "message": "Grok is mid-tool in cedar-signatures. Confirm to pause.",
    "details": { "berth_id": "…", "runtime_status": "running" }
  }
}
```

| Code | When |
| --- | --- |
| BUSY | Pause of a live agent that is not waiting. |
| PROTECTED | Pause of a protected berth. |
| SESSION_MISSING | Play of a Grok berth whose session id is not on disk. |
| IN_PROGRESS | A second play while the first create has not finished. |
| NOT_FOUND | Unknown berth id. |
| CMUX_DOWN | The socket is missing. |
| CMUX_REJECTED | cmux returned an error. `details` holds that error. |
| FOLDER_NONEMPTY | Delete of Inbox. |
| FOLDER_LIVE | Archive of a folder that still has a live berth. |
| KIND_UNSAFE | Play of a process berth without `--run-command`, or pause of a process berth without confirm. |

`--json` on every command prints the record or the error object. Human output is the default in the terminal screen. Scripts pass `--json`.

### Commands

| Command | Effect |
| --- | --- |
| `berth status` | Live count, parked count, child RSS from `cmux memory` when cmux is up. |
| `berth tree` | The folder tree. |
| `berth adopt` | Create live records for current workspaces. Closes nothing. |
| `berth play <id or query>` | Section 5. |
| `berth pause <id or query>` | Section 5. |
| `berth focus <id>` | Select the workspace. Errors with NOT_FOUND-equivalent `NOT_LIVE` when parked. |
| `berth new --folder <path> --title <text>` | Create a parked grok berth and play it, using the folder profile and folder flags. |
| `berth mkdir <path>` | Create folders, including parents. |
| `berth move <id> <folder>` | Change folder. |
| `berth note <id> <text>` | Set the one-line note. |
| `berth protect <id>` / `berth unprotect <id>` | |
| `berth excerpt <id>` | Print the stored excerpt. |
| `berth reconcile` | Repair `pausing` and `playing`, drop stale workspace ids, adopt workspaces that appeared outside Berth into Inbox as live. |
| `berth export <dir>` | Write the Obsidian mirror. |
| `berth log <id>` | Events for one berth. |

Query matching is exact id, then exact title, then a unique substring of title or folder path. An ambiguous substring returns the matches and does not act.

`berth pause` without an id, inside a cmux workspace, uses `CMUX_WORKSPACE_ID` for that workspace's berth.

## 9. Rules that keep work from disappearing

These are product rules. They are not optional polish.

1. Berth never closes a workspace it does not have a record for, except `reconcile` reporting it. Adopt is how a workspace gets a record.
2. The recipe hits disk before `workspace close`.
3. Play of a Grok berth refuses to start a blank session when the session id is missing. You can `berth new` for a blank session. That is a different command and a different id.
4. The Berth workspace is protected and cannot be paused by a bulk action.
5. Busy agents require a person, or `--force`.
6. Process berths require a person on pause and on play.
7. There is no recursive delete.
8. The catalog contains no tokens, no hook payloads beyond the fields in the record, and no copy of `~/.grok/config.toml`.
9. A command that would close more than one workspace does not exist in v1. You pause one berth, or you pause a folder, and a folder pause lists the berths and asks once for the whole list. The list is on screen before any close.
10. If cmux is down, pause fails with `CMUX_DOWN`. Berth does not kill PIDs by walking the process table. The close path is the socket, so cmux still runs its own teardown.

Folder pause is the one bulk path. It pauses only quiet, unprotected berths in that folder. Busy and protected rows are listed and left live. You pass `--force` to include busy rows. Protected rows stay. There is still no "pause everything on the machine" command.

## 10. Reconcile

Run reconcile at the start of the Berth screen, and on `berth reconcile`.

| Catalog state | Workspace present | Result |
| --- | --- | --- |
| live | yes | Keep. Refresh PID from the hook file when it has one. |
| live | no | Set parked. Keep the recipe. Event `workspace_vanished`. This is also what happens when you close a row yourself in cmux. The berth survives. |
| parked | a workspace exists with this id | Set live. You reopened it outside Berth. |
| pausing | no | Set parked. |
| pausing | yes | Set live. Close did not happen. |
| playing, younger than 2 minutes | no | Leave it. The create may be in flight. |
| playing, older than 2 minutes | no | Set parked. Event `play_stale`. |
| playing | yes | Set live. |

Workspaces cmux has that no berth points at become Inbox records in state live, the same way adopt works. Reconcile does not close them.

Hook records with dead PIDs and `runtimeStatus=running` are not written back by Berth. Fixing the Grok hook is a separate repair. Berth treats a dead PID as quiet for the purpose of pause classification, which is the conservative reading: the process is already gone, so closing the workspace does not interrupt a tool.

## 11. Failure you should expect

| Situation | What you see |
| --- | --- |
| cmux relaunches and restores every workspace, including ones you parked | Session restore brings back the layout cmux saved, which can include a workspace you closed earlier if that close did not make it into the last snapshot. Reconcile reattaches those rows to the parked berth ids when the workspace UUID matches, or adopts them into Inbox. You pause them again. Berth does not fight restore by closing on startup. |
| Grok resume paints a fresh TUI and the old scrollback is gone | The excerpt is in the berth. Space shows it. cmux's own scrollback restore is best-effort and does not apply to a workspace you closed on purpose. |
| Two berths, one Grok session id | Play of the second is refused with a new code `SESSION_IN_USE` while the first is live. Resume twice would be two TUIs on one session file. |
| You rename the workspace in cmux | Reconcile copies the cmux title onto the berth when the berth was not renamed later than the workspace. If both changed, keep the berth title and show a one-line conflict in the row. |
| Socket mode is "cmux processes only" | Berth runs inside a cmux workspace, so it is a cmux process and can connect. A Berth launched from Terminal.app cannot, unless socket mode is `allowAll`. The supported place to run the screen is inside cmux. Document that in the screen's first-run line. |
| Hooks were never installed for that agent | Kind falls back to shell. Play opens a shell at the cwd. The screen says "no session id." You can paste a session id onto the berth with `berth recipe <id> --session <id>` once, and the kind becomes grok. |

## 12. Build order

The order is the design of the work, so a later session can build it without re-deciding.

1. **Catalog and adopt.** sqlite, folders, `adopt`, `tree`, `reconcile`. No closes. You can look at the nested list of the workspaces that are open and move them into folders. Success: every current workspace has one berth, Inbox holds only what you left there, and a second adopt does not duplicate rows.
2. **Pause and play for one Grok berth.** Recipe, excerpt, the state machine, protect on the Berth workspace. Success: pause a quiet test workspace, see it leave the sidebar, read the excerpt, play it, and land in the same Grok session id. Then pause the test again.
3. **The screen.** Keys from section 4, search, folder move. Success: you can run a day from that workspace without the `berth` shell for ordinary pause and play.
4. **Folder pause and the memory header.** The header reads `cmux memory`. Folder pause skips busy and protected rows. Success: a folder of quiet berths parks, the child RSS reported by cmux drops, and busy rows in that folder stay.
5. **Profile gate.** One test resume under `GROK_HOME`. If it resumes the session and `cmux memory` shows that workspace without chrome-devtools, browser-use, and overleaf, profiles become real and `lean` is the default for new folders. If resume fails, stop and leave profiles documented as blocked. Do not half-enable them.
6. **Palette actions and the Obsidian export.** After the screen is the daily driver.

Out of the first build: automatic pause, split-layout restore, a custom sidebar, an ExtensionKit app, an Obsidian plugin, a URL scheme, Vault registration, and any change to cmux itself.

## 13. What you can do before a build

These are operator actions, not part of Berth. They are listed so the memory section has a path that does not wait on the program. None of them were applied while writing this design.

- Turn on routine hibernation only after a hook change maps "Waiting for your next prompt" to `idle`. Until that mapping exists, the switch does not see the sessions you would want it to freeze. The hook repair is the reconciler already described in the sidebar-alert notes: dead PID and a waiting body become idle, through `cmux` status or the lifecycle command, without hand-editing the hook JSON.
- Leave Overleaf, chrome-devtools, and browser-use enabled globally only if every new Grok session should pay for them. `disabled_mcp_servers` is global. Changing it affects the session you are in, including this one. That is a deliberate edit, done when you are ready, not from this design pass.
- New workspaces started after the cmux-mcp switch do not launch `cmux-agent-mcp`. Old ones do, until they exit. Pause-and-play is the clean way to drop them. Closing them by hand, before Berth exists, drops the sidebar row and depends on cmux's own resume to get the Grok session back.

## 14. Decisions recorded here

| Topic | Decision |
| --- | --- |
| Product name | Berth |
| Process model | One external program. cmux stays the terminal. |
| Where nested folders live | The catalog. |
| Where live terminals show | The cmux sidebar, short on purpose. |
| First interface | A terminal screen inside one protected cmux workspace. |
| Obsidian | Generated notes. No plugin in the first build. |
| HTML | None. |
| Fork | None. |
| Automatic pause | Off. |
| Default profile for new folders | lean, after the gate test passes. |
| Bulk close | Folder pause only, with the list shown first. Protected rows stay. |
| Identity | Berth UUID. cmux UUIDs are hints. |
