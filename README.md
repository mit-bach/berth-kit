# Berth kit

Dominik's agent setup, in one repository. **Berth** is a catalog of every agent session on a Mac: folders, sleep and wake, restart, fork, a 30-day trash, and the Berth MCP server, so agents can start and run other agents. **cmux-mcp** lets an agent look at and type into another session's pane. The **berth**, **cmux-claude** and **cmux-grok** skills teach agents to use both. There's an installer, a checker, and onboarding for three kinds of people. Your agent does the work: you send it one prompt from below and answer its questions.

Version 3.1.2. What changed: `CHANGELOG.md`. macOS only.

## Who sends which prompt

Find yourself here, follow your section's steps, and send your agent its prompt.

| You | Your track | How long |
| --- | --- | --- |
| You already run Claude Code in a terminal, with a pile of tabs | [Terminal](#terminal): your agent moves your conversations into cmux and Berth, then sets up agents running agents | several chats |
| You're new to cmux and to running many agents, with Claude Code or Grok | [cmux](#cmux): install, check, and a first day | one chat |
| You run Claude in Cursor and want your own version of this | [Cursor](#cursor): your agent studies Berth and your setup and brings you options; nothing is installed | one chat |
| You have an earlier version (a zip, or an older clone) | [Update](#update) | one chat |

Clone it where it will live for good, because everything installed runs from that folder. Don't use Downloads, Desktop, Documents or iCloud.

### Terminal

1. Clone the repository: `git clone https://github.com/mit-bach/berth-kit` (it makes a `berth-kit` folder).
2. Open the `berth-kit` folder in the terminal you use today, and start Claude Code there.
3. Send it this:

```text
My friend Dominik runs twenty to thirty Claude agents at once on his Mac, using a terminal app called cmux and a system he built called Berth: a catalog of every session, with folders, sleep and wake, restart, fork, and MCP tools so agents can start and run other agents. His whole setup is this repository, and you're running inside my clone of it. I want to move from my pile of terminal tabs to that setup, made to fit how I work, and I want you to run the move.

The repository's top folder is the kit, and the install runs from it for good. If you think it should live somewhere else, ask me before moving it. Read onboarding/terminal/START-HERE.md all the way through and do what it says, starting with stage 0. It is meant to take several chats. Keep track in ~/.berth-onboarding/PROGRESS.md as it says, and at the end of each stage tell me exactly what to paste into the next chat.

Ask me before you change anything on my Mac. Don't close or type into any of my other sessions. I'll exit those myself when it's time.
```

### cmux

1. Install cmux from https://cmux.com: drag it into Applications and open it once.
2. Clone the repository: `git clone https://github.com/mit-bach/berth-kit` (it makes a `berth-kit` folder).
3. In cmux, open a workspace, `cd` into `berth-kit`, and start `claude` (or `grok`).
4. Send it this:

```text
My friend Dominik built a system called Berth for running many agent sessions at once in cmux. His whole setup is this repository, and you're running inside my clone of it. I want you to install it on this Mac, check that it works, and teach me how to use it. I'm new to this, so explain each step in plain words before you do it.

1. The repository's top folder is the kit. It has to stay where it is for good, because the install runs from it. Run `git describe --tags` and tell me which version I have.

2. Read onboarding/cmux/START-HERE.md all the way through, then docs/WHY.md, before you change anything on my Mac.

3. Go through the decisions in START-HERE.md with me, one at a time, in plain words. Recommend, but don't decide for me.

4. For every install step, show me the dry run first and wait for my go-ahead.

5. When it's installed, restart this chat the way START-HERE.md says, so you get the new tools, and check that everything works.

6. Then walk me through onboarding/cmux/FIRST-DAY.md, letting me press the keys myself.

Ground rules: don't close, restart, or type into any of my other sessions unless I say so. Permission prompts and warnings in other sessions are mine to answer. Don't edit the Berth catalog by hand. If anything fails, stop and tell me what happened instead of working around it.
```

### Cursor

1. Clone the repository: `git clone https://github.com/mit-bach/berth-kit` (it makes a `berth-kit` folder).
2. Open the `berth-kit` folder in Cursor, and start a Claude chat there.
3. Send it this:

```text
My friend Dominik built a system called Berth for running lots of Claude sessions at once, and his whole setup is this repository, so I can build something like it for myself. You're running inside my clone of it. I'm not going to install his version as is. He runs his agents in a terminal app called cmux, and I run Claude inside Cursor. I want you to get fully up to speed on it, and then work out how it would apply to the way I work, so I don't have to.

1. Run `git describe --tags` and tell me which version I have.

2. Read onboarding/cursor/START-HERE.md all the way through, then follow its reading order: WHY.md, ARCHITECTURE.md, CURSOR-SETUP.md, TIPS.md, the Berth README, the state machine spec, MIGRATION.md, and the rest. Read enough of the code it points to that you understand how the catalog, sleep and wake, busy and drafts, the Berth MCP, and the Claude session tracking actually work. Don't stop at the summaries.

3. Run both test suites in their own virtualenv, the way START-HERE.md says, and tell me if the counts differ from what it expects.

4. Then look at my setup, without changing anything. Here's how I work: several Cursor windows open, each with several Claude chats. What I want is to turn every session off safely before I update my computer, then turn them all back on afterwards in the state they were in, ideally in the same windows, without resuming each one by hand, and to be able to restart a single session. Work through "Step 0" and "Cheap first experiments" in CURSOR-SETUP.md on my machine. Then list every Claude session running now: its conversation id, folder, name, how it was started, and which Cursor window it's in. You're one of them, so include yourself.

5. Ground rules while you explore:
   - Look only. Don't install anything from the repository: no berth install, no watcher, no MCP server registration, and no changes to my Cursor or Claude settings.
   - Don't stop, restart, resume, or type into any of my other sessions, and don't quit Cursor. If an experiment needs that, describe it and ask me first.
   - The files in ~/.claude/ide/ contain a login token. Read the other fields only; never print, copy, or save the token.
   - Don't copy what my conversations say into your notes. Ids, names, and folders are enough.

6. Write up what you found in MY-SETUP.md at the repository's top level (git ignores it):
   - my setup, with the full session list;
   - how Berth's ideas map onto it: what carries over, what doesn't, and which habits in TIPS.md could work in Cursor;
   - two or three ways turning everything off and back on could work for me, with the tradeoffs of each;
   - every question from CURSOR-SETUP.md you couldn't answer just by looking.

Don't start building anything yet. When you're done, give me a short summary here and the list of questions you need me to answer.
```

### Update

1. If your kit is a clone, open its folder. If it's a zip from before, clone the repository next to it: `git clone https://github.com/mit-bach/berth-kit`, and open the new `berth-kit` folder.
2. Start your agent there, the way you did the first time (Claude Code in cmux or your terminal, or a Claude chat in Cursor).
3. Send it this:

```text
I have Dominik's Berth kit, and there's a newer version. Bring me up to date.

1. If this folder is a clone of his repository, run `git pull`. Then run `git describe --tags`, and tell me which version I was on and which I have now. If my old kit was a zip, find it (~/berth-onboarding, ~/berth-cmux-onboarding, ~/berth-cursor-handoff, ~/berth-handoff) and tell me which version it was.

2. Read CHANGELOG.md from my old version to this one, and tell me in plain words what changed for me.

3. If Berth is installed on this Mac (`berth status` answers), it runs from the folder it was installed from. Show me each installer stage I used, with --dry-run first (tools/install.sh <stage> --dry-run), then run it for real once I say yes. If my install came from an old zip folder, that points it here. Then restart what has the old code loaded, as docs/TROUBLESHOOTING.md says under "After changing code, a skill, or an MCP server", and run python3 tools/doctor.py.

4. If I'm on the Cursor track and never installed anything, read onboarding/cursor/START-HERE.md from "If you read an earlier version", and update my MY-SETUP.md.

Ask me before you change anything on my Mac, and don't close or type into any of my other sessions.
```

## Once it runs

Read `TIPS.md`. It's the one document written for you rather than your agent: the shortcuts and habits that make Berth fast, which you wouldn't find on your own.

## What's in here

| Path | What it is |
| --- | --- |
| `onboarding/terminal/` | The terminal track: `START-HERE.md`, the stages (0 to 4), and `KICKOFF.md` (the message for every next chat). |
| `onboarding/cmux/` | The cmux track: `START-HERE.md` and `FIRST-DAY.md`. |
| `onboarding/cursor/` | The Cursor track: `START-HERE.md`, `CURSOR-SETUP.md`, `MIGRATION.md`. |
| `docs/` | Why Berth exists, its architecture, how Dominik works with it, the day-one decisions, the invariants, troubleshooting, uninstalling, history, open items. |
| `tools/` | `install.sh` (in stages), `doctor.py` (checks each stage), `cmux_action.py`, `inventory.py` (a read-only survey), `migrate.py` (moves running conversations into cmux). |
| `packages/berth/` | Berth: code, tests, `README.md` (the full manual), `skill/` (the `berth` skill). |
| `packages/cmux-mcp/` | The cmux-mcp server. |
| `skills/cmux-claude/`, `skills/cmux-grok/` | The skills that start and drive Claude and Grok sessions. |
| `grok/hooks/` | A fallback copy of Grok's cmux hook. |
| `TIPS.md`, `CHANGELOG.md`, `CONTRIBUTING.md` | For people. |

## Repositories this uses

| Repository | What it's for | Install from it, or read only |
| --- | --- | --- |
| [mit-bach/berth-kit](https://github.com/mit-bach/berth-kit) | This kit: Berth, cmux-mcp, the skills, the installer, and the onboarding | Install: everything runs from your clone |
| [cmux](https://cmux.com) | The terminal app Berth runs on. Not ours | Install: you download the app from cmux.com |
| [astral-sh/uv](https://github.com/astral-sh/uv) | Makes Berth's Python environment, and fetches Python 3.12 for it | Install, only if you have neither uv nor Python 3.12: Homebrew (`brew install uv`) or astral.sh's installer |
| [pytest-dev/pytest](https://github.com/pytest-dev/pytest), [selectel/pyte](https://github.com/selectel/pyte) | Berth's tests (pyte reads the screen back in them) | Installed for you from PyPI into Berth's environment |
| [pypa/setuptools](https://github.com/pypa/setuptools) | Builds Berth's package while it installs | Installed for you from PyPI during the install |
| [anthropics/claude-code](https://github.com/anthropics/claude-code) | Claude Code, the agent the onboarding runs in. Not part of the kit | Yours already; read only here |

Berth itself needs nothing beyond Python's standard library. Grok Build is optional: the installer's `grok` stage configures it if you have it, and installs nothing for it.

## Versions

Every version is a git tag (`git tag`), and `CHANGELOG.md` says what each one changed. `git pull` brings the newest. Since everything installed runs from this folder, follow a pull with the Update prompt above.

This version was tested on macOS 27, Python 3.12, cmux 0.65.2 (0.65 or newer is required), and Claude Code 2.1.31x. Tests: Berth 601 passed, 3 failed; cmux-mcp 55 passed.

## Contributing

Fork it and send pull requests: `CONTRIBUTING.md`.
