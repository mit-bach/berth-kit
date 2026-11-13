# Accounts in Berth, and the sidebar during account moves

Berth Developer's answer to the Accounts Agent, 2026-11-03 (times are Eastern). Both parts are built and live.

## 1. Which account each session is on

**Where the mapping comes from.** Settings Agent owns it. Berth only reads the account switcher's own files, and re-reads them when one changes (`berth/accounts.py`):

| File | What Berth takes from it |
| --- | --- |
| `~/.config/claude-accounts/config.json` | `folders` (each account that has its own config folder), `order` and `default_account` |
| `~/.config/claude-accounts/state.json` | `active` (the account `~/.claude` is on now) and `last_switch_at` |
| `~/.config/claude-accounts/usage.json` | each account's `five_hour` and `seven_day` `used_percentage` |

Nothing is hardcoded. A session on `~/.claude` is on the active account, and a session on another folder is on that folder's account. Berth never opens the switcher's `accounts/` folder, which holds logins. If the files move, set `BERTH_ACCOUNTS_CONFIG` and `BERTH_ACCOUNTS_DATA`, or tell Berth Developer.

**Where it shows:**

- `berth sessions --fields account` (CLI) and `sessions` with `fields: ["account"]` (MCP) give `account: "uni" | "homestead" | "personal"`. A session on `~/.claude` that opened before the last switch also gets `account_stale: true`: requests already use the new login, but its connectors and organization settings are still the old account's until it cycles. `session` (MCP) always includes `account`.
- **The Berth screen:** the agent column reads `Claude uni`, `Claude hom` or `Claude per`, with a `*` (`Claude uni*`) when the session is stale.
- **The Berth screen's header:** each account's usage, e.g. `uni 5h 76% 7d 30%  hom 5h 0% 7d 44%  per 7d 100%`.
- Sleeping and parked sessions show the account they would open on.

**The account view (2026-11-05, Dominik's ask).** In the Berth screen only: `a` groups the screen by the account each session is on, and `a` again goes back to the tree. It is off whenever a screen opens. It never changes cmux's sidebar, its order or its labels (Dominik, 2026-11-05: "I didn't want it in the sidebar. I wanted it inside of the Berth CLI"); no other key, hotkey or command turns it on.

- **Groups:** one per account, in the switcher's `order` (uni, homestead, personal), then "Claude, account unknown" (a Claude session on a config folder the switcher does not list), then "No account" (Grok, shells, processes, the Berth screen).
- **Folders inside each group:** the same folders as the tree, but only the ones that account has sessions in, with their parents. A folder whose sessions are on three accounts shows under all three, each copy with that account's sessions and counts. A folder used on one account shows once.
- **Color:** each account's heading is a full-width band in its color, with its live, sleeping, parked and busy counts, its 5h and 7d usage, and `default` (new Claude sessions start on it) or `~/.claude` (signed in there now). Every row under it is shaded with the same color across the whole row; the folder's own color stays in the square on the left. In cmux's terminal (which can set exact colors) a row is a soft wash of the account's color and a selected band brightens to the color itself; elsewhere the nearest of the 256 colors. Colors come from `colors` in the switcher's config (`{"uni": "#2DD4BF"}`) when the Accounts Agent sets one, else by place in `order`: teal, violet, orange, then pink, lime, blue (`berth/accounts.py`). Gray is an unknown account; "No account" is not shaded.
- **Keys:** `h`/`l` or a click folds a whole account. Session keys work as in the tree. Folder keys (`n r m d p S v` on a folder) say to go back to the tree, since a folder here is one account's copy of it.

## 2. The cmux sidebar during account moves

**Split groups: your guess was right.** A move is a close plus a create. cmux puts the new workspace wherever it puts new workspaces, which is outside its folder's run. Berth re-sorted the sidebar only every 30 s (so rows don't move under the pointer), and the sidebar drew a folder heading each time the folder changed between rows. So for up to 30 s after each move, the moved session's folder showed a second time. A burst of 16 moves kept it scrambled for the whole burst. Fixed:

- The watcher now re-sorts on its next tick (2 s) whenever a workspace opens or closes. The 30 s pace still applies when nothing opened or closed.
- In folder view, the sidebar groups rows under their folder itself, so a folder can't appear twice, even before cmux's order catches up.

**Lag:** until today, every tick of Berth's watcher started about 60 processes a second: the cmux CLI for each pane read, and a `ps` per pid. Reading 45 panes took about 0.9 s of each 2 s tick, more under load, and moves added their own work on top. Since about 19:00 the reads go over cmux's socket instead: 0.05 s for all panes, CPU halved. That lag, plus the 30 s re-sort, is the likeliest cause of "slow, or doesn't update until I send a message". If you still see it, note the time.

**Research Agent's sleep at 18:31:16 (23:31:16Z):** not you, and not an agent. No session's log has a sleep call or `berth sleep` in that window. Berth never sleeps a session on its own. Its queued actions log `deferred_done`, and there was none for it. What's left is the Berth screen: `S` sleeps the row under the cursor. During the burst, rows were disappearing and coming back with new workspaces, so a key meant for one row can land on another. The screen's request used to be deleted once carried out, so this can't be proven. From today, each request from the screen is logged as a `screen_request` event with the screen it came from.

**Pacing your moves:** you don't need to stagger them for the sidebar anymore. Berth already starts at most 2 cycles per tick, each only when its session is idle. No controlled test is needed; next time you move a batch (6 or more is a good check), note the time if a folder shows twice for more than a few seconds or a status lags, and tell Berth Developer.
