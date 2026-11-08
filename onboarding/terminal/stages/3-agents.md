# Stage 3: agents running agents

**Goal.** This chat has the `berth` and `cmux-mcp` tools, your operator's choices for how new sessions start are applied, and together you have run a job through a second agent end to end: started it on a prompt file, watched its stop file, read its pane, forked it, and put it to sleep.

**Runs in:** chat 3 (or chat 2 after its cycle), inside cmux. First read `PROGRESS.md` and `DECISIONS.md`.

## Steps

1. **Check the tools are loaded.** `python3 <kit>/tools/doctor.py --stage 3`. Then call `mcp__berth__status`: it says who you are (`you`: your Berth id, folder, and working directory), the counts, and that cmux and the watcher are up. If the tools are missing, this session started before they were registered: cycle it (`berth cycle <your id>`, then end your turn) or start a new chat.

2. **Read the skills now**, in full: `~/.claude/skills/berth/SKILL.md` (sessions and folders: scopes, wake, sleep, cycle, create, fork, move, rename, pin, delete, trash, urgent, the refusals) and `~/.claude/skills/cmux-claude/SKILL.md` (starting a Claude on a job, reading its pane, clearing its dialogs, prompts from files, stop files). Read `skills/cmux-claude/references/prompts.md` before you write any prompt for another agent. From now on, follow them; they are what every agent on this Mac will follow.

3. **Keep a baseline first.** The kit has no git remote. With their yes: `cd <kit> && git init && git add -A && git commit -m "Berth kit as received"` (it needs their git name and email set). Every change after this shows in `git diff`.

4. **Apply their choices for new sessions** (`DECISIONS.md`). Show each change as a diff before you make it:
   - **Permission mode** of sessions an agent starts: their default mode, `permissions.defaultMode` in `~/.claude/settings.json` (Claude Code's settings documentation lists the values; Dominik's is `auto`). Berth's `create` passes no permission flag, so every new session takes that default and nothing in Berth changes. **Effort**: `spawn_command` in `<kit>/packages/berth/berth/mcp.py` passes `--effort xhigh` (Dominik's); change or drop it there. A wake or cycle reopens each session in the mode its status strip showed, and a manual-mode session (which shows none) in their default (`docs/DECISIONS.md`, decision 2). The cmux-claude skill's **Spawn** and **Dialog** sections describe Dominik's setup; edit them to match (outside the `berth-core` block, so the two skills' shared block stays identical).
   - **Model rule.** `claude_model` in the same file uses `"model"` from `~/.claude/settings.json`, and Opus when that is unset or a Fable model; the `berth` and `cmux-claude` skills forbid Fable unless the operator names it in the chat. Dominik's rule; keep or change it in all three places.
   - **The Dialog section** of their `cmux-claude` skill (`skills/cmux-claude/SKILL.md`, outside the `berth-core` block) describes Dominik's Mac, where `bypassPermissions` and `skipDangerousModePermissionPrompt` are set and agents press Enter on the bypass warning. Rewrite it for this Mac: the agent clears only the folder-trust question; the bypass-permissions warning, tool permission prompts, and plan approvals in a worker go to the operator (tell them, or raise `urgent`). If they kept bypass, the operator accepts the warning in the first worker once, or sets `skipDangerousModePermissionPrompt` themselves.
   - Run Berth's tests after a code change (`docs/TROUBLESHOOTING.md` → "Run the tests").
   - **Restart before the first worker**, if you changed `mcp.py` (effort, model): `berth mcp` runs inside this session and keeps the code it started with. Call `control {"action": "cycle", "sessions": "me"}`, tell the operator to send "continue" once the chat reopens, and end your turn. In the reopened chat, check before step 5 (the next worker's `recipe`, from `session` with `fields: ["recipe"]`, shows the new flags). A change to `settings.json` alone needs no restart: each new session reads it when it starts.

5. **A job through a second agent.** Pick a small task with the operator. Make the first one read-only or in a scratch directory (a summary of a directory, a report on a repo's tests), so a worker's first run cannot change their code. Then, following the cmux-claude skill's **Spawn**:
   - write the job to a file (per `prompts.md`), with an absolute stop file the worker writes when done (`DONE` or `FAILED` plus a line);
   - `mcp__berth__create {"title": ..., "cwd": "<repo>", "folder": "<their folder>", "prompt_file": "<job file>"}`;
   - `mcp__cmux-mcp__screen` on the new id: if Claude asks to trust the folder, say so and clear it (cmux-claude **Dialog**); any other dialog or permission prompt goes to the operator;
   - watch the stop file with the skill's `scripts/wait-stop-files.py` (run in the background), not by polling the screen. It prints `DONE` once every stop file exists and is not empty (then read the file: it may say the worker failed) and `FAILED` only when it times out;
   - when it ends, read the result from disk and show the operator, in the sidebar, how the worker's word went Working → Done.

6. **Fork it.** `mcp__berth__create {"from": "<worker id>"}`: a copy opens next to it, titled with ⑂, under its own session id. Give the fork a follow-up job (`input` with a prompt file, as the skill's **Drive** says). Never type `/fork`.

7. **Scopes.** Show the operator the reads that make a big desk manageable: `sessions {}` (your folder), `{"down": "all"}`, `{"folder": "/"}`, `{"query": "..."}`, `{"busy": true}`. Then a scope action as a dry run: `control {"action": "sleep", "scope": {"folder": "<their folder>"}}` returns `would` and `skipped` and changes nothing until `"confirm": true`, and only if they ask.

8. **Urgent.** Explain `update {"sessions": "me", "urgent": true, "why": "..."}`: the session turns red, first in the sidebar, and cmux posts a notification. Ask the operator which agents may raise it, and for what. Write the answer into `PROGRESS.md`; stage 4 puts it in their CLAUDE.md.

9. **Restart what is idle**, so every session has the new tools: `control {"action": "cycle", "scope": {"folder": "/", "busy": false}}` returns the dry run; with the operator's yes, again with `"confirm": true`. Busy sessions, you, and the Berth screen are skipped.

10. **Tidy.** Sleep or delete the worker and the fork (`delete` puts them in the trash for 30 days).

11. **Update PROGRESS.md**: `Stage 3: done`, the choices applied (with the lines changed), the job that ran, the urgent rule.

## Checks

- `python3 <kit>/tools/doctor.py` passes stages 1 to 3.
- The operator watched a worker start on a file, run, write its stop file, and be forked.

## Ends with

Stage 4 can start here or in a new chat. It is open-ended: their layout, their conventions, their routine.
