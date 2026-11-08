> **In this kit:** this reference was written on Dominik's Mac by one of his orchestrator sessions. His `~/STUDIO/ROOTS.md`, his orchestrators, and the roster script it names are his and are not in the kit; the method (place the unit, write its brief, check its directory's settings, move or fork, verify) carries over.

# Moving a session to a new working directory

This is how you give an existing agent a proper home after the fact. Use it when a session runs in a directory that makes no sense:
- a skill's directory;
- `~`;
- another agent's directory;
- a directory it drifted into.

The result is a unit directory of its own, with a brief written for it, and the session working there with as much of its context as you choose to keep. Written 2026-10-29 by HOMESTEAD Orch, from Berth Developer's build and tests of that day.

**Words.**
- **A Berth folder** is where a session is filed in Berth.
- **A working directory** (`cwd`) is the directory on disk it runs in.

They're independent. Moving one never moves the other. SKILL.md, "A folder is not a directory", has the detail.

**Calls.** SKILL.md has them: "Move a session to another working directory" (`update {cwd}`) and "Fork" (`create {from, cwd}`). For a session whose berth server started before 2026-10-29 15:11, the CLI has the same calls (`berth cwd`, `berth fork --cwd`), and the CLI always runs current code.

---

## Pick the route

| The agent | What you want | Route |
|---|---|---|
| Claude | The same session, its whole conversation, its name | **A. Move it**: `update {cwd}` |
| Claude | A new agent that starts with the whole conversation, while the old session stays as it is | **B. Fork it into the directory**: `create {from, cwd}`, then compact the fork with its new role |
| Grok | Any | **C. A fresh session and a handoff.** A resumed Grok session keeps working in the directory it started in, so Berth refuses `cwd` for Grok. |
| Claude | A clean start, because the history is long, off-topic or mostly superseded | **C** |

**A or B.**
- **Use A when the session *is* the agent,** and one identity should carry on.
- **Use B when the old session should keep living:** it still does another job, or it shares its directory with siblings, or it stays as the archive. B also suits giving the agent a new role through a compaction.

## 1. Prepare the new home (every route)

Do this before the move, so the session's first open in the new directory loads its brief.

1. **Place it.** The root's orchestrator picks the unit by the root's rules (`~/STUDIO/ROOTS.md`): HOMESTEAD Orch for `~/HOMESTEAD`, STUDIO Orch for `~/STUDIO`. Make the unit directory.
2. **Write the brief from evidence.** Either the session drafts its own brief into the unit, since it holds its own history, or the orchestrator writes it from the transcript and Dominik's words, when he has asked the orchestrator to settle the role. That's what STUDIO Orch did on 2026-10-29. It's `CLAUDE.md` for Claude, or `AGENTS.md` for Grok. The brief has:
   - its job, in Dominik's words, quoted from its transcript;
   - what it owns;
   - who it works with;
   - which files it keeps true;
   - what it may do.

   The orchestrator checks the draft against the transcript (`python3 ~/FORGE/lab/2026-10/homestead-roster/roster.py --portraits`) and the root's rules, and Dominik reads it. Under `ROOTS.md`, a Grok unit Dominik drives gets an `AGENTS.md` in words he has accepted; no Claude agent writes it.
3. **Write the README,** with **Pointed at from outside**: every skill link, LaunchAgent, hook rule, other agent's file and Berth folder that names the old directory or the new one.
4. **Carry the memory over deliberately.**
   - Read the old directory's memory index: `~/.claude/projects/<old directory, encoded>/memory/MEMORY.md`.
   - Facts that belong to this agent go into its new brief.
   - Don't copy the whole memory directory. Every session in the old directory shares it.
   - Inside a git repo, memory may be keyed by the repo root rather than the exact directory (Berth Developer saw this, but didn't test it).
5. **Check directory-scoped machinery.** Look at the old directory's `.claude/settings*.json`, its hooks and its project skills. Any of those that the agent relied on won't follow it. For example, Learning's session-identity hook only fires for sessions working under `~/Work/Research`. Recreate what's needed in the unit, or accept the loss, and write down which.
6. **Trust.** If Claude has never been trusted in the new directory, its "Do you trust this folder?" dialog appears at the first open, and Berth doesn't answer it. Plan who clears it: Dominik, or an agent following cmux-claude's **Dialog**. A Grok session needs Grok's folder trust too.
7. **The Berth folder.** If the root mirrors its units in Berth, create the matching Berth folder (`folder create`) and refile the session there (`update {folder}`). This is separate from the move.

## 2. Route A: move it (Claude)

**Before you start:** Dominik's yes; the session idle (not `busy`); and section 1 done.

1. **Record where it is now:** `session {"session": "<id>"}`. Note its `cwd`, its `folder`, and its transcript, which is `~/.claude/projects/<old directory, encoded>/<session id>.jsonl`.
2. **Move it:** `update {"sessions": "<id>", "cwd": "<absolute unit directory>", "cycle": true}`.
   - Leave out `cycle` to have it move at its next wake instead.
   - Your own session is queued until your turn ends.
   - From the shell: `berth cwd <id> <directory> --cycle`.
3. **At the open,** `screen`, and clear the trust dialog if it's up.
4. **Verify:**
   - `session` shows the new `cwd`. Its `cwd_note` goes away once the session has recorded a turn there.
   - Ask the agent, in one line, which `CLAUDE.md` files and which memory index it now has. It should name its new brief.
5. **Sweep the references:**
   - grep for the old directory;
   - update the **Pointed at from outside** tables and the root's records (roster, directory map);
   - log the move.
6. **Retire the old directory last,** once no session's `cwd` is in it. Check with `sessions {"folder": "/", "fields": ["cwd"]}`. After that, the old directory is only files. A skill directory, for example, can then move into the unit's `skill/` and be symlinked back, with the usual grep sweep.

**First real runs, 2026-10-29.** STUDIO Orch moved two Claude sessions out of `~/.claude/skills/mac-app-config` into `~/STUDIO/Mac/Config/Settings-Agent/` and `~/STUDIO/Mac/Config/Mac-Developer/` with `update {cwd}` plus `cycle`. Both resumed the same conversation, and neither showed a trust dialog; the bypass-permissions settings were in effect.

What it keeps, and what changes:
- **Keeps:** the same conversation; the same name; the same Berth folder.
- **The transcript** stays in the old directory's project; new turns record the new directory.
- **From the new directory:** the `CLAUDE.md` chain, auto-memory, project settings and hooks, and trust.

## 3. Route B: fork it into the directory (Claude)

1. **Fork it:** `create {"from": "<id>", "cwd": "<absolute unit directory>", "title": "<the agent's name>"}`.
   - The fork is filed in the source's Berth folder; refile it.
   - It runs in the new directory from its first turn.
   - Its transcript is new, under the new directory's project.
2. **Clear the trust dialog** if it appears.
3. **Compact the fork into its role** with Berth (SKILL.md **Compact**), never by placing `/compact` yourself: `send {"session": "<fork id>", "compact_file": "<role note>"}`. Or do route B in one step: `create {"from": "<id>", "cwd": "<unit>", "title": "<name>", "compact_file": "<role note>", "prompt_file": "<first job>"}` forks into the unit, compacts before its pane opens, then sends the job. The note says who it is now, where its unit is, and that its brief is that unit's `CLAUDE.md`. `session` shows `compaction.state` `done` once Claude has recorded it.
4. **The original** keeps its job, or is renamed "<title> (retired)" and parked, on Dominik's word. Its transcript is the archive.
5. **Verify and sweep** as in route A, steps 4 to 6.

## 4. Route C: a fresh session and a handoff (Grok, or a clean start)

1. **The old session writes `HANDOFF.md` into the new unit,** at compaction quality:
   - who it is;
   - its job, in Dominik's words;
   - what it owns;
   - what it changed, with how to undo each change;
   - what's open;
   - where its history is (the transcript path, or for Grok, `~/.grok/sessions/<directory>/<id>/`).
2. **Start the new session in the unit:** `create {"title": "<name>", "cwd": "<unit>", "folder": "<Berth folder>", "prompt_file": "<job>"}`, with `"agent": "grok"` for Grok.
   - The first job: read your brief and `HANDOFF.md`, then say what you understand your job to be.
   - For a Grok session Dominik drives, he gives it its first job; per `ROOTS.md`, no Claude agent prompts it.
3. **The old session** is renamed "<title> (retired)" and parked, on Dominik's word.

**Grok, someday.** Berth Developer moved a closed test session's directory under `~/.grok/sessions/` to the new directory's key, and `grok -r` then worked in the new directory. That was one run, and it rewrites Grok's own store. Berth doesn't offer it, and it would need Dominik's go-ahead.

## Pitfalls

- **A stray `cd` isn't a move, but it isn't harmless either.**
  - Without a chosen directory, Berth reopens a session in the last directory its transcript recorded.
  - Claude records a `cd` into a directory under the one it started in. That's how HOMESTEAD Orch drifted into `~/HOMESTEAD/_orchestrator` on 2026-10-28.
  - Long-lived sessions never `cd`. Moves go through `cwd`.
- **The trust dialog stalls an unattended open.** Someone has to clear it.
- **Sessions sharing a directory move one at a time,** and the directory is retired only after the last one.
- **Directory-scoped hooks and settings don't travel.** Check section 1, step 5, before the move, not after.
- **Never `mv` a directory a session runs in.** Move the session first; then the old directory is just files.
- **Never edit Berth's catalog, and never move transcripts in `~/.claude/projects` by hand.**
