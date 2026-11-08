# Proof, pass 2

The server is `python3 -m cmux_mcp`. Initialize returned `cmux-mcp` `0.1.0`.

`group` create used only a name, so it did not pass `--from`. The new group was `workspace_group:6`, named `cmux-mcp-proof`. Its anchor was the new workspace `workspace:46`, not an existing workspace.

`workspace_create` put one workspace in that group. `focus` was false, and the workspace was not selected. `cwd` was `/tmp/cmux-mcp-proof`.

## workspace_list

The proof workspace from `workspace_list`:

```json
{
  "ref": "workspace:47",
  "id": "E02551D7-E8A0-4717-8307-69D0C5EE2C40",
  "title": "cmux-mcp-proof",
  "cwd": "/tmp/cmux-mcp-proof",
  "selected": false,
  "group_ref": "workspace_group:6"
}
```

The proof group from the same list:

```json
{
  "ref": "workspace_group:6",
  "name": "cmux-mcp-proof",
  "anchor_workspace_ref": "workspace:46",
  "member_workspace_refs": [
    "workspace:46",
    "workspace:47"
  ],
  "member_count": 2,
  "is_collapsed": false,
  "is_pinned": false
}
```

`group_ref` on the workspace is that group. The other groups were still named School OS, UNI-Classes, Fog of war, and Chrome Dev. Their refs, anchors, member counts, collapsed state, and pinned state were the same after cleanup as before the proof.

## File placement

`input` arguments:

```json
{
  "workspace": "workspace:47",
  "surface": "surface:47",
  "target": "prompt",
  "path": "/tmp/cmux-mcp-proof/payload.txt"
}
```

Those arguments do not contain the file body. The result was `submitted: false`, `bytes: 208`, and sha256 `a1486fbb9d858c942c3b469de0cceb8c06ddc4d25b9d4404ffefc799d583842c`. The result does not contain the file body.

`screen` showed the file inside the prompt box. The footer was `Enter:send`.

```text
  ╭─────────────────────────────────────────────────────────────────────────────────────────╮
  │ ❯ CMUXMCP-PROOF-LINE-1 the server read this file from disk.                             │
  │   CMUXMCP-PROOF-LINE-2 the tool arguments do not contain this sentence.                 │
  │   CMUXMCP-PROOF-LINE-3 the bytes are in the prompt box and Enter was not pressed.       │
  │                                                                                         │
  ╰─────────────────────────────────────────────────────── Grok 4.7 (low) · always-approve ─╯

  Enter:send  │  Shift+Enter/Opt+Enter:newline  │  Shift+Tab:mode  │  Ctrl+.:shortcuts
```

## Cleanup

`workspace_close` closed only `workspace:47`. The group was then anchor-only. `group` `ungroup` with `remove_generated_anchor` true removed `workspace_group:6` and the generated anchor `workspace:46`.

`workspace:38` and `workspace:39` stayed open. No group named `cmux-mcp-proof` remains.
