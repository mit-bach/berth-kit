# Berth tips

These are the tricks that make Berth fast, and that you wouldn't find on your own. Your agent covers the basics during setup. `?` in the Berth screen lists every key.

## The one habit: ⌘⇧B, one key, Enter

**Press ⌘⇧B from inside any session and the Berth screen opens with the cursor already on that session**, its folder unfolded. Press one key, then Enter to go straight back:

| You want to | Keys |
| --- | --- |
| rename it | ⌘⇧B `t` |
| file it in a folder | ⌘⇧B `v`, arrow to the folder, `m` |
| put it away for today | ⌘⇧B `S` |
| restart it | ⌘⇧B `c` |
| try another approach from here | ⌘⇧B `F` |
| keep it at the top of the sidebar | ⌘⇧B `P` |

Most of how Dominik uses Berth is that loop. Clicking **Berth** in cmux's sidebar lands on the session the same way.

In the screen, **Enter on any row takes you to it.** On a live session it switches there. On a closed one it reopens it in its own workspace, on the same conversation. You never have to remember where something is open.

## Closing never loses anything

- **Sleep freely.** `S` closes the pane and keeps the conversation; Enter brings it back where it was. Thirty open sessions are noise, so put away what you aren't using today.
- **A half-typed message survives.** Anything typed in a session's prompt box and not sent is saved when it sleeps or restarts, and put back (unsent) when it reopens.
- **Space shows a closed session's last screen** without waking it. Use it to check what it was doing before you bring it back.
- **Closing a workspace in cmux (⌘W) doesn't delete the session.** Berth keeps it as parked; Enter reopens it.
- **Even `d` can be undone for 30 days.** ctrl-t opens the trash; Enter restores.
- **Protected rows still go when you mean it.** `d` on the Berth screen, or on the workspace you're in, says why it's protected; `!` deletes it anyway.
- **There is one Berth screen.** Typing `berth` anywhere takes you to the open one, like ⌘⇧B.
- **Busy sessions ask first.** If a session is mid-turn, asking a question, or still running background shells, Berth shows you why and waits for `y`.

## `c` fixes most things

`c` turns a session off and on again, on the same conversation. Use it:

- after adding an MCP server or a skill, or changing Claude's settings. A running session loads those only when it starts;
- when a session's terminal looks wrong or stuck.

**A session can restart itself.** Tell it "cycle yourself to load the new tools". It waits until its turn ends. When it comes back it is waiting for you, so send "continue".

**Restart everything idle at once**: mark sessions with `v` and press `c`, or ask any agent to "cycle every idle session". It shows you the list first.

A restart keeps the permission mode the session showed on its status line. The exception is manual mode (ask every time), which shows nothing there, so the session comes back in the mode it was first started with. Check the status line after cycling a manual-mode session.

## Updates and crashes

- **Before a macOS or cmux update:** `Z` in the screen (everything sleeps, each session with its screen and draft saved). Update and reopen cmux, then ⌘⇧B and `W` (everything wakes, each in its own folder).
- **If cmux crashes, or you quit it by accident:** just open it again. Berth reopens everything that was open, one at a time, without stealing focus.
- **Closing the laptop lid is not a restart.** Nothing closes.
- **After a cmux update,** run `berth sidebar install` once, in case the update reset its settings.

## Forks

- **`F` copies a conversation into a new session next to it**, titled with ⑂. Use it to try two approaches from the same point, or to chase a side question without derailing the main thread. The original keeps going.
- **Don't type `/fork` inside Claude.** That makes a hidden background copy with no window. Berth's fork is the one that gets a window and a place in your folders.
- **An agent can fork into another project:** "fork this session into ~/other-repo" (Claude only).

## Names, folders, colors, order

- **Rename anywhere and the rest follow.** `t` in the screen, Claude's own `/rename`, or the cmux tab title: Berth, the sidebar, and Claude's name all match.
- **A folder is a label, not a directory.** Filing a session never changes where it runs. To move a session to a different project directory, ask an agent: "move this session's working directory to ~/new-place". It keeps the conversation and opens there the next time it wakes or restarts (Claude only).
- **Colors come from folders.** Every session in a folder shares its color, and subfolders get nearby shades. You never pick colors; you make folders.
- **Pins (`P`) stay at the top of cmux's sidebar**, even after a session closes and reopens. Pin the few you use every day. Right-click in the sidebar pins too.
- **The sidebar follows the screen:** most recently used first, grouped by folder. If folders jumping around bothers you, `o` switches the sidebar to fixed folder-by-folder groups. Dragging rows in the sidebar doesn't stick.
- **Folder shortcuts:** in `n`, `Work/Client A` makes both levels, and a leading `/` makes a top-level folder from anywhere. `m` on a folder, with nothing marked, picks up the folder itself to move it.
- **Marks multiply every key.** With several sessions marked (`v`), `m` files them all, Enter opens them all, `c` restarts them all, and `S` sleeps them all. `S` on a folder sleeps everything in it.
- **Finding things:** `/` searches names and folder paths. `f` steps through filters (live, sleeping, parked), which is the quickest way to find something you put to sleep last week. `s` sorts by name instead of last used.

## Read the sidebar at a glance

Each row has one word. Look for amber and red; ignore the rest.

| Looks like | Means |
| --- | --- |
| amber: Question, Permission, Plan to approve, Trust folder? | it is stopped until you answer |
| red, at the top: Urgent | an agent says it needs you now |
| green, filled: Done | it finished and you haven't looked |
| green ring: Ready (Ready · draft) | idle, waiting for you (you left something typed) |
| blue, blinking: Working | mid-turn |
| purple: Waiting on 2 subagents, Waiting on 1 shell | its turn ended but work it started is still running; it carries on by itself |

The line at the top counts them. Notifications use the session's own name and say Done or Needs you.

## Let agents run agents

Talk to any session in plain words about your other sessions. It uses Berth for you:

- "Start a Claude in ~/project, in the Work folder, titled API review, and have it review the open branch."
- "What's running in my folder?" "Wake Client A." "Put everything in Work to sleep." "Fork the API session."
- Agents know where they're filed, so "my folder", "my siblings", and "the parent folder" work.
- **Agents look before they prompt.** An agent sending another session a prompt sees what it's doing first. If it's busy, it waits until it's free, queues the prompt with a note to finish first, or interrupts, and it never types into a question that's yours to answer or over something you're typing.
- **Compaction that works:** "compact the API session and keep the open questions", or "fork it and compact the fork". Berth types `/compact` with the note and checks Claude really did it, which a pasted `/compact` never does.
- **Anything that touches many sessions shows you a plan first**, and nothing happens until you say go.
- **Permission prompts in sessions an agent started are yours.** When a row turns amber, click it and answer.
- **Set up an orchestrator** for recurring multi-agent work: one session whose job is starting workers, checking their results, and marking itself Urgent only when it needs you. Tell it in its instructions when urgent is allowed.

## From any terminal

The same things work as commands, which is handy in scripts:

- `berth sleep <name>`, `berth wake <name>`, `berth wake --all`, `berth cycle <name>`
- `berth sessions --query <words>`
- `berth check` says whether Berth and cmux agree.

A name can be part of a title, as long as only one session matches.

## When something looks wrong

Run `berth check`, then `python3 <kit>/tools/doctor.py`. Each problem comes with what to do. For anything else, ask your agent to read `docs/TROUBLESHOOTING.md` in the kit.
