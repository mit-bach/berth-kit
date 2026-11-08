# What the kit installs, and how to undo it

Everything below is outside the kit's own directory. Undo it **top to bottom**: the agents' pieces first, then the pieces that need the `berth` command (the sidebar), then the watcher, the command, and cmux's settings last. Each file the installer replaced is next to the original as `<name>.bak-berth-<time>`; `berth sidebar install` keeps its own `cmux.json.bak-<time>`.

| # | What | Where | Installed by | Undo |
| --- | --- | --- | --- | --- |
| 1 | The Berth MCP for Claude Code | `~/.claude.json` → `mcpServers.berth` | `install.sh agents` | `claude mcp remove berth -s user` |
| 2 | cmux-mcp for Claude Code | `~/.claude.json` → `mcpServers.cmux-mcp` | `install.sh agents` | `claude mcp remove cmux-mcp -s user` |
| 3 | The `berth` and `cmux-claude` skills | `~/.claude/skills/berth`, `~/.claude/skills/cmux-claude` (links) | `install.sh agents` | `rm` both links; move back any `.bak-berth-*` |
| 4 | Grok: skills, hook, MCP tables | `~/.grok/skills/{berth,cmux-grok}` (links), `~/.grok/hooks/cmux-session.json`, `~/.grok/config.toml` → `[mcp_servers.berth]`, `[mcp_servers.cmux-mcp]` | `install.sh grok` | `rm` the links; `cmux hooks grok uninstall --yes` (or delete the hook file the kit wrote); restore `config.toml.bak-berth-*` or delete the tables |
| 5 | Berth's sidebar, quieter cmux rows, reworded notifications | `~/.config/cmux/sidebars/berth.js` (link), sidebar and notification keys in `cmux.json` | `install.sh berth` (`berth sidebar install`) | `berth sidebar uninstall` (while `berth` still exists) |
| 6 | ⌘⇧B opens the Berth screen | `cmux.json` → `actions.berth`; `shortcuts.bindings.jumpToLastPrompt` | `install.sh berth` (`tools/cmux_action.py`, `cmux config set`) | delete the `berth` entry under `actions` in `cmux.json` (or restore `cmux.json.bak-berth-*`); `cmux config unset shortcuts.bindings.jumpToLastPrompt` |
| 7 | The watcher | `~/Library/LaunchAgents/com.bach.berth.watch.plist` | `install.sh berth` (`berth install`) | `launchctl bootout gui/$(id -u)/com.bach.berth.watch`, then delete the file |
| 8 | The `berth` command | `~/.local/bin/berth` → `<kit>/packages/berth/.venv/bin/berth` | `install.sh berth` | `rm ~/.local/bin/berth` |
| 9 | Berth's data: the catalog, backups, logs | `~/.local/share/berth/` | the watcher | delete the folder once the watcher is stopped (this deletes the catalog) |
| 10 | cmux lets background programs connect | `cmux.json` → `automation.socketControlMode` | `install.sh cmux` | `cmux config unset automation.socketControlMode` (back to cmux's default, `cmuxOnly`) |
| 11 | cmux's own agent resume turned off | `cmux.json` → `terminal.autoResumeAgentSessions` | `install.sh cmux` | `cmux config unset terminal.autoResumeAgentSessions` |
| 11b | Links open in the system browser | `cmux.json` → `browser.openTerminalLinksInCmuxBrowser`, `browser.interceptTerminalOpenCommandInCmuxBrowser`, `sidebar.openPullRequestLinksInCmuxBrowser`, `sidebar.openPortLinksInCmuxBrowser` | `install.sh cmux` | `cmux config unset` each of the four (back to cmux's built-in browser) |
| 12 | The onboarding's notes | `~/.berth-onboarding/` | the onboarding agent | delete the folder |

uv and cmux, if the onboarding installed them, stay; remove them the way they were installed (`brew uninstall uv`, or `rm ~/.local/bin/uv ~/.local/bin/uvx`; drag cmux to the Trash).

Uninstalling Berth never deletes a conversation: Claude's transcripts stay in `~/.claude/projects`, and any one can be resumed with `claude -r <session id>` from its directory.
