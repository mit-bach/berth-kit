# Changelog

Newest first. `docs/HISTORY.md` tells how Berth itself was built.

## v3.1.1 (2026-11-07)

- The README lists every repository the kit comes from or uses ("Repositories this uses"), and whether you install from it or only read it.

## v3.1.0 (2026-11-07)

- **Links open in your normal browser**, not cmux's built-in one: links clicked in a terminal, `open <url>`, and the sidebar's pull request and port links. A day-one decision (`docs/DECISIONS.md`, 8b); `tools/install.sh cmux --cmux-browser` keeps cmux's browser.
- Includes v3.0.1's fix (below).

## v3.0.1 (2026-11-07)

- Berth: two processes opening a brand-new catalog at the same moment (the watcher, the screen and the MCP server starting together on a fresh install) no longer make one call answer "catalog unreadable". Every schema step is now safe to run twice at once.

## v3.0.0 (2026-11-05)

- **One repository instead of three zips.** Clone it; the README says which prompt you send. The tracks live in `onboarding/` (terminal, cmux, Cursor). Updating is `git pull` plus the Update prompt in the README.
- **Berth's session standard**, now a day-one decision (`docs/DECISIONS.md`, 3a): Berth keeps every Claude session in auto mode at effort xhigh, and corrects one that drifts when it's idle. `berth standard --correct off` turns correcting off.
- **Berth since v2.1.0** (details in `packages/berth/README.md`):
  - one primitive for prompts from programs and agents (delivery, a mailbox per session);
  - compaction notes wrapped in a frame, so the summarizer decides what to keep;
  - the Follow up flag (magenta in the sidebar), for when a session needs its operator to act;
  - every watcher job runs on its own, so one failing job can't stop the rest;
  - a folder can name its orchestrator;
  - optional: one Claude config folder per account, with a view grouped by account (`a`).

## v2.1.0 (2026-10-31)

- `send`: a prompt goes into a session only after a look at what it's doing. When it's busy, the agent waits, queues the prompt with a note to finish first, or interrupts.
- Compaction done by Berth, and proved from Claude's own transcript.
- One Berth screen. A protected delete can be overridden (`d` then `!`, or `force` and `confirm`).
- New sessions take the permission mode from Claude's settings; Berth no longer passes `--dangerously-skip-permissions`.

## v2.0.0 (2026-10-29)

- Three zips on the current Berth: its own MCP server, sleep and wake, drafts that survive, busy read from each pane, pins, the trash, urgent, Berth's sidebar.
- Onboarding written for the recipient's agent: survey, cmux, Berth and the move, agents running agents, making it yours.

## v1.0.0 (2026-10-27)

- The first kits, for cmux and for Cursor.
