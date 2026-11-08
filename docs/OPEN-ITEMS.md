# Open items and known limits

## Known failing tests (3, all older than this kit)

- `packages/berth/tests/test_desk.py::test_desk_frame_is_one_tree` and `::test_look_matches_the_rendered_frames`: the screen's header grew and no longer matches the golden frames in `packages/berth/docs/LOOK.md`.
- `packages/berth/tests/test_terminal.py::test_package_does_not_call_out`: the test says the screen modules never import cmux, but the screen now runs the intent engine, which does.

Fixing them is a design choice (update the frames, or move the engine out of the screen process), so they were left.

## Limits

- **macOS only.** cmux, the LaunchAgent, and the clipboard paths are Mac-specific.
- **cmux is required.** Berth acts only through cmux's CLI and socket.
- **A folder's default directory** (the rule that files a new workspace by its directory) cannot be set from the CLI or the MCP yet. New workspaces opened by hand land in Inbox; file them with `m` in the screen, `berth move`, or the migration plan. Sessions agents start go to the folder they name, or the agent's own folder.
- **Grok sessions cannot change working directory**; `grok -r` resumes only where a session started.
- **No import.** Berth catalogs what runs in cmux. Conversations elsewhere come in by being resumed in cmux (`tools/migrate.py`).
- **cmux-mcp still has older Berth tools** (list, get, play, fork, ...) next to `screen` and `input`. Use the `berth` MCP for sessions; the older tools may be removed.
- **Manual permission mode is not recorded.** It shows nothing on Claude's status strip, so a session slept or cycled in manual mode reopens in their `settings.json` default (Berth starts sessions with no permission flag). It matters only for a session switched to manual by hand. `docs/DECISIONS.md`, decision 2.
- **The MCP's tool descriptions** use Dominik's own folder names in their examples; they are only examples.

## Not done in Berth itself

- State-machine stages 3 and 4: name and identity changes as transitions with events; merging provably duplicate rows.
- Moving the catalog to `~/.berth/` (Dominik's convention for data folders) waits for his OK; the kit uses `~/.local/share/berth/`.

## The prompt guide's examples

`skills/cmux-claude/references/prompts.md` (identical in cmux-grok) teaches by Dominik's own past prompts, which come from his coursework. Email addresses in them were replaced with placeholders. Copy their shape, not their content.
