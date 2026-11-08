# Stage 4: make it yours

**Goal.** The setup fits how your operator works, every agent they run knows how to use it, and they can keep it working without you.

**Runs in:** chat 4 and later, inside cmux. First read `PROGRESS.md` and `DECISIONS.md`. Take these in the order the operator cares about; each is worth doing well rather than all at once.

## 1. Folder layout

Look at what they actually run now (`mcp__berth__folders`, `sessions {"folder": "/", "down": "all"}`). Folders that hold one session for a week, or twenty unrelated ones, want reshaping. Rename, nest, and move with `folder` (`create`, `rename`, `move`, `delete`); refile sessions with `update {"folder"}`. A Berth folder is not a directory: moving a session to another Berth folder never changes where it runs; `update {"cwd"}` does that (the `berth` skill's "Move a session to another working directory", and `packages/berth/skill/references/moving-sessions.md` for re-homing an agent with its brief and memory).

## 2. Teach every agent the house rules

Their agents read `~/.claude/CLAUDE.md` (global) and each project's `CLAUDE.md`. Draft, with the operator, a short section for the global file, for example:

- "Sessions on this Mac are managed by Berth. To find, start, fork, restart, file, or close a session, use the `berth` MCP and the `berth` skill; to see or type into another session's pane, `cmux-mcp` and the `cmux-claude` skill. Never type `/fork` or `/rename`; never run one conversation in two places."
- Who may raise `urgent`, and for what (from stage 3).
- Where job files and stop files go for agents they start (a folder in each repo, or `~/.agent-jobs/<job>/`).
- Any orchestrator rules (below).

Show the diff; write it on their yes. New sessions pick it up; cycle the idle ones (`control cycle` with a scope) to give it to running ones.

## 3. Orchestrators

Dominik runs some sessions whose job is to run other sessions: an orchestrator for a folder that starts workers on jobs, watches their stop files, reviews their output, forks them for variants, and raises `urgent` only when he is needed. `docs/WAYS-OF-WORKING.md` describes the pattern. If the operator has a recurring multi-agent job, set one up with them: a session in its own folder, a brief in its working directory's `CLAUDE.md`, and the jobs it may start.

## 4. The update routine, for real

When they are ready (no important turn running):

1. ⌘⇧B, `Z`: every session except the screen goes to sleep, each with its screen and draft saved.
2. Quit cmux, update cmux or macOS, reopen cmux.
3. ⌘⇧B, `W`: every sleeping session wakes in its own workspace, in its folder, on its conversation.

Then the crash case, if they want to see it: with sessions live, quit cmux (⌘Q) and reopen it. Berth parks what was open when cmux goes away, closes the idle panes cmux restores by itself, and wakes every session that was open, without taking focus. A Mac going to sleep or waking is not a restart: nothing closes.

After a cmux update, run `berth sidebar install` again (it is safe to repeat) in case the update reset cmux's settings, then `python3 <kit>/tools/doctor.py`.

## 5. Keeping it working

Make sure they know:

- `berth check` (or `mcp__berth__status {"check": true}`) says whether Berth matches cmux; `docs/TROUBLESHOOTING.md` covers the rest.
- After changing Berth's code, a skill, or an MCP server: restart the watcher (`launchctl kickstart -k gui/$(id -u)/com.bach.berth.watch`), the screen (`q`, then ⌘⇧B), and the agent sessions (cycle them).
- The kit is under git since stage 3 (no remote): `git diff` shows every change from the kit as received; commit what they keep.
- `docs/UNINSTALL.md` undoes everything.

## 6. Optional: Grok

If they use Grok Build: `<kit>/tools/install.sh grok --dry-run`, then without `--dry-run`. It links the `berth` and `cmux-grok` skills into `~/.grok/skills`, writes Grok's cmux hook if they have none, and sets the `berth` and `cmux-mcp` tables in `~/.grok/config.toml` (backed up first). `python3 <kit>/tools/doctor.py --stage 4` checks it. Grok sessions cannot change working directory (`grok -r` resumes only where a session started); the skills say so.

## Ends with

`PROGRESS.md` says `Stage 4: done` with what was set up, and a short "how I run my desk" note for the operator: the keys they use, the update routine, and where to look when something is off. Build it on `TIPS.md`, marking the tips they use and anything you changed. Offer to put that note in their global `CLAUDE.md` too.
