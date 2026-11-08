# Stage 0: survey, explain, decide

**Goal.** Know exactly how your operator works today, show them where they are going, and settle the choices that shape the install. Nothing outside `~/.berth-onboarding/` changes in this stage, except moving the kit if it is in a temporary place.

**Runs in:** the chat they started you in, in their current terminal.

## Steps

1. **Make the progress folder.** `mkdir -p ~/.berth-onboarding`. Write `~/.berth-onboarding/PROGRESS.md` from the template at the end of this file: today's date, `Stage 0: started`, and the kit's path (the repository's top folder; update it if you move the kit in step 5).

2. **Survey.** Run, and read the output with them:

   ```bash
   python3 <kit>/tools/inventory.py --json ~/.berth-onboarding/survey.json
   ```

   It shows their macOS, which of Claude Code, cmux, Python 3.12, uv, Homebrew, and Grok are installed, which terminal apps are running, Claude's default model and permission mode, their MCP servers and skills by name, every Claude conversation running now (name, status, directory, session id, and the terminal app and tty it runs in), any Claude process that left no session record, and the conversations active in the last 14 days that are not running. It never prints message text or secrets.

   Check Claude Code's version. The migration in stage 2 relies on `~/.claude/sessions` records, and this kit was tested on 2.1.31x. If the survey lists Claude processes with no session record, or their Claude Code is older, have them update it (`claude update`) and restart those tabs before stage 2.

3. **Ask what the survey cannot see.** Keep it short; these answers decide the folder layout and the pace:
   - What are the groups of work? (clients, products, school, personal). These become Berth folders.
   - Which of the running conversations matter, and which can be dropped?
   - Do they ever run several agents on one job, or want to? Do they use Grok, Codex, or other agents?
   - How do they update macOS today, and what happens to their tabs when they do?
   - Anything they already automated around Claude (hooks, a status line, tmux, scripts)? Note it; nothing in this kit should break it.

4. **Explain the target**, in their terms, using their own sessions as the example. Use `docs/WHY.md` and `docs/WAYS-OF-WORKING.md`. Five minutes, not a lecture:
   - one cmux window, one workspace per conversation, a sidebar that says what each is doing;
   - Berth folders for their groups of work; sleep, wake, cycle, fork; ⌘⇧B for the Berth screen;
   - the update routine (sleep all, update, wake all) and the crash case (Berth brings back what was open);
   - agents that start and run other agents through the `berth` MCP.

5. **Decide.** Walk through `docs/DECISIONS.md` one item at a time. Recommend; let them choose; write each answer into `~/.berth-onboarding/DECISIONS.md`. The ones that matter most:
   - where the kit lives (move it now if it is in Downloads, on the Desktop, or in `/tmp`);
   - permission mode and effort for sessions agents start (Dominik: skip permission prompts, effort xhigh);
   - their model rule;
   - Berth's sidebar replacing cmux's own, and ⌘⇧B;
   - Grok yes or no.

6. **Plan the move.** List the conversations they want to keep, with the Berth folder each goes into. That list becomes the migration plan in stage 2. Nothing moves yet.

7. **Update PROGRESS.md**: `Stage 0: done`, the decisions in one line each, the planned folders, the conversations to keep.

## Checks

- `~/.berth-onboarding/survey.json`, `DECISIONS.md`, and `PROGRESS.md` exist.
- The kit is in its permanent place, and `PROGRESS.md` says where.
- The operator can say, in one sentence, what sleep, wake, and cycle do.

## Ends with

Stage 1 runs in this same chat, if they want to keep going. If they stop here, give them the "Every next chat" message from `onboarding/terminal/KICKOFF.md`, filled in.

## Template: `~/.berth-onboarding/PROGRESS.md`

```markdown
# Berth onboarding

Kit: /Users/<them>/berth-kit
Started: 2026-..-..

## Stages
- [x] 0 survey: done 2026-..-..
- [ ] 1 cmux
- [ ] 2 Berth and the move
- [ ] 3 agents running agents
- [ ] 4 make it yours

## Decisions (details in DECISIONS.md)
- kit location: ...
- spawned sessions: permission mode ..., effort ...
- model rule: ...
- sidebar / ⌘⇧B: ...
- Grok: ...

## Folders planned
- Work/Client A: <directories>
- ...

## Conversations to keep (moved in stage 2)
- <title>: <directory>, session <id>, folder <folder>

## Notes for the next chat
- ...
```
