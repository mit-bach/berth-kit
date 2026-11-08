# First day with Berth

A walkthrough to do with your operator, after the install and the checks in `START-HERE.md` sections 5 and 6. Let them press the keys. Each step says what to do, what should happen, and what it shows. The full manual is `packages/berth/README.md`; `?` in the screen lists every key.

## 1. Open the screen

⌘⇧B switches to the workspace named Berth, running the Berth screen (it starts one if none is open). The tree shows folders and sessions, last used first. Inbox holds sessions in no folder. The top line counts live, sleeping, and parked sessions and shows whether the watcher is up.

Keys to start with: `j` `k` (or arrows) move, `h` `l` fold, Enter wakes a closed session or focuses a live one, `S` sleep, `c` cycle, `F` fork, `n` new folder, `m` move, `t` rename a session, `r` rename a folder, `v` mark, `P` pin, `d` delete, ctrl-t the trash, `f` filter, `s` sort, `/` search, space shows a session's saved screen, `?` help, `q` quits (⌘⇧B brings it back). Text boxes edit like a prompt box: readline keys, ⌘← ⌘→ ⌥← ⌥→, shift or drag to select, ⌘V and dictation paste whole, ctrl-z undo.

From any session, ⌘⇧B brings them back to the screen with the cursor on that session's row.

## 2. Make folders

Put the cursor on Inbox and press `n`, type `Work`, Enter. Then with the cursor on `Work`, `n`, type `Clients`, Enter: `Work/Clients` is a subfolder. `n` makes a folder inside the one under the cursor; `a/b` makes both levels at once; a leading `/` makes a top-level folder from anywhere. From a shell: `berth mkdir "Work/Clients"`.

Colors appear on their own: every folder gets an equal share of the color wheel, subfolders sit next to their parent's color, and each workspace in cmux's sidebar takes its folder's color.

To move a folder: cursor on it, `m` picks it up, arrow to the new parent, `m` drops it there.

## 3. Start a session and file it

- **By hand:** a new cmux workspace, `cd` to a project, `claude` (or `grok`), one message. Within a few seconds the row appears in Inbox. Berth learns the conversation from the agent's own records and its name from the agent's own title.
- **File it:** cursor on the row, `v` to mark, arrow to `Work/Clients`, `m`. **Name it:** `t`, type `Client A`. The name changes everywhere: Berth, cmux's sidebar, and the agent's own name.
- **Pin it** if it matters: `P`. A pinned session stays in its place and comes first in cmux's sidebar.

## 4. Sleep, wake, cycle

Use the session from step 3, not a real one.

- Type something in its prompt box and do not send it. Then, on its row, `S`: the pane closes and the row says `sleeping`. Space shows the screen Berth saved.
- Enter on the sleeping row: a new workspace opens in the same directory, the same conversation continues, and the unsent draft is pasted back.
- `c` on a live row: cycle, off and on again in one step, without taking focus. Agents use it to load new tools or settings.
- A busy session (mid-turn, or asking a question) first shows why it is busy and waits for `y`.

## 5. Fork

On a session with at least one turn, `F`. A copy opens next to it, titled `Client A ⑂`, in the same folder, with the whole conversation under a new session id. The original keeps running. Never type `/fork` into a pane; Berth's fork is the one that gets a pane and a row.

## 6. The trash

`d` on the fork, then `y`: it goes to the trash. ctrl-t opens the trash (30 days); Enter brings it back, closed, into Inbox. `D` in the trash deletes for good.

## 7. The update routine

Before a macOS or cmux update: `Z` in the screen (every session except the screen goes to sleep, each with its screen and draft saved), update, then `W` (every sleeping session wakes in its own workspace, in its folder). If cmux quits or crashes instead, Berth wakes what was open by itself when cmux is back. A Mac going to sleep is not a restart: nothing closes.

If they want to see the crash case: with sessions idle, quit cmux (⌘Q) and open it again.

## 8. Let agents do the work

Ask an agent, in plain words, and it uses the `berth` MCP and the skills:

- "Start a Claude session in `~/some/project`, in the Berth folder Work/Clients, titled Client B, and have it summarize the README into a file." The agent starts it, clears the folder-trust question if asked, places the job from a file, and watches for the file it writes when done. Any permission prompt in that session is the operator's to answer.
- "Fork Client B." "Put everything in Work to sleep." "Cycle every idle session in my folder." Actions on a whole folder show what they would do first.
- "Mark this urgent if you need me." The session then shows as urgent in cmux's sidebar.

## 9. Hand over the tips

Tell your operator about `TIPS.md` at the top of the kit, and open it with them. It is written for them, not for you: the ⌘⇧B loop, drafts that survive, cycling, the update routine, and how to read the sidebar.

## 10. When something looks off

`berth check` compares the catalog with cmux. `berth status` shows the watcher. `docs/TROUBLESHOOTING.md` has the common cases. Report differences rather than editing the catalog by hand.
