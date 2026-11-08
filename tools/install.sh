#!/bin/bash
# Install Dominik's agent setup from this kit, one stage at a time.
#
#   tools/install.sh <stage> [--dry-run] [--here] [--no-sidebar] [--replace-mcp]
#
#   cmux     cmux's settings for Berth: Berth's watcher may connect to cmux's
#            socket, and cmux's own agent resume is off (cmux itself is
#            installed by hand; your track's START-HERE says how)
#   berth    Berth: its virtualenv, the berth command, the watcher, the Berth
#            sidebar, and the Cmd+Shift+B shortcut
#   agents   the berth and cmux-mcp MCP servers for Claude Code, and the
#            berth and cmux-claude skills
#   grok     optional: the same for Grok Build, if it is installed
#   all      cmux, berth, agents
#
#   --dry-run      print every step and change nothing
#   --here         allow a kit in Downloads, Desktop, Documents, iCloud, or /tmp
#   --no-sidebar   leave cmux's own sidebar as it is
#   --cmux-browser keep links opening in cmux's built-in browser (the default sends
#                  them to the system's default browser)
#   --replace-mcp  replace MCP servers already registered as berth or cmux-mcp
#                  that point somewhere else
#
# Everything runs from this kit's directory for good: the virtualenv, both MCP
# servers, the skills (symlinks), and the watcher. Keep the kit where it is.
# Running a stage again is safe. A file or directory replaced is backed up
# first, as <name>.bak-berth-<time>. What it installs and how to undo each
# piece: docs/UNINSTALL.md.
#
# cmux must be in /Applications/cmux.app: Berth starts Claude through cmux's
# Claude wrapper there.

set -euo pipefail

KIT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$KIT"
BERTH_HOME="$REPO/packages/berth"
VENV="$BERTH_HOME/.venv"
STAMP="$(date +%Y%m%d-%H%M%S)"
CMUX_APP=/Applications/cmux.app
CMUX_BIN_DIR="$CMUX_APP/Contents/Resources/bin"
STAGE="${1:-}"
DRY=0; HERE=0; SIDEBAR=1; REPLACE_MCP=0; CMUX_BROWSER=0
shift || true
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --here) HERE=1 ;;
    --no-sidebar) SIDEBAR=0 ;;
    --cmux-browser) CMUX_BROWSER=1 ;;
    --replace-mcp) REPLACE_MCP=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
case "$STAGE" in cmux|berth|agents|grok|all) ;; *) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 2 ;; esac

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
run() {
  if [[ $DRY == 1 ]]; then printf '  would run:'; printf ' %q' "$@"; printf '\n'; else "$@"; fi
}
backup() {
  # Move a real file or directory aside before a link replaces it. A link is just replaced.
  local target="$1"
  if [[ -e "$target" && ! -L "$target" ]]; then run mv "$target" "$target.bak-berth-$STAMP"; fi
}
link() {
  backup "$2"
  run mkdir -p "$(dirname "$2")"
  run ln -sfn "$1" "$2"
}

[[ "$(uname)" == "Darwin" ]] || { say "This setup needs macOS: cmux and Berth's watcher are Mac-only."; exit 1; }
[[ -d "$BERTH_HOME/berth" && -d "$REPO/packages/cmux-mcp" ]] || { say "This is not the Berth kit: $REPO/packages is missing."; exit 1; }
case "$KIT" in
  /tmp/*|/private/tmp/*|/var/folders/*|"$HOME/Downloads"/*|"$HOME/Desktop"/*|"$HOME/Documents"/*|"$HOME/Library/Mobile Documents"/*|"$HOME/Library/CloudStorage"/*)
    if [[ $HERE == 0 ]]; then
      say "The kit is at $KIT. Everything installed keeps running from this directory, and"
      say "macOS guards Downloads, Desktop, Documents, and iCloud folders from background programs like Berth's watcher."
      say "Move it somewhere permanent first (for example ~/berth-kit), or pass --here."
      exit 1
    fi ;;
esac

CMUX="$(command -v cmux || true)"
[[ -z "$CMUX" && -x "$CMUX_BIN_DIR/cmux" ]] && CMUX="$CMUX_BIN_DIR/cmux"
PY="$(command -v python3.12 || true)"
[[ -z "$PY" && -x "$HOME/.local/bin/python3.12" ]] && PY="$HOME/.local/bin/python3.12"
UV="$(command -v uv || true)"
[[ -z "$UV" && -x "$HOME/.local/bin/uv" ]] && UV="$HOME/.local/bin/uv"
CLAUDE="$(command -v claude || true)"

need_cmux() {
  [[ -d "$CMUX_APP" && -n "$CMUX" ]] || { say "cmux is not installed in /Applications. Install it first (onboarding/terminal/stages/1-cmux.md)."; exit 1; }
  local version
  version="$("$CMUX" --version 2>/dev/null | head -1)"
  if [[ "$version" =~ ([0-9]+)\.([0-9]+) ]] && (( BASH_REMATCH[1] == 0 && BASH_REMATCH[2] < 65 )); then
    say "cmux is $version; this kit needs 0.65 or newer (it was tested on 0.65.2). Update cmux first."; exit 1
  fi
}

stage_cmux() {
  step "cmux: settings Berth needs"
  need_cmux
  say "cmux: $("$CMUX" --version 2>/dev/null | head -1)"
  [[ -x "$CMUX_BIN_DIR/cmux-claude-wrapper" ]] || say "warning: this cmux has no cmux-claude-wrapper; Berth cannot follow Claude sessions without it. Update cmux."
  # By default cmux answers only processes started inside its own terminals. Berth's watcher is a
  # LaunchAgent, not a cmux process, so it needs "automation": any process of this user may connect
  # (docs/DECISIONS.md, "cmux socket access").
  run "$CMUX" config set automation.socketControlMode automation
  # cmux's own resume uses stale session ids after forks and /resume; Berth resumes instead.
  run "$CMUX" config set terminal.autoResumeAgentSessions false
  # Links (clicked in a terminal, `open <url>` in one, and the sidebar's pull request and port links) open in the
  # system's default browser, not cmux's built-in one (docs/DECISIONS.md, "Where links open"). --cmux-browser keeps cmux's.
  if [[ $CMUX_BROWSER == 0 ]]; then
    for key in browser.openTerminalLinksInCmuxBrowser browser.interceptTerminalOpenCommandInCmuxBrowser sidebar.openPullRequestLinksInCmuxBrowser sidebar.openPortLinksInCmuxBrowser; do
      run "$CMUX" config set "$key" false
    done
  fi
  [[ -n "$UV" || -n "$PY" ]] || say "note: Berth's stage needs uv or Python 3.12 (onboarding/terminal/stages/1-cmux.md, step 1)."
}

stage_berth() {
  step "Berth: virtualenv, command, watcher"
  need_cmux
  if [[ -n "$UV" ]]; then
    run "$UV" venv --python 3.12 --allow-existing "$VENV"
    run "$UV" pip install --quiet --python "$VENV/bin/python" -e "$BERTH_HOME[dev]"
  elif [[ -n "$PY" ]]; then
    run "$PY" -m venv "$VENV"
    run "$VENV/bin/python" -m pip install --quiet --upgrade pip
    run "$VENV/bin/python" -m pip install --quiet -e "$BERTH_HOME[dev]"
  else
    say "uv or Python 3.12 is required (onboarding/terminal/stages/1-cmux.md, step 1)."; exit 1
  fi
  link "$VENV/bin/berth" "$HOME/.local/bin/berth"
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "note: add ~/.local/bin to PATH in the shell profile, so 'berth' runs in every terminal (doctor.py checks it)." ;; esac
  # The watcher: a LaunchAgent running `berth watch`, logs and the catalog in ~/.local/share/berth.
  run env PATH="$(dirname "$CMUX"):$VENV/bin:$PATH" "$VENV/bin/berth" install
  step "cmux: Cmd+Shift+B opens the Berth screen"
  run "$CMUX" config set shortcuts.bindings.jumpToLastPrompt '"none"'
  if [[ $DRY == 1 ]]; then
    python3 "$KIT/tools/cmux_action.py" --dry-run | sed 's/^/  /' || true
  elif ! python3 "$KIT/tools/cmux_action.py"; then
    say "warning: the Berth action was not added to cmux.json (see above); add it by hand or leave it."
  fi
  if [[ $SIDEBAR == 1 ]]; then
    step "cmux: the Berth sidebar and notifications"
    if [[ $DRY == 1 ]]; then
      say "  would run: berth sidebar install (it backs up cmux.json as cmux.json.bak-<time> first)"
    elif ! env PATH="$(dirname "$CMUX"):$VENV/bin:$PATH" "$VENV/bin/berth" sidebar install; then
      say "warning: berth sidebar install failed; cmux's own sidebar stays. Run it again after updating cmux."
    fi
  fi
}

register_mcp() {
  # register_mcp <name> <kit path it must mention> <claude mcp add arguments...>
  local name="$1" mine="$2" current
  shift 2
  if current="$(claude mcp get "$name" 2>/dev/null)"; then
    if [[ "$current" != *"$mine"* && $REPLACE_MCP == 0 ]]; then
      say "An MCP server named $name is already registered and does not point at this kit:"
      printf '%s\n' "$current" | grep -E "Scope:|Command:|Args:" | sed 's/^/    /'
      say "Not replaced. Show it to the operator; re-run with --replace-mcp to replace it."
      return 1
    fi
    if printf '%s' "$current" | grep -qE "Scope: (Project|Local)"; then
      say "warning: $name is also registered for a project or locally; there it shadows this one."
    fi
    if [[ $DRY == 1 ]]; then say "  would run: claude mcp remove $name -s user"; else claude mcp remove "$name" -s user >/dev/null 2>&1 || true; fi
  fi
  run claude mcp add --scope user "$name" "$@"
}

stage_agents() {
  step "Claude Code: the berth and cmux-mcp MCP servers"
  [[ -x "$VENV/bin/berth" || $DRY == 1 ]] || { say "Install the berth stage first."; exit 1; }
  local ok=1
  if [[ -z "$CLAUDE" ]]; then
    say "claude is not on PATH. Register the two servers by hand:"
    say "  claude mcp add --scope user berth -- $HOME/.local/bin/berth mcp"
    say "  claude mcp add --scope user cmux-mcp -e PYTHONPATH=$REPO/packages/cmux-mcp -e BERTH_ROOT=$BERTH_HOME -- $VENV/bin/python -m cmux_mcp"
  else
    register_mcp berth "$HOME/.local/bin/berth" -- "$HOME/.local/bin/berth" mcp || ok=0
    register_mcp cmux-mcp "$BERTH_HOME" -e "PYTHONPATH=$REPO/packages/cmux-mcp" -e "BERTH_ROOT=$BERTH_HOME" \
      -- "$VENV/bin/python" -m cmux_mcp || ok=0
  fi
  step "Claude Code: the berth and cmux-claude skills (links into this kit)"
  link "$BERTH_HOME/skill" "$HOME/.claude/skills/berth"
  link "$REPO/skills/cmux-claude" "$HOME/.claude/skills/cmux-claude"
  say "Start a new Claude session (or cycle this one) to load them."
  [[ $ok == 1 ]] || exit 1
}

stage_grok() {
  step "Grok Build (optional)"
  [[ -d "$HOME/.grok" ]] || { say "Grok Build is not installed; nothing to do."; return 0; }
  [[ -x "$VENV/bin/berth" || $DRY == 1 ]] || { say "Install the berth stage first."; exit 1; }
  link "$BERTH_HOME/skill" "$HOME/.grok/skills/berth"
  link "$REPO/skills/cmux-grok" "$HOME/.grok/skills/cmux-grok"
  # Berth starts Grok from ~/.grok/bin, not through cmux's grok wrapper, so Grok needs cmux's hooks
  # in its own config. Prefer cmux's installer; fall back to the kit's copy of Dominik's hook file.
  local hook="$HOME/.grok/hooks/cmux-session.json"
  if [[ $DRY == 1 ]]; then
    say "  would run: cmux hooks grok install --yes (or, if that fails and $hook is missing, write the kit's hook file)"
  elif "$CMUX" hooks grok install --yes >/dev/null 2>&1; then
    say "  cmux installed its Grok hooks"
  elif [[ -e "$hook" ]]; then
    say "  $hook exists; left as it is."
  else
    mkdir -p "$(dirname "$hook")"
    "$VENV/bin/python" - "$REPO/grok/hooks/cmux-session.json" "$hook" "$HOME" <<'PY'
import sys
from pathlib import Path
source, target, home = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
text = source.read_text(encoding="utf-8")
# The file escapes its slashes (\/); replace both spellings of the source Mac's home.
text = text.replace("\\/Users\\/olduser", home.replace("/", "\\/")).replace("/Users/olduser", home)
target.write_text(text, encoding="utf-8")
PY
  fi
  local config="$HOME/.grok/config.toml"
  if [[ $DRY == 1 ]]; then
    [[ -e "$config" ]] && say "  would back up $config"
    say "  would set the [mcp_servers.berth] and [mcp_servers.cmux-mcp] tables in $config"
  else
    [[ -e "$config" ]] && cp "$config" "$config.bak-berth-$STAMP"
    "$VENV/bin/python" - "$config" "$HOME/.local/bin/berth" "$VENV/bin/python" "$REPO" <<'PY'
import sys
from pathlib import Path
path, berth, python, repo = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
block = (
    "[mcp_servers.berth]\n"
    f'command = "{berth}"\n'
    'args = ["mcp"]\n'
    "enabled = true\n"
    "startup_timeout_sec = 30\n\n"
    "[mcp_servers.cmux-mcp]\n"
    f'command = "{python}"\n'
    'args = ["-m", "cmux_mcp"]\n'
    "enabled = true\n"
    "startup_timeout_sec = 30\n\n"
    "[mcp_servers.cmux-mcp.env]\n"
    f'PYTHONPATH = "{repo / "packages" / "cmux-mcp"}"\n'
    f'BERTH_ROOT = "{repo / "packages" / "berth"}"\n'
)
ours = ("[mcp_servers.berth]", "[mcp_servers.berth.", "[mcp_servers.cmux-mcp]", "[mcp_servers.cmux-mcp.")
text = path.read_text(encoding="utf-8") if path.exists() else ""
kept, skipping = [], False
for line in text.splitlines(keepends=True):
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        skipping = stripped.startswith(ours)
    if not skipping:
        kept.append(line)
body = "".join(kept).rstrip()
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text((body + "\n\n" if body else "") + block, encoding="utf-8")
print(f"  set berth and cmux-mcp in {path}")
PY
  fi
}

case "$STAGE" in
  cmux) stage_cmux ;;
  berth) stage_berth ;;
  agents) stage_agents ;;
  grok) stage_grok ;;
  all) stage_cmux; stage_berth; stage_agents ;;
esac

step "Next"
[[ $DRY == 1 ]] && say "Dry run: nothing was changed."
say "Check it: python3 $KIT/tools/doctor.py"
