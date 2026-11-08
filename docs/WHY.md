# Why Berth exists

## The problem

Claude Code in a terminal tab is one conversation in one tab. That works for three conversations. At twenty it breaks, in ways anyone with a pile of tabs knows:

- **An update loses the desk.** A macOS or terminal update, or a crash, closes every tab. The conversations are still on disk, but which tab held which, in which directory, under which id? Bringing twenty back means finding twenty session ids by hand, so most never come back.
- **Sessions get lost.** Which tab was the one fixing the export? Tabs have no folders, and the name in the tab, the name Claude shows, and the name in your head drift apart.
- **Idle sessions cost memory and attention.** Thirty live agents are several GB of RAM and thirty things to look at. Closing the idle ones is only safe if you can bring each back exactly.
- **You cannot see who needs you.** A finished turn, a permission prompt, and a running job look alike from the tab bar.
- **Agents cannot run agents.** An agent that wants to start a helper on a job, watch it, and fork it for a second attempt needs a stable handle on sessions, not "the third tab".

## The one idea

**A session is a conversation, not a pane.** The pane is disposable. What must survive is the conversation's id, the directory it runs in, and the command that resumes it. Berth calls that the recipe, and keeps one catalog row per session.

Everything follows from it:

- **Sleep** (or **park**, "put away"): save the recipe, the last screen, and anything typed but not sent; close the pane. The row stays.
- **Wake**: open a new pane that runs the recipe; Claude resumes the same conversation, in the permission mode its status strip showed (manual mode shows none; see `DECISIONS.md`), with the unsent draft pasted back.
- **Cycle**: sleep then wake, for one session or a whole folder: how a session picks up a new MCP server, skill, or setting.
- **Restore**: when the terminal app quits or crashes, every live row is parked as auto-parked; when it comes back, every one is woken. Nobody reopens sessions by hand.
- **Fork**: a new conversation copied from another, opened next to it, under an id Berth picked so the row knows it from the first second.
- **Folders** file rows, nested, independent of directories. **Names** are one string everywhere: Berth, the sidebar, and the agent's own name.

## Why cmux

Berth needs a terminal that other programs can drive: list its panes with stable ids, open one in a directory running a command, close one, read what it shows, type into it, and say which agent session each pane holds. cmux does all of that through a socket and a CLI, runs Claude through a wrapper whose hooks report the session, and lets Berth replace its sidebar. A plain terminal does none of it, which is why stage 1 moves you there.

## Why it is strict

Every rule in `INVARIANTS.md` exists because breaking it lost work. The worst case: Berth once took a Mac waking from sleep for a terminal restart and closed every pane, seven times in one day, killing agents mid-turn. Since then only a different terminal-app process counts as a restart, and a pane running an agent is never closed unless a person asked for that session to close. The catalog must never lie about what is open, because people and agents trust it to say what is safe to close.
