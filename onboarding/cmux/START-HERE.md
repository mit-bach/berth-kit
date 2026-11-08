# START HERE: Berth kit for cmux (for agents)

Paths below are relative to the kit: the repository's top folder.

You are an agent, Claude Code or Grok Build, helping your operator install and start using Berth, the system Dominik built to run many agent sessions at once in cmux. Your operator is new to this way of working: explain every step in plain words before you take it, and show every dry run. Read this file first, all of it.

Kit v3.1.0, built 2026-11-07 (Berth as of 2026-11-07). Tested on macOS 27, Python 3.12, cmux 0.65.2 (0.65 or newer is required), Claude Code 2.1.31x, Grok Build 1.0.67. Your operator's Mac starts empty: no catalog, no sessions, none of Dominik's state.

## 0. Why Berth

Dominik keeps twenty to thirty agent sessions running, each a long conversation with its own job, each in a cmux workspace. Berth makes that manageable:

- every session is a row in one catalog, filed in nested folders, colored by folder in cmux's sidebar;
- any session can be put to sleep (pane closed, conversation and unsent draft kept) and woken (a new pane resumes the same conversation), or cycled (off and on again in one step);
- before an update, `Z` puts everything to sleep and `W` wakes it all; when cmux quits or crashes, what was open comes back by itself;
- forks open next to their source; names stay the same everywhere; deleted sessions wait 30 days in a trash;
- agents do all of it too, through two MCP servers: `berth` (find, start, fork, wake, sleep, cycle, file, rename, pin, delete) and `cmux-mcp` (see another session's screen, type into it).

`docs/WHY.md` explains the idea (a session is a conversation, not a pane). Read it next.

## 1. What is in this kit

| Path | What it is |
| --- | --- |
| `START-HERE.md` | This file. |
| `README.md` | At the top of the kit: the prompt your operator sent to start you ("cmux"). |
| `TIPS.md` | **For the operator, not you:** the non-obvious workflows (⌘⇧B lands on the current session, drafts survive, cycle, `Z`/`W`, the sidebar's words). Point them to it once Berth runs; keep it true if you change how things work. |
| `tools/install.sh` | The installer, in stages: `cmux`, `berth`, `agents` (Claude Code), `grok`. `--dry-run` changes nothing. |
| `tools/doctor.py` | Checks each stage: `ok`, `FIX` with what to do, or `note` for a recommended piece they may have declined. |
| `tools/cmux_action.py` | Used by the installer: the ⌘⇧B shortcut that opens the Berth screen. |
| `tools/inventory.py`, `tools/migrate.py` | Only if they already run Claude in other terminal apps: the survey lists those conversations, and the migration reopens them in cmux, filed (each script's top comment says how). |
| `onboarding/cmux/FIRST-DAY.md` | The walkthrough to do with your operator once it is installed. |
| `docs/WHY.md`, `docs/ARCHITECTURE.md`, `docs/WAYS-OF-WORKING.md` | Why Berth exists, how its pieces fit, and how Dominik works with it. |
| `docs/DECISIONS.md` | The choices built into the kit, with the options. |
| `docs/INVARIANTS.md` | The rules that must not break. |
| `docs/TROUBLESHOOTING.md`, `docs/UNINSTALL.md` | When something is off; how to undo every piece. |
| `docs/HISTORY.md`, `docs/OPEN-ITEMS.md` | How it got here; what is unfinished, including the 3 known failing tests. |
| `packages/berth/` | Berth: code, tests, `README.md` (the full manual: screen keys, CLI, MCP, folders, colors, forks, restarts), `skill/` (the `berth` skill), `docs/` (`INDEX.md` maps the code). |
| `packages/cmux-mcp/` | The cmux-mcp server. Agents use two of its tools, `screen` and `input`; Berth's own MCP does the rest. |
| `skills/cmux-claude/`, `skills/cmux-grok/` | The skills that teach agents to start and drive Claude and Grok sessions. Each sends the agent to the `berth` skill for folders, scopes, and the trash. |
| `grok/hooks/cmux-session.json` | A fallback copy of Grok's cmux hook. |

## 2. Reading order

1. This file.
2. `docs/WHY.md`, then `docs/ARCHITECTURE.md`.
3. `packages/berth/README.md`, the manual. Skim it now; you will teach from it.
4. `docs/DECISIONS.md`.
5. `onboarding/cmux/FIRST-DAY.md`.
6. The skills, before you start sessions for them: `packages/berth/skill/SKILL.md`, and `skills/cmux-claude/SKILL.md` (Claude) or `skills/cmux-grok/SKILL.md` (Grok).
7. `docs/INVARIANTS.md`, `docs/OPEN-ITEMS.md`, and code when you need it.

## 3. Ground rules

- **Ask before every change to their Mac.** Show the dry run, say in one or two sentences what it does, wait for a yes.
- **Permission prompts and warnings in other sessions belong to the operator.** Never approve a tool permission, accept a bypass-permissions warning, or answer a plan approval in a session you started, unless they told you to for that session. The only dialog you clear yourself is the folder-trust question, after saying so. (The skills' **Dialog** sections describe Dominik's Mac, where bypass is pre-accepted; section 5 rewrites them for this one.)
- **Never close, restart, or type into a session that is not yours** unless they asked for exactly that.
- **Never edit the catalog by hand.** Use `berth` commands or the MCP. If something is off, `berth check` and `docs/TROUBLESHOOTING.md`.
- **If a step fails, stop and say what happened.** Do not work around it.

## 4. Decide with your operator

Go through `docs/DECISIONS.md` one row at a time, in plain words. Recommend; let them choose. Write their answers to `~/.berth-onboarding/DECISIONS.md` (make the folder) so a later chat can read them. The ones that matter most:

1. **Where the kit lives.** Everything installed runs from the kit's folder for good. Where they cloned it is fine. Not Downloads, Desktop, Documents, iCloud, or `/tmp`; the installer refuses those.
2. **cmux socket access.** Berth's watcher runs in the background, outside cmux, so cmux must let it connect (`automation.socketControlMode = automation`). Without it nothing gets cataloged. Say plainly what it means: any program running as them can control cmux.
3. **Permission mode for sessions agents start.** New sessions start in their Claude default mode, `permissions.defaultMode` in `~/.claude/settings.json` (Dominik's is `auto`, with effort `xhigh`). A newcomer may want prompts (manual) or `acceptEdits`. Applied in section 5.
4. **The model** a started Claude uses: their `settings.json` default unless they want a rule.
5. **Berth's sidebar** in place of cmux's own, and **⌘⇧B** for the Berth screen. Both recommended; both optional.
5a. **Where links open.** The installer sends links to their normal browser instead of cmux's built-in one (`--cmux-browser` keeps cmux's). Recommend the normal browser: people new to cmux find links opening inside the terminal confusing.
6. **Claude Code, Grok, or both.** Install the `agents` stage for Claude Code and the `grok` stage for Grok.

The "Applied in" column of `DECISIONS.md` numbers the stages of Dominik's longer, multi-chat onboarding kit. Here everything happens in one pass, in section 5's order.

## 5. Install

**Before you install.** Check, and install what is missing on their yes:

- macOS, and **cmux 0.65 or newer in `/Applications`** (Berth starts Claude through cmux's wrapper there). `cmux --version`.
- **uv** (it fetches Python 3.12 by itself), or Python 3.12. `brew install uv` with Homebrew, else `curl -LsSf https://astral.sh/uv/install.sh | sh` (installs to `~/.local/bin` and adds it to the shell profile).
- Claude Code (`claude`) and/or Grok Build (`grok`); you are running in one of them.

**Then, one stage at a time**, each with `--dry-run` first (paths are the kit's):

```bash
tools/install.sh cmux --dry-run      # then without --dry-run
tools/install.sh berth --dry-run     # add --no-sidebar if they keep cmux's own sidebar
tools/install.sh agents --dry-run    # Claude Code: the berth and cmux-mcp MCP servers, the berth and cmux-claude skills
tools/install.sh grok --dry-run      # Grok: the berth and cmux-grok skills, Grok's cmux hook, both MCP servers in ~/.grok/config.toml
python3 tools/doctor.py              # add --stage 4 to check Grok
```

What each stage changes, and how to undo it, is `docs/UNINSTALL.md`. Running a stage again is safe. If `agents` says an MCP server named `berth` or `cmux-mcp` already exists and points elsewhere, show it to the operator; `--replace-mcp` replaces it on their yes.

**Keep a baseline.** The kit has no git remote. With their yes, `git init && git add -A && git commit -m "Berth kit as received"` in the kit, so every change after shows in `git diff`.

**Apply the permission choice** (decision 3), if it differs from Dominik's. Show each change as a diff first:

- Their default mode: `permissions.defaultMode` in `~/.claude/settings.json`. Berth starts Claude sessions with no permission flag, so new sessions take it. Effort (`--effort xhigh`) and the model rule (`claude_model`) are in `spawn_command`, `packages/berth/berth/mcp.py`.
- The **Spawn** and **Dialog** sections of `skills/cmux-claude/SKILL.md` (and `cmux-grok` for Grok's `--always-approve`), outside the `berth-core` block, which must stay identical in both. Rewrite Dialog for this Mac: the agent clears only the folder-trust question; any other prompt goes to the operator.
- How a cycle keeps the mode: `docs/DECISIONS.md`, the note on permission modes.

## 6. Restart this chat, then verify

This chat started before the MCP servers were registered, so it does not have their tools yet. Restart it on the same conversation, which is also the first thing your operator sees Berth do:

1. `berth sessions --json`: the row marked `"you": true` is this chat.
2. Tell the operator what happens next, and that once the chat reopens they should send it one message ("continue"). Then run `berth cycle <that id>` and **end your turn**. Berth closes this pane when your turn ends and reopens it on the same conversation, with the new tools.
3. If nothing happens within a minute, they press ⌘⇧B, put the cursor on this chat's row, and press `c`.

In the reopened chat, check:

1. `python3 tools/doctor.py`: no `FIX`.
2. `berth status`: the watcher is `up`, cmux is `up`.
3. ⌘⇧B (have the operator press it): a workspace named Berth opens running the Berth screen, cursor on this chat's row.
4. Every open workspace is filed, in Inbox to start with. `berth check` says `ok`.
5. The MCP tools: call `mcp__berth__status`, then `mcp__berth__sessions` with no arguments (your own folder). Call cmux-mcp's `screen` on your own Berth id.

## 7. First day

Walk your operator through `onboarding/cmux/FIRST-DAY.md`, letting them press the keys: the screen, folders, a session, sleep and wake, cycle, fork, the trash, the update routine, and an agent starting another agent. Then hand them `TIPS.md` in the kit: it is the one document written for them, and it holds the habits that make them fast.

## 8. Keeping it working

- After changing Berth's code, a skill, or an MCP server, restart what has it loaded: the watcher (`launchctl kickstart -k gui/$(id -u)/com.bach.berth.watch`), the screen (`q` in it, then ⌘⇧B), and agent sessions (cycle them: `c` in the screen, or the MCP `control` tool). The skills are links into the kit, so an edit is live for the next session.
- Before a macOS or cmux update: `Z` in the screen (everything sleeps), update, then `W` (everything wakes). After a cmux update, run `berth sidebar install` again, then `python3 tools/doctor.py`.
- `berth check` says whether Berth matches cmux. `docs/TROUBLESHOOTING.md` covers the rest; `docs/UNINSTALL.md` undoes everything.
- The tests: `docs/TROUBLESHOOTING.md`, "Run the tests". Expected: Berth 601 passed, 3 failed; cmux-mcp 55 passed.

## 9. What is not in this kit

- Secrets of any kind. The bundle was searched for them before zipping, and for the source operator's own addresses.
- Dominik's live state: his catalog, sessions, transcripts, hook files, logs.
- Build output: `.venv`, `__pycache__`, `.pytest_cache`, the LaunchAgent plist (`berth install` writes it).
- The rest of Dominik's setup, and the skills that serve only his company's work.
