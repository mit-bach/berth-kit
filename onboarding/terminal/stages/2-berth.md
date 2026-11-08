# Stage 2: Berth, and moving the old tabs in

**Goal.** Berth runs: the watcher catalogs every cmux workspace, the screen opens with ⌘⇧B, the sidebar is Berth's. Your operator's folders exist, the conversations they wanted to keep run in cmux and are filed, and they have put a session to sleep and woken it.

**Runs in:** chat 2, inside cmux. First read `~/.berth-onboarding/PROGRESS.md` and `DECISIONS.md`.

Read `docs/ARCHITECTURE.md` before you start, if you have not, and skim `packages/berth/README.md` (the manual). You will teach from both.

## Steps

### Install Berth

1. Show the dry run, say what it does, and on a yes install:

   ```bash
   <kit>/tools/install.sh berth --dry-run
   <kit>/tools/install.sh berth           # add --no-sidebar if they chose to keep cmux's own sidebar
   ```

   It makes Berth's virtualenv in `<kit>/packages/berth/.venv` (with `uv` when installed, else Python 3.12), links `~/.local/bin/berth`, installs the watcher (the LaunchAgent `com.bach.berth.watch`, logs and catalog in `~/.local/share/berth/`), unbinds cmux's own ⌘⇧B (Jump to Last Prompt, which stays in cmux's command palette) and adds an action that makes ⌘⇧B open the Berth screen, and runs `berth sidebar install` (Berth's sidebar and notification wording, after backing up `cmux.json`).

2. Check: `python3 <kit>/tools/doctor.py --stage 2`. Then `berth status`: the watcher is `up`. Within a few seconds the watcher files every open cmux workspace, this chat's included, in **Inbox**. If one is missing, `berth adopt` files every open workspace at once; it closes nothing.

3. Press ⌘⇧B (have the operator do it). A workspace named Berth opens running the screen, with this chat's row under the cursor. `?` lists every key; `q` quits the screen (⌘⇧B brings it back).

### Their folders

4. Create the folders from `PROGRESS.md`. From a shell: `berth mkdir "Work/Client A"` (it makes both levels). In the screen, `n` makes a folder inside the folder under the cursor (on Inbox, at the top level); a leading `/` makes it top-level from anywhere: `/Work/Client A`. Colors appear by themselves: each folder gets an equal share of the color wheel, subfolders sit next to their parent's color, and every workspace in the cmux sidebar takes its folder's color.

5. File this chat: in the screen, cursor on its row, `v` to mark, arrow to the folder, `m` to drop it there. Name it: `t` renames it everywhere (Berth, the sidebar, and Claude's own `/rename`).

### Move the old tabs in

6. Make the plan from what is running now:

   ```bash
   python3 <kit>/tools/migrate.py plan --out ~/.berth-onboarding/migration.json
   ```

   It lists every Claude conversation running outside cmux, with its directory and the terminal app and tty it runs in. Add `--include-recent` to also list recent conversations that are not running (marked `"move": false`; set `true` for any they want back). Edit the file with the operator: `"move": false` for the ones they leave behind, a Berth `"folder"` for each one that moves (from `PROGRESS.md`), and `"flags"` only if they want a different permission mode for it (for example `--permission-mode plan`).

7. **The operator exits each conversation in its old tab** (type `/exit`). A conversation must never run in two places; the script refuses one that is still running and says which terminal it is in. If a tab is mid-turn, let it finish first.

8. Dry run, then apply:

   ```bash
   python3 <kit>/tools/migrate.py apply ~/.berth-onboarding/migration.json --dry-run
   python3 <kit>/tools/migrate.py apply ~/.berth-onboarding/migration.json
   python3 <kit>/tools/migrate.py status ~/.berth-onboarding/migration.json
   ```

   For each one, the script opens a cmux workspace in the conversation's directory running Claude through cmux's wrapper with `-r <session id>`: the same conversation, now in cmux. Berth's watcher files it; the script then moves it into its folder. A conversation Berth already holds is opened from its row instead (woken if sleeping, played if parked), and each result is checked. `apply` refuses to run while a Claude process outside cmux has left no session record; end or restart it first. Workspaces open without taking focus, one every couple of seconds.

9. Look at the result together: the screen, and cmux's sidebar (one row per session, its word, its folder's color). Old terminal tabs that are now empty can be closed.

### Prove sleep, wake, cycle

10. Make a throwaway session for the drills: a new cmux workspace in a scratch directory, `claude`, one message ("hi"). Have the operator do each once, on that session, never on a real one:
    - Enter on a live row: focus it. ⌘⇧B: back to the screen.
    - `S`: sleep. The pane closes; the row says sleeping; space shows the saved screen. Type something in a session's prompt box first and do not send it: that draft is saved and pasted back on wake.
    - Enter on the sleeping row: wake. A new workspace resumes the same conversation.
    - `c`: cycle (off and on, same conversation).
    - `F`: fork. A copy opens next to it, titled with ⑂.
    - `d`, then `y` to confirm: it goes to the trash. ctrl-t opens the trash; Enter brings it back (parked, into Inbox).
11. Tell the operator about `TIPS.md` at the top of the kit, the one document written for them, and show them its first section (the ⌘⇧B loop) now: they will use it all day.
12. Explain the update routine: before a macOS or cmux update, `Z` in the screen (sleep every session except the screen), update, then `W` (wake every sleeping session). If cmux quits or crashes instead, Berth wakes what was open by itself when cmux is back. Do not try a real cmux restart now unless the operator wants to; it is part of stage 4.

### Install the agent tools, then restart this chat

13. Show and run the next stage's install now, so the next chat has the tools:

    ```bash
    <kit>/tools/install.sh agents --dry-run
    <kit>/tools/install.sh agents
    ```

    It registers two MCP servers for Claude Code (user scope): `berth` (`~/.local/bin/berth mcp`) and `cmux-mcp` (Berth's Python, `PYTHONPATH` at `<kit>/packages/cmux-mcp`, `BERTH_ROOT` at `<kit>/packages/berth`), and links the `berth` and `cmux-claude` skills into `~/.claude/skills`. `python3 <kit>/tools/doctor.py --stage 3` checks it.

14. **Update PROGRESS.md**: `Stage 2: done`, the folders made, what moved (from `migrate.py status`), anything that did not and why.

## Ends with

A Claude session loads MCP servers and skills when it starts, so this chat cannot use them yet. Restart it on the same conversation, which is also the first thing the operator sees Berth do for an agent:

- Find this chat's Berth id: `berth sessions --json` with no arguments lists your own folder, and the row marked `"you": true` is this chat. (A title query can match the old chat you moved, or a fork.)
- Tell the operator what will happen, and that once this chat reopens they should send it one message ("continue"): a reopened chat waits for its next prompt. Then run `berth cycle <that id>` and **end your turn**. Your own pane cannot close itself while you are working, so Berth's watcher queues it and cycles this session as soon as your turn ends: the pane closes and reopens on the same conversation, now with the `berth` and `cmux-mcp` tools.
- If that does not happen within a minute, the operator can press `c` on this row in the Berth screen, or start a new chat in cmux with the "Every next chat" message.

In the restarted chat, continue with `onboarding/terminal/stages/3-agents.md`.
