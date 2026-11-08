# START HERE: onboarding to Berth (for agents)

Paths below are relative to the kit: the repository's top folder.

You are a Claude Code agent. Your operator already uses Claude Code. Today they run it in an ordinary terminal app with a pile of tabs, one conversation per tab. Their friend Dominik runs twenty to thirty agents at once on one Mac, and does it with a system you are going to install and teach: **cmux** (a terminal app for agents), **Berth** (the catalog of every session, with a watcher and a screen), two **MCP servers** (`berth` and `cmux-mcp`), and three **skills** (`berth`, `cmux-claude`, and, if they use Grok, `cmux-grok`). Dominik is giving your operator his whole setup, so they can start where he is.

Your job is to move your operator from tabs to that setup **over several chats**, at their pace, and to make it theirs, not a copy of Dominik's. Read this file to the end before you act.

Kit v3.1.2, built 2026-11-07 (Berth as of 2026-11-07). Tested on macOS 27, Python 3.12, cmux 0.65.2 (0.65 or newer is required), Claude Code 2.1.31x.

## 1. What your operator gets

When the onboarding is done:

- Every Claude conversation runs in a cmux workspace and is a row in Berth's catalog, filed in Berth folders they chose (`Work/Client A`, `Personal/Reading`).
- Any session can be put to **sleep** (pane closed, screen and draft saved) and **woken** (a new pane resumes the same conversation, in the permission mode it was in; see `docs/DECISIONS.md` for the one exception), one at a time or by folder. Before a macOS or cmux update: sleep everything, update, wake everything. If cmux quits or crashes, Berth brings back what was open by itself.
- **Cycle** restarts a session on the same conversation: how a session picks up a new MCP server, skill, or setting.
- **Fork** copies a conversation into a new session next to it, with its own id.
- cmux's sidebar shows every session with one honest word (Working, Ready, Needs you, Waiting on 2 subagents, Urgent) and its folder's color; ⌘⇧B jumps to the Berth screen.
- **Agents run agents.** From any session, an agent can start a new Claude on a job written to a file, watch its stop files, read its pane, answer its dialogs, fork it, file it, restart it, and raise an urgent flag when the operator is needed. That is the productivity multiplier.

`docs/WHY.md` explains why it is built this way. `docs/ARCHITECTURE.md` explains how the pieces fit.

## 2. How the onboarding runs: stages and chats

| Stage | File | Runs in | What happens | Ends with |
| --- | --- | --- | --- | --- |
| 0 | `onboarding/terminal/stages/0-survey.md` | the chat they started you in (their old terminal) | Read their setup (`tools/inventory.py`), explain the target, settle the day-one decisions with them (`docs/DECISIONS.md`). Changes nothing. | `~/.berth-onboarding/PROGRESS.md` and `DECISIONS.md` written |
| 1 | `onboarding/terminal/stages/1-cmux.md` | the same chat | They install cmux; you set its options; they try it with one throwaway session. | the operator opens cmux and starts a **new chat inside it** |
| 2 | `onboarding/terminal/stages/2-berth.md` | chat 2, in cmux | Install Berth (watcher, screen, sidebar, ⌘⇧B); make their folders; move their old tabs' conversations into cmux (`tools/migrate.py`); tour the screen; prove sleep and wake. | install the agent tools, then restart this chat (cycle) or start chat 3 |
| 3 | `onboarding/terminal/stages/3-agents.md` | chat 3, in cmux | The `berth` and `cmux-mcp` MCP servers and the skills are loaded: spawn a worker on a job, watch it, fork, cycle, urgent. Apply their choices to how new sessions start. | they have run a two-agent job end to end |
| 4 | `onboarding/terminal/stages/4-make-it-yours.md` | chat 4 and later | Their folder layout, orchestrator sessions, CLAUDE.md conventions, the update routine, troubleshooting, optional Grok. | the setup is theirs and they know how to keep it working |

A stage can take more than one chat; a chat can do more than one stage when the operator wants to keep going. **Never skip a stage's checks** (`tools/doctor.py --stage N`).

### The progress file

State lives on disk, not in your memory, because every stage ends in a new chat:

- `~/.berth-onboarding/PROGRESS.md`: where this kit is (absolute path), which stage is done, what was decided, what is left, notes for the next chat. Read it at the start of every chat; update it at the end of every stage, before you tell the operator what to do next.
- `~/.berth-onboarding/DECISIONS.md`: their answers to `docs/DECISIONS.md`.
- `~/.berth-onboarding/survey.json`, `migration.json`: outputs of the tools.

When a stage ends, give the operator the exact message to paste into the next chat. `onboarding/terminal/KICKOFF.md` has the template.

## 3. Ground rules

- **Ask before anything that changes their Mac.** Show the dry run first (`tools/install.sh <stage> --dry-run`), say what it changes in plain words, then run it on their yes.
- **Permission prompts and safety dialogs in other sessions belong to the operator.** Never approve a tool permission, accept the bypass-permissions warning, or answer a plan approval in a session you started, unless the operator told you to for that session. Tell them, or raise `urgent`. The only dialog you clear yourself is Claude's folder-trust question, after saying so. (The cmux-claude skill's **Dialog** section describes Dominik's Mac, where bypass is pre-accepted; stage 3 rewrites it for this one.)
- **Never close, kill, or type into a session that is not yours** unless the operator asked for exactly that. Their old tabs are theirs to exit (`/exit`); `tools/migrate.py` refuses a conversation that is still running.
- **One conversation, one place.** Never resume a conversation that is still running somewhere else.
- **No secrets** in files, prompts, or progress notes. Never print tokens, `~/.claude.json` server configs, or the `authToken` in `~/.claude/ide/*.lock`.
- **Their choices, not Dominik's.** Dominik's defaults (auto permission mode, effort xhigh, his model rules, his folder names) are written into this kit. `docs/DECISIONS.md` lists every one, where it lives, and how to change it. Ask; then apply what they chose.
- **Teach while you go.** The operator should be able to run the Berth screen and the update routine without you by the end. Have them press the keys.
- When something fails, stop, show what happened, read `docs/TROUBLESHOOTING.md`, and fix the cause. Never edit Berth's catalog (`~/.local/share/berth`) by hand.

## 4. What is in this kit

| Path | What it is |
| --- | --- |
| `START-HERE.md` | This file. |
| `README.md` | At the top of the kit: the first message your operator sends, per track. `onboarding/terminal/KICKOFF.md` is the message for every next chat. |
| `TIPS.md` | **For the operator, not you:** the non-obvious workflows (⌘⇧B lands on the current session, drafts survive, cycle, `Z`/`W`, the sidebar's words). Point them to it once Berth runs; keep it true if you change how things work. |
| `onboarding/terminal/stages/` | The five stages, in order. Each says its goal, steps, checks, and how it ends. |
| `docs/WHY.md` | The problem Berth solves and the one idea it rests on. |
| `docs/ARCHITECTURE.md` | cmux, Berth (catalog, watcher, screen, sidebar), the two MCP servers, the skills, and how a session moves between them; every file on disk. |
| `docs/WAYS-OF-WORKING.md` | How Dominik actually works with dozens of agents: folders, orchestrators, prompts from files, stop files, urgent, the update routine. |
| `docs/DECISIONS.md` | Every day-one choice, Dominik's answer, the options, and where to change it. |
| `docs/INVARIANTS.md` | Rules that must not break, and the incidents behind them. |
| `docs/TROUBLESHOOTING.md` | Symptoms, causes, fixes. |
| `docs/UNINSTALL.md` | Everything the kit puts outside its directory, and how to undo each. |
| `docs/HISTORY.md` | What was built, in what order, and why. |
| `docs/OPEN-ITEMS.md` | Known limits, the 3 failing tests, things not done. |
| `tools/inventory.py` | Read-only survey of their Claude setup and every running conversation, with the terminal it runs in. |
| `tools/migrate.py` | Moves conversations from terminal tabs into cmux (plan, then apply). Never kills or types into a tab. |
| `tools/install.sh` | The installer, by stage (`cmux`, `berth`, `agents`, `grok`), with `--dry-run`. |
| `tools/doctor.py` | Checks each stage's end state and says how to fix what is missing. |
| `tools/cmux_action.py` | Adds the ⌘⇧B action to cmux's config (used by `install.sh berth`). |
| `packages/berth/` | Berth: the code, tests, `README.md` (the full manual), `docs/` (`BERTH-STATE-MACHINE.md`, `BERTH-CRUD.md`, the design notes), `skill/` (the `berth` skill), `cmux-sidebar/` (the sidebar). |
| `packages/cmux-mcp/` | The cmux-mcp server (agents read and type into panes with it). |
| `skills/cmux-claude/`, `skills/cmux-grok/` | The skills for driving Claude and Grok panes; `references/prompts.md` is how to write a job for a child agent. |
| `grok/hooks/cmux-session.json` | Grok's cmux hook (stage 4, only with Grok). |

Dominik's own docs inside `packages/` and `skills/` sometimes say where things live on his Mac: `~/FORGE/systems/agents/berth` is `packages/berth` here, and `~/work/bach-coding-stack` is this repository's top folder. Examples that name his folders and projects were replaced with neutral ones.

## 5. Before stage 0

1. Check the version: `git -C <kit> describe --tags`, and tell the operator.
2. The kit is the repository's top folder, two levels above this file. It must live somewhere permanent, because everything installed runs from it. If it is in Downloads, Desktop, Documents, an iCloud folder, or `/tmp`, ask the operator where it should live (`~/berth-kit` is a good default) and move it before stage 1. Record its absolute path in `PROGRESS.md`.
3. Optional, and a good habit: run the tests in a throwaway virtualenv (`docs/TROUBLESHOOTING.md` → "Run the tests"). Expected: Berth 601 passed, 3 failed; cmux-mcp 55 passed. The 3 known failures are older than this kit and harmless (`docs/OPEN-ITEMS.md`).

Then open `onboarding/terminal/stages/0-survey.md`.
