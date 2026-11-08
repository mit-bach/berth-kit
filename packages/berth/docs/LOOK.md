# Berth screen

The screen is the place the work lives. One terminal. One tree. Live rows and parked rows use the same line. A folder can be closed. The counts on that folder stay. A row filed while the screen is open is marked, including when its folder is closed.

The excerpt is a frame. It replaces the tree until you close it. There is no second pane.

## What a person sees

The top line is the catalog. It always shows the live count, the parked count, the child RSS when a figure is stored, and whether the watcher is up. `playing` and `pausing` appear when those counts are not zero. `new` and `busy` appear when those counts are not zero. The counts ignore the search box and ignore which folders are closed. They are the desk, not the filter.

```text
Berth                3 live  8 parked  1 new  1 busy  9 Grok  2 Shell  children 7.1 GB  watch up
```

The second line is the hint, the search, or the prompt you are typing. The bottom line is the result of the last key, or a prompt's instruction. The rows between them are the tree.

A folder line has a triangle, the name, and the counts inside it. `▾` is open. `▸` is closed. Closing a folder hides the rows under it and leaves the counts, including `N new` and `N busy`.

A berth line has the title, the state word, a three-character flag, the kind word, the one-line note, and the folder path. Kind `grok` is the word Grok. Kind `shell` is the word Shell. Kind `berth` is the word Berth. The state word starts at the same column on every berth, at every depth. The flag column and the kind column do too. Two rows with the same title show different paths. When two rows share a title and a folder, the path gains the berth id. Inbox is a root folder. Its summary starts with `not in a folder`.

At 96 columns the fields are fixed:

| Field | Width | What it holds |
| --- | ---: | --- |
| cursor | 1 | `>` on the current row, otherwise a space |
| gutter | 26 | indent, triangle, title |
| state | 9 | `live`, `live busy`, `parked`, `playing`, `pausing` |
| flags | 3 | `!` protect, `*` new, `v` mark |
| kind | 6 | `Grok`, `Shell`, `Berth` |
| note | 31 | the one-line note, cut on the right |
| path | 15 | the folder path, cut on the left |

Narrower terminals give the spare columns up in that order: path, then note, then the gutter. The state word and the flags stay.

Folders follow `folder.sort`. Inside a folder, `live`, `playing`, and `pausing` come first, then `parked`. Each of those groups is ordered by title. The running work sits at the top of its folder. The parked work is the same kind of line underneath.

## States and flags

`parked`, `playing`, `live`, and `pausing` are the states in the catalog. The screen draws those words. It does not invent a fifth state.

`busy` is a tag on a live row. The screen reads `runtime.busy`. A missing table, or a missing row, means quiet. The screen does not read hook files and does not call cmux.

`!` is protect. `*` is new. A new row is a live berth whose `created_at` is later than the moment the screen process opened. It stays marked until you quit. The header counts it. A closed folder counts it. The terminal loop re-reads the database every 400 ms, and again after each key, so a row the watcher just inserted shows up on the next frame.

Archive hides a folder and its parked berths. A live, playing, or pausing berth under an archived folder stays on screen, and the folder line says `archived`. Search does not bring the hidden parked rows back.

## Keys

| Key | What it does |
| --- | --- |
| j k | Move. Arrow keys do the same. |
| h | Close the folder under the cursor. On a berth, or on a folder that is already closed, move to the parent folder. |
| l | Open the folder under the cursor. If it is already open, move to its first child. |
| g G | First row. Last row. |
| ctrl-u ctrl-d | Half a page. |
| enter | Parked row: record play. Live row: record focus. `playing` or `pausing`: say that it is in progress, and record nothing. A parked `process` row shows the recipe and waits for `y`. |
| p | Quiet live row: record pause. Busy live row, or a live `process`: show the excerpt and wait for `y`. Protected row: refuse. Parked row: say it is already parked. |
| space | Show the stored excerpt. Record nothing. Change nothing. |
| / | Filter by title, note, folder path, excerpt, and cwd. Letters go into the query, including j, k, h, and l. Arrow keys still move. Enter keeps the filter. The header counts stay the full catalog. |
| n | Ask for a folder path. The path starts as the cursor's parent, ready for a name. Enter writes the folder, creating missing parents. |
| m | Ask for a folder path and move that berth. Exact path wins. Otherwise one unique substring wins. Several matches do not move the row. |
| ! | Toggle protect on the berth and write the column. |
| ? | The key list, in place of the tree. |
| q | Quit, from the tree only. |
| esc | Close the excerpt, the confirm, or the key list. Clear the search. Cancel a prompt. |
| y n | `y` records the waiting intent. `n` cancels it. |

A second enter, or a second `p`, does not write a second intent for the same berth and the same operation. The engine deletes the row when it has done the work. Until then the key says it is already recorded.

The 400 ms tick re-reads the catalog. When that intent row is gone, the status line drops `pause recorded`, `play recorded`, `focus recorded`, and `already recorded`. The row shows the state the engine wrote: `parked` after pause, `live` after play.

The excerpt frame, the confirm frame, and the key list take the body. `j` and `k` scroll that text. They do not move the tree and they do not play. `q` does not quit from those frames, and it does not confirm a pause. From the excerpt, space or esc returns to the same row.

## What is written

`n`, `m`, and `!` write the sqlite file the screen has open.

- `n` inserts `folder` rows and an `event` of kind `mkdir`.
- `m` sets `berth.folder_id` and writes an `event` of kind `move`.
- `!` sets `berth.protect` and writes an `event` of kind `protect` or `unprotect`.

Enter and `p` do not change `berth.state`. They do not close a workspace and they do not create one. They insert a row in `intent`:

| op | When |
| --- | --- |
| `play` | Enter on a parked row, or `y` on a parked process. |
| `focus` | Enter on a live row. |
| `pause` | `p` on a quiet live row, or `y` after a busy or process confirm. |

`detail` is JSON. A pause stores `busy` and `confirm`. A process play stores `confirm`. The state in the catalog stays where it was.

## Figures the engine stores

The screen creates these tables when they are missing. It does not fill in a RSS figure. It does not call `cmux memory`.

```sql
CREATE TABLE status (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  child_rss_bytes INTEGER,
  watcher TEXT,
  sampled_at TEXT
);

CREATE TABLE runtime (
  berth_id TEXT PRIMARY KEY,
  busy INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE intent (
  id TEXT PRIMARY KEY,
  at TEXT NOT NULL,
  op TEXT NOT NULL,
  berth_id TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}'
);
```

`child_rss_bytes` is a byte count. The header prints decimal GB at 1000-based units, one decimal, then MB, then bytes. No row, or a null figure, prints `children unknown`. `watcher` prints `watch up` or `watch down`. Anything else, including a missing column, prints `watch unknown`. `sampled_at` is stored for the engine and is not drawn. `runtime.busy` of 1 marks that berth busy.

## Shapes that failed

Two lists, one for live and one for parked, fail. They feel like two programs. The desk is one tree.

A side column for the excerpt fails. It is a panel you might open. Space takes the screen, then gives it back.

A card for the child RSS fails. The figure is one field on the top line, next to the live count, which is the reason the number exists.

Textual fails this screen. It keeps a widget for every row, and its layout is panes. That is how a catalog becomes a three-column dashboard.

prompt_toolkit fails this screen. It is a line editor. A full-screen tree becomes a layout of windows, and the keys live in a binding table. The state machine belongs in one module the tests drive with strings.

Rich fails as the interactive layer. It paints a snapshot. It does not own a cursor, a confirm, or a search.

urwid fails on cost. Each row becomes a column widget, collapse rebuilds the walker, and the tests need a display.

## What runs it

The standard library. `curses` owns the terminal: keys, resize, color, and the 400 ms tick. A pure function turns the model into exactly `height` lines and only fills the visible window. Rows above or below that window are not drawn. The tests read those lines. They do not need a tty and they do not read a screenshot from memory.

Color is a second pass on the same text. Live is green. Busy and new are yellow. Playing and pausing are cyan. Protect is red. Parked stays the default color, so a parked row is not a dim copy of a live row. The selected row is reverse video.

## Frames

The desk fixture is the picture: a course folder, an agent-systems folder, Inbox, live rows, parked rows, one protected row, one busy row, one new row, a Berth row, and a Shell row titled `6.S978 HW1`. Titles and session ids in the fixture are invented. The clock for these frames is `2026-10-02T21:00:00Z`. `fresh-shell` was filed at `2026-10-02T21:23:00Z`, so it is new. The child RSS is the stored status value `7100000000`, drawn as `children 7.1 GB`. The screen did not measure it.

Width 96. Height 18. The last line of the default frame is blank. The excerpt footer is the last line of that frame. The blank rows between the stored screen and the footer are the rest of the terminal.

### Default frame

```text
Berth                4 live  8 parked  1 new  1 busy  9 Grok  2 Shell  children 7.1 GB  watch up
h l fold · n new · r rename · t title · m move · v mark · d delete · f filter · ? keys · q quits
>▾ Course                   1 live  3 parked  4 Grok  0 Shell
     sample-split           live          Grok   split the sample on the mark    Course
     margin-notes           parked        Grok   margin comments on the draft    Course
     mark-finder            parked        Grok   waiting at the prompt           Course
     method-draft           parked        Grok   draft the methods paragraph     Course
 ▾ Agent Systems            1 live  2 parked  1 busy  3 Grok  0 Shell
     signer-lab             live busy     Grok   mid-tool on the signer          Agent Systems
     idle-map               parked        Grok   idle map is a later repair      Agent Systems
     lexicon-run            parked    !   Grok   leave this session              Agent Systems
 ▾ Inbox                    not in a folder  2 live  3 parked  1 new  2 Grok  2 Shell
     Berth                  live          Berth  this screen                     Inbox
     fresh-shell            live      *   Shell  adopted from the sidebar        Inbox
     6.S978 HW1             parked        Shell  shell anchor                    Inbox
     margin-notes           parked        Grok   loose capture                   Inbox
     paper-queue            parked        Grok   a paper queue                   Inbox
```

### Excerpt frame

Space on `mark-finder`. The tree is gone. The header stays. Nothing is played.

```text
Berth                4 live  8 parked  1 new  1 busy  9 Grok  2 Shell  children 7.1 GB  watch up
excerpt · mark-finder · parked · Grok · Course · stored 2026-10-02T16:40:00Z
will play · grok -r fixture-session-mark · cwd /desk/course · recorded 1.0.44
split the sample on the mark
rows 1-40 stay the control
the mark is the blank line
waiting at the prompt










space or esc closes · does not play
```

### Busy confirm

`p` on `signer-lab` uses the same shape as the excerpt. The banner is `busy · signer-lab · live · Agent Systems · y records pause · n cancels`. The footer says the workspace stays up. `y` writes the pause intent with `confirm` true. `n` writes nothing.
