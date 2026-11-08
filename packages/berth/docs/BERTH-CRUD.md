# Berth: what can change, who changes it, and what that is

Written 2026-10-29. What Berth holds, how each thing is created, read, updated, and deleted, and which of it is a real state machine. `BERTH-STATE-MACHINE.md` is the spec for a row's lifecycle and wins over this file there.

## Three kinds of state

| Kind | What | Stored? | Who writes it | How |
| --- | --- | --- | --- | --- |
| Lifecycle | `parked`, `playing`, `live`, `pausing` (and sleeping: parked with a reason) | yes, `berth.state` and the workspace link | only `berth/machine.py` | a transition table, one `BEGIN IMMEDIATE` per move, one event per move, guarded columns, an invariant checked every watcher tick |
| Flags | pinned, urgent, a queued close | yes, `berth.pinned`, `berth.attention*`, the `deferred` table | `berth/pins.py`, `berth/attention.py`, `berth/deferred.py` | each a small machine of its own (below), one writer, an event per move |
| Activity | working, ready, needs you, waiting on agents or shells, /btw, ... | no: the `runtime` table is a cache of the last look | the watcher, every 3 seconds | read from each pane (`berth/activity.py`) and cmux's program status; mapped to one state (`status_of` in `berth/cmux_sidebar.py`) |

Activity is deliberately not a stored state machine. It is the agent's state, not Berth's: Berth observes it, and the pane is the truth. Storing it with transitions would add a second copy that can drift from the pane, which is the class of bug the lifecycle refactor removed. Each look replaces the last.

## Is Berth a state machine?

The lifecycle is, for real, since 2026-10-15: one writer, a table (`machine.TABLE`), `BAD_TRANSITION` for anything else, the guarded columns refused everywhere else (`Catalog.update_berth`), a test that scans the package for raw SQL writing them, an event in the same transaction as every move, and an invariant (`berth check`, every watcher tick) that compares the catalog with cmux. No refactor is needed there.

What is not finished, from the spec's own stage list: stage 3 (identity and name sync as observer output through `machine.set_fields`) and stage 4 (removing dead paths, merging duplicate rows). Those are cleanup, not a refactor.

The flags were ad hoc writes with events. They are now each one module with named moves (below); that is the light change, and it is done. Activity stays an observation (above).

## Sessions

| | MCP | CLI | Screen | Watcher |
| --- | --- | --- | --- | --- |
| Create | `create` (new Claude, Grok, shell, or a fork; `cwd` for a Claude fork in another working directory) | `berth new`, `berth fork [--cwd]` | `F` (fork) | adopts a workspace made outside Berth (`adopt`) |
| Read | `sessions`, `session`, `status` | `berth sessions`, `berth tree`, `berth status`, `berth check` | the tree | |
| Update: lifecycle | `control` wake, sleep, park, cycle, focus | `berth play`, `pause`, `sleep`, `wake`, `cycle`, `focus` | Enter, `p`, `S`, `W`, `Z`, `c` | `relink`, `vanished`, `retarget`, restore after a cmux quit; queued closes (`deferred`) |
| Update: fields | `update` title, folder (Berth folder), cwd (working directory, + `cycle`), note, pin, urgent | `berth rename`, `move`, `cwd`, `note`, `pin`, `unpin` | `t`, `m`, `P` | title and session id from Claude and Grok (`identity`), `last_used_at` (`recency`), colors (`palette`), pins from cmux (`pins`), urgent answered (`attention`) |
| Delete | `delete` (to the trash; `force` overrides the protection of a Berth screen and your own session), `trash` forget | `berth delete [--force]`, `berth trash forget` | `d` (`!` overrides), `D` in the trash | merges duplicate rows |
| Undelete | `trash` restore | `berth trash restore` | Enter in the trash | |

Folders: `folder` create, rename, move, delete, archive, unarchive; `folders` reads. Queued closes: `control` with `when idle` creates, `session` shows `queued`, `control` cancel deletes (added 2026-10-29; before, a queued close could only be replaced).

## Flags

**Pinned** (`berth/pins.py`): `pin`/`unpin` events; the watcher keeps cmux's workspace pin in step both ways.

**Urgent** (`berth/attention.py`): a session says it needs the operator urgently (`update` with `urgent` and `why`; the operator decides in each agent's instructions when). The sidebar shows it red, Urgent, first, once its turn ends, and cmux posts a notification.

| From | Move | To | Event |
| --- | --- | --- | --- |
| none | raise | urgent | `attention_raised` |
| urgent | raise | urgent (new why) | `attention_raised` |
| urgent | answer: a prompt reaches it after the flag went up | none | `attention_answered` |
| urgent | lower: `update` urgent false | none | `attention_lowered` |
| any | delete | (row gone) | |

Sleep, park, and cycle keep it.

**Queued close** (`berth/deferred.py`): queued, then done, failed, gave up, dropped, or cancelled, each an event.

**Compaction** (`berth/compact.py`, `attachments.compaction`): queued, waiting, compacting, opening, sending, then done or failed, with events `compact_queued`, `compact_done`, `compact_failed`, `prompt_sent`. Done means Claude's transcript has a new `compact_boundary`.

**Working directory set on purpose** (`berth/workdir.py`, `attachments.cwd_set`): set (`cwd_set`), opened there by a play (`opened`), then confirmed once Claude records a turn there (`cwd_confirmed`), when the transcript speaks for it again. A shell or process is done at its first open.

## What moves a session in the sidebar order

The sidebar lists live sessions in Berth's order (`berth/sidebar.py`): by last used (or by name), pinned first, folder by folder or following the tree. The watcher re-sorts at most every 30 seconds, and at once on a new sort, order, or pin. Urgent sessions are shown first by the Berth sidebar itself.

| | Event | Effect |
| --- | --- | --- |
| Create | Berth opens a session (play, wake, new, fork, a cycle's reopen) | `played_at` now: first in its folder |
| | a workspace made outside Berth | adopted; its time from the signals below |
| | cmux restarts and sleeping sessions wake | they keep their times |
| Read | clicking a workspace in cmux, scrolling, the Berth screen, notifications | nothing: looking is not using |
| Update: last used (only forward, every 2 s) | Berth play or focus | up |
| | cmux's last submitted message in the pane (typed, or pasted by another agent) | up |
| | the agent's last hook event | up |
| | Claude: newest message in its transcript (yours, its replies, tool results, task notices), a slash command (`/subtask`), a queued message, its forks' transcripts, and Claude's prompt history | up |
| | Grok: `last_active_at` | up |
| Update: other | the screen's `s` (last used or name) and `o` (tree or folder by folder) | re-sort at once |
| | pin or unpin | pinned first, at once |
| | move to another folder; rename (by name only) | its place changes |
| | cmux's own moves (a drag, a new workspace's spot) | undone at the next look; cmux moving a notified workspace up is off |
| Delete | pause, park, sleep, a cycle's close, the workspace closed in cmux, delete, cmux quit | the row leaves the sidebar |

2026-10-29: a prompt sent with `/subtask` reached none of the first four signals, so the session did not move; the transcript's slash commands, forks, and Claude's prompt history were added.

## What changes a session's status

The watcher looks every 3 seconds (`berth/activity.py`): the pane's text (the prompt box, its status strip, the agent list under it, whatever covers the box), cmux's program status for the pane (`programs.json`, from `program_status.changed` events), the shell's foreground process, and the urgent flag. cmux's unread mark turns Ready into Done. The table of states and their colors is in the README (**cmux's sidebar and notifications**) and at the top of `berth/cmux_sidebar.py`. Precedence, first wins: the Berth screen; Urgent (unless a turn is running); Question, Permission, Plan, Needs you; Usage limit; Working; /btw answered or answering; Waiting on subagents or shells (main idle, a fork or shell running); Running a command; Shell; Menu open; Unclear; Starting; Ready.
