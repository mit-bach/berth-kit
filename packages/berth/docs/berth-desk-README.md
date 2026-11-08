# Berth

Design for a session desk that sits beside cmux. This directory is the design. It does not contain a program.

Measured on this Mac on 2026-10-02, against cmux 0.64.25 (106).

## Decision

Build **Berth**, a local catalog and a terminal program. Berth tells cmux, through the socket cmux already publishes, when to open a workspace and when to close one.

A **berth** is one piece of work. It is either **live** (a cmux workspace is running) or **parked** (the workspace is closed, and the catalog holds the resume recipe). A **folder** is a catalog directory. Folders nest to any depth. A cmux sidebar group does not.

Pause stores the recipe, then closes the workspace. Play creates a workspace from the recipe, or focuses the workspace when it is already live.

The cmux sidebar then shows the work that is actually running. The folder tree, including everything parked, is the Berth screen.

## Why this shape

cmux 0.64.25 already restores a Grok session with `grok -r <id>`, already groups workspaces one level deep, and already has an automatic hibernation switch. The missing piece is a durable list of work you can close without losing the place you stopped.

A fork of cmux would take on a Swift application that ships often, to add a list the socket can already drive. A custom sidebar file cannot read a catalog: the sidebar runtime has no filesystem. An ExtensionKit sidebar can, and it runs inside the macOS sandbox. An Obsidian plugin would put the control surface in a second app.

Details, the rejected shapes, and the contract are in [DESIGN.md](DESIGN.md). The measurements and the sources are in [EVIDENCE.md](EVIDENCE.md).

## What is expensive

`cmux memory --all` reported:

| Piece | Size |
| --- | --- |
| cmux app footprint | 699 MB |
| Child processes | 7.68 GB across about 515 processes |

The child cost is about 40 Grok processes. Each one starts the MCP servers in `~/.grok/config.toml`: chrome-devtools, browser-use, and overleaf, plus a shell. Older processes still run `cmux-agent-mcp`. Parking a berth kills that process tree. A live berth stays expensive until its folder names a smaller MCP set.

Chrome-CDP.app is a separate tree, 3957 MB in the same snapshot, with no processes in common with cmux. Closing cmux workspaces does not shrink that browser.

## Read this first

1. [DESIGN.md](DESIGN.md) — the system, the screen, the records, and the rules for pause and play.
2. [EVIDENCE.md](EVIDENCE.md) — the live sidebar, the process table, and the cmux features this design relies on.
