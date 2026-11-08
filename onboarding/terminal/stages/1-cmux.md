# Stage 1: cmux

**Goal.** cmux is installed and set up the way Berth needs, and your operator has used it once.

**Runs in:** the same chat as stage 0, in their old terminal.

cmux is a macOS terminal app made for agents: each workspace is a terminal (or split panes) with a stable id, and cmux has a socket and a CLI (`cmux`) that other programs, Berth included, use to list, create, close, rename, color, and read workspaces. Inside cmux, `claude` runs through cmux's Claude wrapper, whose hooks tell cmux (and so Berth) which conversation each pane holds.

## Steps

1. **Prerequisites.** Berth's virtualenv needs **uv** (it also fetches Python 3.12 by itself) or Python 3.12. If the survey found neither, install uv on their yes: `brew install uv` when they have Homebrew, else Astral's installer (`curl -LsSf https://astral.sh/uv/install.sh | sh`), which installs to `~/.local/bin` and adds that directory to their shell profile. Say so before running it.

2. **Install cmux.** If the survey said it is missing: get it from https://cmux.com (or `brew info --cask cmux`; use the cask if it exists). It must go in **`/Applications`**: Berth starts Claude through cmux's wrapper at `/Applications/cmux.app/...`. Have the operator open it once so macOS approves it. This kit needs **cmux 0.65 or newer** and was tested on 0.65.2.

3. **cmux's settings for Berth.** Explain the socket decision (`docs/DECISIONS.md`, "cmux socket access") and get their yes, then show and run:

   ```bash
   <kit>/tools/install.sh cmux --dry-run
   <kit>/tools/install.sh cmux
   ```

   It sets these with `cmux config set`, which validates each and keeps every comment in `~/.config/cmux/cmux.json`:

   - `automation.socketControlMode = automation`. By default cmux answers only programs started inside its own terminals. Berth's watcher runs in the background as a LaunchAgent, so without this it cannot see cmux at all: nothing is cataloged, parked, or restored, while the screen and the MCP (started inside cmux) seem to work. With it, any program running as this user may control cmux.
   - `terminal.autoResumeAgentSessions = false`, turning off cmux's own agent resume. When cmux restarts it restores its workspaces, and would restart each agent itself, using the session id from when the agent first started, which is wrong after a fork, `/resume`, or `/clear`. Berth does the resuming instead, from Claude's own records.
   - Links open in the system's default browser instead of cmux's built-in one: links clicked in a terminal, `open <url>` run in one, and the sidebar's pull request and port links (four `browser.*` and `sidebar.*` settings, all `false`). New users find cmux's in-terminal browser confusing. If they want cmux's browser, add `--cmux-browser` (`docs/DECISIONS.md`, "Where links open").

4. **Try it.** Have the operator open a new workspace in cmux, `cd` into a scratch directory, run `claude`, and send one message. Point out: the sidebar row for the workspace, the workspace's title, and that `claude` here is cmux's (`which claude` inside cmux shows a shim under `~/.cmuxterm/cmux-cli-shims/`, which starts Claude with cmux's hooks). Then close that workspace.

5. **Check.** `python3 <kit>/tools/doctor.py --stage 1`. Every line ok. Run here, outside cmux, "cmux is running and answers" passes only once the socket setting is in.

6. **Update PROGRESS.md**: `Stage 1: done`, the cmux version, anything notable (an old `cmux.json` they had, a tmux habit).

## Ends with

Stage 2 must run **inside cmux**, so Berth can see the chat you are in, and later restart it. Tell the operator:

1. Leave this chat open for now (its conversation moves into cmux in stage 2 too, if they want).
2. In cmux, open a new workspace, `cd` to `<kit>`, and run `claude`.
3. Paste the "Every next chat" message from `onboarding/terminal/KICKOFF.md`, filled in with the kit's path.

Give them that message, filled in, as the last thing you say.
