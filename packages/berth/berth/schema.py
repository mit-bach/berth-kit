"""Contract tables are the seam. Screen tables hold figures and intents."""

CONTRACT_DDL = """
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
"""

STATUS_SQL = """
CREATE TABLE status (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  child_rss_bytes INTEGER,
  watcher TEXT,
  sampled_at TEXT
)
"""

RUNTIME_SQL = """
CREATE TABLE runtime (
  berth_id TEXT PRIMARY KEY,
  busy INTEGER NOT NULL DEFAULT 0,
  why TEXT NOT NULL DEFAULT '',
  draft TEXT NOT NULL DEFAULT '',
  seen_at TEXT,
  status TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT ''
)
"""

INTENT_SQL = """
CREATE TABLE intent (
  id TEXT PRIMARY KEY,
  at TEXT NOT NULL,
  op TEXT NOT NULL,
  berth_id TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}'
)
"""
