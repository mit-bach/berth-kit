# Ways of working

How Dominik works with twenty to thirty agents at once. These are patterns, not rules; take what fits your operator.

## One session, one job, one name

Each conversation has one job and a short name that says it ("Credit memo PR", "Recon export fix"). The name is the same in Berth, in cmux's sidebar, and in Claude. A session that finished its job is put to sleep or deleted (the trash keeps it 30 days), not left open.

## Folders by area of work

Top-level folders are areas (a company, personal, the agent system itself); subfolders are projects or teams inside them. A session's Berth folder is where it is filed; its working directory is where it runs. They are independent: a session filed under `Work/Billing` may run in any repo. The sidebar colors make the areas readable at a glance.

## Orchestrators and workers

Some sessions run other sessions. An orchestrator owns an area: it starts workers on jobs, watches their stop files, reviews what they produce, forks a worker to try a variant, sleeps finished ones, and raises `urgent` only when Dominik must decide something. Workers do one job and stop. The orchestrator's brief lives in its working directory's `CLAUDE.md`; it uses the `berth` MCP to manage sessions and cmux-mcp to read or answer a worker's pane.

A related pattern is a **steward**: one session that owns a shared resource (a set of repos, the container engine) so that other agents ask it instead of operating the resource themselves. Requests go in as files; the steward answers with files and stop files.

## Jobs as files, completion as files

A job for another agent is written to a file first (the cmux-claude skill's `references/prompts.md` says how: the job, the inputs, what to write, what fails it), and given with `create {"prompt_file": ...}` or `input {"path": ...}`. Long prompts never go as typed text. The worker writes a stop file (an absolute path named in the job) when it is done, and the caller waits on that file (`scripts/wait-stop-files.py`), not on the screen. Disk is the proof of done.

## Forks for variants and second opinions

`create {"from": <id>}` copies a conversation into a new session next to it. Dominik uses it to try a second approach with the full context, to split a long session into two tasks, or to ask "what do you think" without disturbing the original. Claude Code's own `/subtask` (which forks inside the pane) also exists; Berth's fork is the one that gives the copy its own pane and row.

## Restart instead of re-explain

When a skill, an MCP server, or a setting changes, sessions get it by being cycled, not by starting fresh: `control {"action": "cycle", "scope": {"folder": "/", "busy": false}}`. A session can restart itself (`control cycle me`): Berth's watcher does it as soon as the turn ends.

## The update routine

Before an update: sleep everything (`Z` in the screen). Update. Wake everything (`W`). Drafts come back; conversations continue. A crash or a cmux quit is handled without asking.

## Attention

The sidebar is the to-do list: Needs you, Question, Permission, and Urgent first; Done means a turn ended that has not been looked at; Ready means idle. Agents raise Urgent only when their instructions say so, with a one-line why.

## Things Dominik has that are not in this kit

He also runs an event system that wakes sessions from Slack messages and schedules (it types a prompt into a sleeping session's box through Berth), a census that keeps a roster of every session, and stewards for his company's repos and containers. They are built on the same pieces (`berth` CLI, `berth mcp`, prompts from files, stop files), and are good examples of what becomes possible once every session is addressable; none is needed to start.
