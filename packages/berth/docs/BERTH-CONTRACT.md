# Berth contract

Shared seam for this arc. Linden and Holly both build against this file. Neither reads the other's workspace. If this file and DESIGN.md disagree on a column or a command, this file wins. If the operator prompt and this file disagree, the operator prompt wins.

## Product

Berth is one Python 3.12 program. The import name is `berth`. The console script is `berth`.

A berth is one catalog record. A folder is a nested catalog directory. Live means a cmux workspace is running for that record. Parked means the workspace is closed and the recipe is stored. Play focuses the live workspace or creates one from the recipe. Pause writes the recipe and then closes the workspace.

Capture is the operator's priority. While `berth watch` is running, every new cmux workspace becomes a live berth without a person typing adopt. The screen shows that tree. cmux still draws the terminal. Berth is what makes the workspace belong to a folder at once.

Automatic pause stays off. Capture is not pause.

## Layout each child owns

Linden writes the engine under `engineers/linden/` and does not write a screen.

Holly writes the screen under `engineers/holly/` and does not write the engine.

A later turn assembles them. This turn each half runs alone.

## Catalog

Sqlite file, default `~/.local/share/berth/berth.sqlite`. Directory mode `0700`. File mode `0600`. Tests use a temp path. Never the real catalog, except the one live proof workspace named `berth-proof`.

```sql
CREATE TABLE folder (
  id TEXT PRIMARY KEY,
  parent_id TEXT,
  name TEXT NOT NULL,
  sort INTEGER NOT NULL,
  profile_id TEXT,
  default_cwd TEXT,
  note TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE berth (
  id TEXT PRIMARY KEY,
  folder_id TEXT NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  state TEXT NOT NULL,
  protect INTEGER NOT NULL DEFAULT 0,
  cwd TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  recipe_argv TEXT NOT NULL,
  recipe_display TEXT NOT NULL,
  agent_name TEXT,
  agent_session_id TEXT,
  agent_version TEXT,
  cmux_workspace_id TEXT,
  cmux_surface_id TEXT,
  cmux_pid INTEGER,
  attachments TEXT NOT NULL DEFAULT '{}',
  excerpt TEXT NOT NULL DEFAULT '',
  excerpt_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  parked_at TEXT,
  played_at TEXT
);

CREATE TABLE event (
  id TEXT PRIMARY KEY,
  berth_id TEXT,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}'
);
```

`kind` is `grok`, `other-agent`, `shell`, or `process`. `state` is `parked`, `playing`, `live`, or `pausing`. `recipe_argv` is a JSON array of strings. Time is ISO-8601 UTC.

Inbox is a root folder named Inbox. Adopt places a workspace there when no cwd rule matches.

## Commands

Every command accepts `--json` and then prints one JSON object. Failure prints the error object and exits non-zero.

```json
{ "error": { "code": "BUSY", "message": "...", "details": {} } }
```

Codes: `BUSY`, `PROTECTED`, `SESSION_MISSING`, `IN_PROGRESS`, `NOT_FOUND`, `NOT_LIVE`, `CMUX_DOWN`, `CMUX_REJECTED`, `FOLDER_NONEMPTY`, `FOLDER_LIVE`, `KIND_UNSAFE`, `SESSION_IN_USE`.

| Command | Effect |
| --- | --- |
| `berth status` | Live count, parked count, watcher up or down, child RSS when `cmux memory --all` answers. |
| `berth tree` | Nested folders and berths. |
| `berth adopt` | Live records for current workspaces. Closes nothing. Second run does not duplicate a workspace UUID. |
| `berth watch` | Blocks. Subscribes to cmux. On a workspace appearing, adopt that workspace only. |
| `berth install` | Installs a LaunchAgent that runs `berth watch` and restarts it. Prints the plist path. |
| `berth play <id or query>` | State machine in DESIGN.md section 5. |
| `berth pause <id or query>` | Recipe flushed before close. |
| `berth focus <id>` | Select. `NOT_LIVE` when parked. |
| `berth new` | New parked grok berth, then play. |
| `berth mkdir <path>` | Folders, including parents. |
| `berth move <id> <folder>` | |
| `berth note <id> <text>` | |
| `berth protect <id>` / `berth unprotect <id>` | |
| `berth excerpt <id>` | |
| `berth reconcile` | DESIGN.md section 10. |
| `berth log <id>` | |

Query: exact id, else exact title, else one unique substring. Ambiguous prints the matches and does not act.

`berth` with no subcommand is the screen. Holly owns that entry's behavior. Until the halves are assembled, Holly's tests invoke her module directly.

## Screen data

The screen reads the sqlite schema above. It does not scrape the cmux sidebar. A row needs title, state, folder path, note, excerpt, protect, and kind. The header needs live count, parked count, and the child-RSS figure when status has it.

Keyboard the screen implements: j/k move, h/l collapse, Enter play, p pause, Space excerpt, / search, n new folder, m move, ! protect. Pause of a busy berth shows the excerpt and waits for confirm. Protected rows do not pause.

The screen is a terminal program. It is not a browser page and not HTML.

## Capture proof

The only workspace a child may close is one that child created whose title is exactly `berth-proof`. Do not pause or close any other workspace. Do not edit `~/.grok/config.toml`. Do not signal a Grok process.

## What stays in DESIGN.md

Recipes, the pause sequence, the play sequence, reconcile, folder rules, profiles, and the ban on recursive delete stay as DESIGN.md wrote them. Profiles are not this turn's gate. Linden may leave profile directories unimplemented and say so in PROOF.md. Capture, adopt, pause, play, and the screen are this turn.
