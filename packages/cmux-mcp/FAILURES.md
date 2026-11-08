# cmux-mcp Berth tool failures

Evidence is from Grok `events.jsonl` `mcp_tool_call_completed` rows for server `cmux-mcp` on 2026-10-05 and 2026-10-06, plus the matching `chat_history.jsonl` / compaction arguments. Counts are executions, not forked copies of the same transcript. Eight sessions completed 272 calls. Fifteen failed. Every connect (33) advertised the same fourteen tools: `status`, `folders`, `list`, `get`, `mkdir`, `move`, `rename`, `create`, `play`, `pause`, `focus`, `screen`, `input`, `excerpt`. Claude transcripts under `~/.claude/projects` do not call this server.

Live servers are `python -m cmux_mcp` from the berth venv. Sampled processes have `PYTHONPATH=packages/cmux-mcp` and `BERTH_ROOT=packages/berth`, and `CMUX_WORKSPACE_ID` set. Cwd differs (CASH-RECON vs this repo) and does not decide the failures below.

## 1. `move_berth` arity on `create` and `move` — fix

What the caller did. `create` with `folder: "Home-Notes"` (and a fresh grok or claude command), or `move` with a berth id and `folder: "Home-Notes"`.

Error. JSON-RPC `-32603`, not a tool error object. Eight `create` and three `move`:

`Mcp error: -32603: move_berth() takes 3 positional arguments but 4 were given`

Example: `~/.grok/sessions/.../GROK/01a10c99-0af4-7733-95c8-818c19935471` at `2026-10-06T00:09:39Z`, tool `create`, folder `Home-Notes`, title `design-grok`. Same string on `move` in `01a10cd2-3b8c-7f72-b99f-25d4ba777e61` at `2026-10-05T17:48:27Z`, id `f9737b64-7ed8-4b13-9096-813405f7170a`.

Why. `berth_api` imports `move_berth` from `berth.ops`, which is `(db, berth_id, folder_path)`. The call passes `(catalog_path, id, folder_id, utc_now)`, the shape of `berth.catalog.move_berth`. The workspace is created and adopted before the move, so the agent sees a failure and the row is left in Inbox.

Deterministic. `create` with no `folder` does not move (adopt already filed Inbox). All 17 successful `create` calls omitted `folder`. Every logged `create` that set `folder`, and every `move`, raised this TypeError. Independent of Claude vs Grok, live vs parked, and cwd. The venv's editable `berth` install is what the running server imports; the bad call is in this tree.

## 2. `pause` missing `pid_alive_fn` — fix

What the caller did. `pause` with a berth id. One execution, session `01a10dc9-779e-77c0-8b70-3700e5c3c0cf` at `2026-10-05T20:47:38Z`, on the live Claude wrapper row.

Error. `Mcp error: -32603: pause_berth() missing 1 required keyword-only argument: 'pid_alive_fn'`.

Why. `berth.state.pause_berth` now requires keyword-only `pid_alive_fn`. The MCP call does not pass it. The same method no longer treats the protect mark or a busy agent as a reason to refuse. `berth_api.pause` still raises `PROTECTED` when `row["protect"]` is set, before it calls `pause_berth`. That branch was not hit by the one logged pause (the row was unprotected). It would still block a marked row, which is not the berth behavior just landed. The caller workspace stays protected inside `pause_berth` when `caller_workspace_id` is the pane's `CMUX_WORKSPACE_ID`. The MCP call does not pass that either.

Deterministic for the TypeError on every `pause` against current `berth`. The extra `PROTECTED` check is deterministic for marked rows. Not cwd-dependent.

## 3. `SESSION_IN_USE` on `create --resume` — do not change

What the caller did. `create` whose command contains `grok --resume <session>` for a session that already has a row. One execution: `01a10c68-00cc-72c0-a2fc-4d32091b8743` at `2026-10-05T14:46:47Z`. Forked chats repeat the same transcript at line 93; those are not extra calls.

Error. Already the JSON object, code `SESSION_IN_USE`, message `that session already has a row`.

Why. `_refuse_resume` looks up `agent_session_id` and refuses before `cmux` create. Test `test_create_refuses_a_session_that_already_has_a_row` locks this.

Deterministic when the resume id is already catalogued. A fresh command does not hit it.

## 4. `screen` on a parked row — do not change

What the caller did. `screen` with berth id `867b21c8-9ce3-4480-a87a-052a59dd0d56` after `/fork` parked that row. Session `01a10dc9` at `2026-10-05T20:48:25Z`.

Error. JSON object, code `NOT_LIVE`, message `that row is not an open workspace`, `state: parked`.

Why. `screen` only reads a live workspace ref. The id was a real berth id, not `workspace:N`. The new live fork is a different row.

Deterministic on row state. Eighty-seven other `screen` calls succeeded with a live berth UUID.

## 5. `input` after the row was gone — do not change

What the caller did. `input` key `enter` on id `34bc446b-f46e-469e-8106-e7bd5fc54392`. That id had just been returned by `create` (title `cmux-claude-git-trust`). Session `01a10dc9` at `2026-10-05T20:56:44Z`.

Error. JSON object, code `NOT_FOUND`, message `no berth 34bc446b-f46e-469e-8106-e7bd5fc54392`.

Why. The row was no longer in the catalog (the workspace had also left `cmux`). It was not a `workspace:N` ref and it is not a `cmux_workspace_id` in the catalog now. No logged call passed `workspace:N` or a cmux workspace UUID as `id`. Successful calls passed berth UUIDs. `workspace:N` appears as the `workspace` field of a successful result, which is the cmux ref, not the argument.

Deterministic when that id is not a catalog row. Guessing another row would be wrong.

## 6. Default `BERTH_ROOT` — fix, not seen as a tool error

`berth_api.py` defaults `BERTH_ROOT` to `/Users/olduser/projects/GROK/GROK-Workspace/cmux-dev/cmux-management/engineers/linden`. That directory is not on this machine. Import of `berth` still succeeds for the berth venv with `BERTH_ROOT` unset, because the venv has an editable install of `packages/berth`. A Python without that install, and without `BERTH_ROOT`, fails at process start with `ModuleNotFoundError` before any tool call. No connected server in these logs failed that way: all 33 connects listed 14 tools, and the running processes have `BERTH_ROOT` set. The failure is deterministic on interpreter and env, not on cwd or which agent.

## 7. Retired cmux tool names — do not restore

`README.md` still documents `workspace_list`, `workspace_create`, `screen`, and `input` as cmux tools. `server.py` keeps those schemas in `_RETIRED` and sets `TOOLS = BERTH_TOOLS`. `dispatch` only calls `berth_call`. Names that exist on both sides (`status`, `screen`, `input`, `rename`, `focus`) are the berth tools.

No logged call asked for `workspace_list`, `workspace_create`, `workspace_rename`, `workspace_focus`, `workspace_close`, `surface_list`, or `group`. Agents called the berth names and, aside from the modes above, those calls succeeded (`list` 21/21, `get` 30/30, `play` 2/2, `rename` 3/3, `status` 17/17, `folders` 13/13). Restoring the retired tools beside the berth tools would reintroduce the name collision. This pass does not restore them. Callers keep using the berth tools. A berth UUID stays the id. A `workspace:N` ref or a cmux workspace UUID is resolved only when exactly one live row has that workspace, and is `NOT_FOUND` otherwise. That path was not in the logs; the hard reject of `workspace:` is still the wrong response when a live row does exist.

## 8. Cancelled calls — not a server bug

Chat transcripts also contain `Tool execution was cancelled by the user` for `create`, `screen`, and `input`. The client dropped the call. Nothing to change.

## This pass

Fix modes 1, 2, and 6. Map an unexpected `TypeError` to the existing JSON error object (`INTERNAL`) instead of `-32603`. Leave modes 3, 4, 5, 7, and 8. Do not close live workspaces. Do not edit `~/.grok/config.toml`. Running MCP processes keep the old code until the client starts them again.
