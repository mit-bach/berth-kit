# Invariants

Rules Berth keeps, and every agent working with it must keep. Each came from a real failure. The full spec is `packages/berth/docs/BERTH-STATE-MACHINE.md`; `BERTH-CRUD.md` lists every way a row changes.

1. **Only `berth/machine.py` writes a row's state and links** (`state`, `cmux_workspace_id`, `last_workspace_id`, `park_reason`), by a fixed transition table, with an event for every change. Nothing else, including you, edits the catalog by hand.
2. **The catalog equals cmux.** While cmux is up: every open workspace belongs to exactly one live row, every live row's workspace is open, titles match, no conversation is held by two open rows, nothing stays `playing` or `pausing` over two minutes. `berth check` reports any violation; a violation must last two looks before it counts.
3. **Never close a pane that runs an agent unless a person asked** to sleep, park, cycle, or delete that session. On a cmux restart a pane still running an agent is relinked, not closed.
4. **Only a different cmux process is a restart.** A cmux that is slow, a Mac asleep, a new socket file: none of them is. (On 2026-10-22 Berth took every wake from sleep for a restart and closed every pane seven times in one day.)
5. **Busy sessions are not closed without a yes.** Every close reads the pane again just before it acts; a busy one (a turn running, background shells or agents, an open question, a running command, a screen Berth cannot read) is closed only when confirmed, and a scope action skips it.
6. **Drafts survive.** Text typed but not sent is saved on close and pasted back, unsent, when the session is open, idle, and its prompt empty; never over other text.
7. **One conversation, one place.** Never resume a conversation that is running elsewhere. `create` refuses a session that exists; `wake` it.
8. **Claude's own records beat hooks.** `~/.claude/sessions/<pid>.json` says which conversation a live Claude is on; hook session ids go stale after `/resume`, a fork, or `/clear`. A `claude -p` that a session runs through its own tools is not that session's pane.
9. **No background agents.** A headless Claude job gets a `bg` row; opening it stops the background copy and resumes the conversation in a pane.
10. **Forks get their id from Berth** (`--session-id` for Claude, `-s` for Grok), chosen before launch. Never type `/fork` or `/rename` into a session; use `create {"from"}` and `update {"title"}`.
11. **One name per session**: Berth, the cmux sidebar, and the agent's own name. A name a person chose is never replaced by a generated one.
12. **A session cannot close its own pane.** A session asking to be cycled or slept is queued, and the watcher acts when its turn ends.
13. **Deleting never loses a session.** Every removal goes to the trash first, kept 30 days.
14. **The two cmux skills share one Berth block**, byte for byte, and its tables name exactly the MCPs' tools (`packages/cmux-mcp/tests/test_skills.py`).
