# cmux-mcp

MCP server for Berth and the cmux that is already running. Agents see Berth rows, not cmux workspaces or groups: every tool takes a Berth id, and every change goes through the same code as the Berth screen and the `berth` CLI. The `cmux-claude` and `cmux-grok` skills teach these tools, in one shared block (`tests/test_skills.py` keeps it the same in both and in step with this list).

Start it with:

```text
python3 -m cmux_mcp
```

`BERTH_ROOT` points at Berth, which lives in `repo/packages/berth` since 2026-10-28 (that path, then a sibling `berth` checkout, are the fallbacks). The catalog is Berth's default. The socket is `CMUX_SOCKET_PATH`, then `~/.local/state/cmux/last-socket-path`, then the uid socket under `~/.local/state/cmux`.

## Tools

| Tool | What it does | Berth screen |
| --- | --- | --- |
| `status` | cmux up or down, counts (live, parked, auto-parked, playing, pausing), unfiled workspaces, the watcher, and the last invariant check (`drift`). | header |
| `check` | The invariant against a fresh workspace list, as `berth check`. Read-only. | |
| `folders` | Every folder path (`Parent/Child`) with live and parked counts. | |
| `list` | One page of rows, newest use first (`sort: name` for the old order). Filters: `folder` (+ `subfolders`), `state`, `kind`, `agent`, `park_reason`. | `f` filters, `s` sort |
| `get` | One row in full: `agent`, `session`, `park_reason`, `last_used`, `fork_of`, `recipe`, `workspace`. | |
| `mkdir` | Make a folder; a slash path makes every missing level. Inbox holds no folders. | `n` |
| `rmdir` | Delete a folder; its sessions move up a level. Nothing closes. | `d` on a folder |
| `move_folder` | Put a folder, with everything in it, inside another, or at the top level. | `m` on a folder |
| `rename_folder` | Rename a folder in place. | `r` on a folder |
| `move` | File one session in a folder. | `m` |
| `rename` | One name for the row, the sidebar, and the agent's session (`/rename` typed by Berth). | `r` |
| `create` | Open a new workspace with a command, filed in a folder. | |
| `play` | Reopen a parked session and resume it; focus a live one. | Enter |
| `fork` | Fork a Claude or Grok session into a new workspace next to it, under a session id Berth chooses. | `F` |
| `pause` | Park one session: save the recipe and the screen, close the workspace. | `p` |
| `cycle` | Turn a live session off and on again: pause, then play the same session in a new workspace. A process needs `confirm`. | `c` |
| `sleep` | Put one session, a folder's live sessions, or all of them (not the Berth screen) to sleep: closed, to come back with `wake`. | `S`, `Z` |
| `wake` | Open one sleeping session, or all of them, again. | Enter, `W` |
| `park_folder` | Park every live session in a folder and its subfolders. Without `confirm`, only the plan. | `p` on a folder |
| `restore` | Reopen what the last cmux quit or restart parked. | auto-parked filter |
| `delete` | Move a row to the trash for 30 days (closes a live workspace). Needs `confirm`. | `d` |
| `trash` | The sessions deleted in the last 30 days. | ctrl-t |
| `untrash` | Bring one back from the trash, parked, into Inbox. | Enter in the trash |
| `focus` | Select a live workspace. | |
| `screen` | The last visible lines of one live pane (at most 40). Not scrollback, not a screenshot. | |
| `input` | Write a live pane's prompt or terminal: a short line, a file, or one key. | |
| `excerpt` | The text saved at the last pause. | |
| `peers`, `offer`, `preview`, `merge` | Other Macs on the tailnet: see and copy parked folders. | |

The Berth screen, and the caller's own workspace, are never paused, parked, or deleted through these tools.

## File placement

`input` is one prompt-box operation with two ways to supply the bytes.

- `text` is one short line or a slash command, at most 200 UTF-8 bytes, with no newline.
- `path` is an absolute file path. The server reads the file and places those bytes in the prompt box.

Pass `target` `prompt`. Do not pass `submit` with `path`. File placement does not press Enter. The tool result reports the path, the byte count, and a sha256. It does not repeat the file body.

A longer prompt belongs in the file. A tool argument that contains the whole prompt is rejected when it is longer than one short line.

`key` sends one key (`enter`, `escape`, `backspace`, `ctrl-c`, and the other names cmux accepts). `submit: true` presses Enter after `text` only.

`target` `terminal` types into the terminal. `path` is only for the prompt box.

Name the row's `id` on every call that changes a pane. The server does not fall back to the caller's current workspace for input or close.

## What this server does not copy

From cmux-agent-mcp: the 82-tool list, a tool per CLI, swarm and broadcast helpers, browser controls, sidebar pills, and sending the prompt body as a tool argument.

From cmuxlayer: `spawn_agent`, `list_agents`, `report_to_parent`, `wait_for`, the agent fleet, the launcher registry, the brew wizard, and screen parsers for Claude, Codex, Gemini, Kiro, or Cursor.

There is no product launcher. A Grok or Claude pane is `create` with the agent's command string, from the skill.

The clones under `reference/` are not part of this server. Git ignores that directory.
