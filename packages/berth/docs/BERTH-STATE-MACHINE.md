# Berth state machine

This file is the spec for the state-machine refactor started 2026-10-15. Where it and DESIGN.md disagree on row state, this file wins.

## Why

Before this refactor about 30 call sites in seven modules wrote `berth.state` and `berth.cmux_workspace_id` directly, from four processes: the watcher's event thread, the watcher's refresh thread, the screen tick, and any CLI command. None of them shared a rule for what may follow what. A cmux quit or restart is exactly when they raced. The visible result was drift: open workspaces whose row was parked, parked rows whose workspace came back under the same UUID and was never relinked, and duplicate rows for one session.

## Invariant

While cmux is up and answered `workspace list`:

1. Every open cmux workspace is linked by exactly one row in `live`, `playing`, or `pausing`.
2. Every `live` row's workspace is open.
3. A `live` row's title equals the workspace title.
4. No session id is held by two rows that are not parked.
5. A parked row has no `cmux_workspace_id`.
6. A `playing` or `pausing` row is not older than two minutes.
7. A `live` agent row's title equals the agent's own session name (2026-10-31): Claude's last custom-title, the name ListAgents and SendMessage use (its generated ai-title is not a name), or the name a person gave a Grok, Codex, Cursor, or Antigravity session. Rule `session_name`. Berth repairs it by typing `/rename` only into an idle pane with an empty prompt box, retrying each tick, and the violation says when that is what it waits for.

`berth check` prints the violations and exits 1 when there are any. The watcher checks every tick, logs a change in the violation set as an `invariant` event, and writes `invariant.json` next to the catalog for the screen. A violation must persist for two ticks before it is reported, so a rename in flight is not drift.

While cmux is down there is no invariant to check. `berth check` says `cmux_down`.

## States

| State | Meaning | `cmux_workspace_id` |
| --- | --- | --- |
| `parked` | No workspace. Recipe stored. | null |
| `playing` | Berth asked cmux to create the workspace. | null until created |
| `live` | Workspace open. | set |
| `pausing` | Recipe flushed, close requested. | set |

A parked row whose `park_reason` is `sleep` or `cmux_quit` is shown as **sleeping** (`berth.model.is_sleeping`): closed to come back, woken by `W` / `wake`, and, for `cmux_quit`, woken by Berth itself after a restart. Any other parked row is **paused**. Sleeping is not a fourth state in the table: it is `parked` with a reason, so play, pause, the observer, and the invariant treat it as parked. `relabel` switches a parked row between `sleep` and `pause` without opening anything.

Every row also keeps `last_workspace_id` (the workspace it was last linked to; never cleared by parking) and `park_reason` (why it is parked: `pause`, `closed`, `cmux_quit`, `replaced`, `stale`, `merged`, `deleted-workspace`, ...). `park_reason` is null while the row is not parked.

## Transitions

Only `berth/machine.py` writes `state`, `cmux_workspace_id`, `last_workspace_id`, or `park_reason`, inserts a row, or deletes one. `Catalog.update_berth` refuses those columns from anywhere else, and a test scans the package for raw SQL that writes them. Every transition writes a `transition` event in the same transaction with `from`, `to`, `event`, and the detail.

| From | Event | To |
| --- | --- | --- |
| (new) | `adopt` | `live` |
| (new) | `import` | `parked` |
| `parked` | `relink` | `live` |
| `parked` | `play_claim` | `playing` |
| `playing` | `play_created` | `live` |
| `playing` | `play_failed`, `play_stale` | `parked` |
| `playing` | `relink` | `live` |
| `live` | `pause_flush` | `pausing` |
| `pausing` | `pause_closed` | `parked` |
| `pausing` | `pause_failed`, `relink` | `live` |
| `live`, `playing`, `pausing` | `vanished` | `parked` |
| `live` | `retarget` | `live` (new workspace id) |
| any | `delete` | (row removed, event kept) |

A transition from a state not in this table raises `BAD_TRANSITION` and changes nothing. The from-state is read inside the same `BEGIN IMMEDIATE` transaction, so two writers cannot both win.

Field changes that are not state (title, kind, session id, recipe, attachments) go through `machine.set_fields`, which also writes an event when the title changes.

## Quit and restart (stage 2, revised 2026-10-21)

When cmux quits or crashes, Berth (the LaunchAgent, still running) parks every live row with `park_reason = cmux_quit`, keeping each recipe, folder, and session id. When cmux comes back, everything that was open comes back: the operator does not reopen sessions one by one. (The first version brought back only the Berth screen; the operator rejected that on 2026-10-21.)

cmux restores workspaces on relaunch under the same UUIDs, with `terminal.autoResumeAgentSessions = false` so their agents stay idle. cmux's own resume uses the hook's session id, which goes stale after forks and `/resume`, so Berth does the resume instead. The watcher closes each restored pane whose UUID was open at the quit, except the Berth screen, which is relinked and started. About six seconds later it plays every session that was open at the quit, one at a time, through `play_berth`, exactly as Enter does, without taking focus (`berth/restore.py`). The sidebar sync then puts them back in tree order. A process row is not rerun without a person, and a play that fails leaves its row in the auto-parked filter with a `restore_failed` event. `berth restore` reopens the last quit's sessions by hand.

A quit is the cmux app process being gone; a restart is a different cmux app process (pid and start time from the process table, `Cmux.app_process`). Nothing else counts. At watcher start the last known identity comes from `observer.json`, so a reboot or a restart while the watcher was down is still caught; a watcher restart against the same cmux reopens nothing.

**Incident, 2026-10-22.** From 2026-10-21 restarts were also inferred from a new socket file and from cmux not answering for two ticks. cmux recreates its socket on every wake from sleep, and does not answer while asleep. So every time the laptop woke, Berth parked every session, closed every pane (killing the agents in them, and one in-flight turn), and reopened them: seven times on 10-22, at 00:50, 11:37, 11:52, 12:41, 13:13, 13:24 and 18:03 UTC, each within seconds of a wake in `pmset -g log`. One wake also stopped the watcher on a cmux timeout; launchd restarted it, and the fresh watcher read the new socket as a missed restart. Rules since then:

1. Only a different cmux process is a restart; a cmux that does not answer while its process is unchanged parks nothing.
2. Berth never closes a pane that runs anything but a shell, unless a person pauses or deletes that session. On a real restart a pane still running an agent is relinked, not closed.
3. One failing cmux call never stops the watcher.

Tests: `test_closing_the_lid_parks_nothing_and_closes_nothing`, `test_a_real_restart_never_closes_a_pane_that_runs_an_agent`, `test_a_first_start_that_cannot_reach_cmux_parks_nothing`, `test_one_failing_event_does_not_stop_the_watcher`.

**Fixes, 2026-10-23.** Three ways the catalog drifted from what the panes were really running:

1. A hook from a process that has ended is not the pane. A headless `grok -p` run from a Claude pane's shell wrote a `SessionEnd` hook under that pane's workspace, and the watcher re-labelled the Claude row as that Grok session. A hook whose event is `SessionEnd` and whose PID is gone is now ignored (`identity.ended`). Test: `test_a_headless_grok_run_in_a_pane_does_not_take_over_that_row`.
2. A list taken just before a pause is stale. `park_folder` closed a pane between the watcher's `workspace list` and its tick, and the observer relinked the row it had just paused as a "returned" workspace. A row parked by `pause` is relinked only when its workspace is seen open on two lists in a row. Test: `test_a_list_taken_just_before_a_pause_does_not_relink_the_paused_row`.
3. cmux's hook keeps a Grok launch's arguments only up to the first session id. Berth launched `grok -r <id> --always-approve ...`, the hook recorded plain `grok`, and the next pause stored a recipe without the flags. Berth now puts Grok's flags before `-r <id>`, and a pause keeps the stored flags when the hook captured none. Test: `test_a_pause_keeps_the_flags_a_lossy_hook_dropped`.

A close event is a hint, not an order. The watcher parks a row only after a fresh `workspace list` confirms the workspace is gone on two consecutive observations. If the socket disappears inside that window, the reason is `cmux_quit`, not `closed`.

## Colors

Folder colors are part of the same observer loop: `berth/palette.py` computes them from the folder tree and the watcher syncs folder rows and live workspace colors to the result every tick. See the package README.

## Stages

1. (done 2026-10-15) Machine, schema (`last_workspace_id`, `park_reason`), invariant check (`berth check`, watcher event), every existing write routed through the machine. No behavior change other than links surviving parking and events for every transition.
2. (done 2026-10-15) Single observer in the watcher: close events as hints, debounced parking, quit detection, relink by `last_workspace_id`, restart close of restored panes, screen filter for `cmux_quit`. The screen tick stops parking.
3. Identity and name sync as observer output through `set_fields`.
4. Remove dead paths, merge provable duplicate rows (shared session id or workspace id, backup first), update README and DESIGN.
