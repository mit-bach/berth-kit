# Prompts

Read this file before bytes go into an agent's prompt, Claude or Grok. The shapes and examples below are earlier prompts. They are not this turn's job.

## What text is

The file is the job the child will perform. The child treats those bytes as the prompt. A file the child opens later does not replace it.

A path may appear in the file. The file still says what to do, which files come out, and what fails the turn.

Put the operator's words in the file when he said to pass them on word for word. Put the design in the file when he said the prompt has to hold it.

Match the length to the job. A one-file proof is not a design catalog. A design the operator has not written up is not a one-line path.

Copy the shape of these examples. Do not copy their class names, paths, or addresses into a different job.

## Reject

These were sent. Do not send this shape.

- `Read <path> and run that job.`
- `Read <path> then the operator prompt it names. Do that job.`
- `Open <path> and do that job. Then stop.`
- `Read this file and do exactly what it says.`
- `You are <role>. This prompt is Stage N only. Then stop. Wait for the next prompt.`
- `/fork` plus a directive, or `/session-info` before a fork. A fork is `berth` `create` with `from`. The prompt you place on the fork afterwards is only its next task.

## One job

The file names the change, the directory, and what must not happen.

```text
I want one file written, and I want you to stop when it exists.

Write the single word ok on its own line in /absolute/path/STOP.

Do not write any other file. Do not spawn anyone. Do not open a second session.

When that file exists and the line is ok, stop.
```

## The design is the prompt

He said there was no design document. The sent line told the child to read a file. The prompt below is the design.

Sent:

```text
Read this file and do exactly what it says. Do not spawn subagents. Put a tremendous amount of work into it. Implement, test, and prove.

~/projects/GROK/GROK-Workspace/chrome-cdp/prompts/0001.md
```

```text
I want the long-term Chrome fix built, tested, and proved. Not sketched. The parent did not hand you a separate design document. This prompt is the design. The longer research note, if a detail here is not enough, is ~/projects/GROK/GROK-Workspace/chrome-cdp/prompts/0001.md. If that file and this prompt disagree on a measured fact, re-measure. Do not rediscover Chromium, and do not go hunting for a Chrome setting that remembers Allow. That hunt already failed.

You are in your own Grok window. Do the work yourself. Do not spawn anyone.

Write under ~/projects/GROK/GROK-Workspace/chrome-cdp. You may add bin/, docs/, scripts/, a macOS app wrapper, and a LaunchAgent under ~/Library/LaunchAgents if that is what makes the launcher real. Do not edit Operator-Workspace. Do not edit the UNI desks. You may read outlook-export. Do not edit it.

The bug, measured on this Mac on 2026-09-13. Re-measure if you need the live numbers. Do not invent them.

Grok's browser-use plugin and chrome-devtools plugin attach to the Chrome that is already running. Chrome 144 and later then shows a native dialog, Allow remote debugging, with Allow, Cancel, and Turn off in settings. I have to click Allow every time. It is not a website popup. It is a native sheet. An extension cannot see it.

Chrome on that measurement was 152.0.7977.83 at /Applications/Google Chrome.app/Contents/MacOS/Google Chrome. The user-data-dir was the default, ~/Library/Application Support/Google/Chrome. The command line had --no-startup-window and did not have --remote-debugging-port. Port 9222 was listening. DevToolsActivePort was present. Local State had devtools.remote_debugging.user-enabled true. That is approval mode from chrome://inspect/#remote-debugging, not command-line CDP.

There are two different CDP servers. Do not mix them.

Command-line mode is the one I want. Chrome started with --remote-debugging-port, a user-data-dir that is not the default, and --remote-allow-origins=*. /json/version answers immediately. No Allow dialog. Chrome 136 and later ignores --remote-debugging-port if the user-data-dir is the default directory. That is why 9222 is up on this machine and the dialog still exists. Copying the default profile into a new directory will not decrypt cookies. Do not copy it. The new directory starts empty. I sign in when I cut over.

Approval mode is what this machine is in now. Every incoming CDP websocket shows the dialog. Not once per launch. Parallel clients stack dialogs. The request to remember the approval was closed. Enterprise policy RemoteDebuggingAllowed does not skip the dialog. Turning the feature off is not an auto-allow.

Do not build the auto-clicker, the yes-dev path, a Chrome extension that clicks Allow, a cloud browser, or chrome-devtools-mcp's empty isolated profile as the daily browser. The short-term clicker is a real path. I chose the long-term one. Do not install the clicker.

Build Chrome-CDP. That is the only name. Use it in DESIGN.md, the scripts, and the docs.

The daily Chrome is a process whose user-data-dir is ~/Library/Application Support/Google/Chrome-CDP, started only through a wrapper that always passes that user-data-dir, --remote-debugging-port=9222 for the real cutover, and --remote-allow-origins=*. Extra arguments pass through. Plugins attach to http://127.0.0.1:9222 and never open chrome://inspect.

The wrapper uses the absolute Chrome binary. Do not use open -a "Google Chrome". open will reuse the default-profile Chrome and drop the flags. If Chrome-CDP is already running on that user-data-dir, a URL goes to that instance. Do not silently attach to the default profile. Print one status line: pid, port, user-data-dir, and whether curl of /json/version worked. A status subcommand must not launch Chrome. Give me something I can actually launch on this Mac: a script in bin/ on a documented PATH, and an app or Dock-able stub you have tested. Do not force Chrome to start at login. Autostart is documented and off unless you can make it safe.

Port 9222 is already taken by my default-profile Chrome. That Chrome holds the signed-in UNI Outlook session. Do not kill it. Do not crash-test it. Do not reset its profile. For the test, launch Chrome-CDP on 9223, or another free loopback port you prove is free, with the Chrome-CDP user-data-dir. Prove /json/version returns JSON immediately with no click, and that the process command line contains --remote-debugging-port. Prove a second attach still has no dialog. The production wrapper still defaults to 9222. Document the cutover: I quit the default Chrome, then I use the wrapper. Until then, a port flag has to work.

Write DESIGN.md from this. Then wire Grok so browser-use uses BU_CDP_URL against this endpoint and does not open chrome://inspect, and so chrome-devtools attaches with --browser-url to that same endpoint rather than launching an empty Chrome or using --autoConnect on the default profile. Read the live plugin config. Do not guess where the env has to go. If a plugin update would wipe it, put the snippet in the place Grok actually reads and say where.

Both plugins have to be able to use the signed-in browser. An empty profile is not the daily browser. UNI Outlook on the web is one of the things that browser is for.

Get it to a state that works. Test it. Prove the no-dialog attach. Do not stop at a sketch.
```

## Fork

`create` with `from` has already copied the whole conversation into the fork. The file you place on the fork's id is its next task, written like any other job here. It does not start with `/fork`.

Name the fork in the prompt when it takes a different seat. Its title is the one `create` returned: the source title plus ` ⑂`, or the `title` you passed. For a same-task fork, keep the task and change the name, and change a write root or a stop file that contains the source title. The source is not given this file.

```text
You are TITLE ⑂. You keep the history of TITLE. You do that same task under your own name.

Write only under /absolute/path/TITLE-fork.
Write the single word forked on its own line in /absolute/path/TITLE-fork/FORK-STOP.

Do not write in TITLE's directory. Do not spawn anyone. Do not fork.

When that file exists and the line is forked, stop.
```

The calendar text below went to a fork that took a new seat. Before Berth's fork, it was sent as `/fork` plus this text. Now it is placed on the fork's id after `create` with `from` returns.

```text
You are taking the Calendar seat. PRINCIPLES.md is already written. Do not edit it. Do not write Hermes.

I want a complete job-law document that a replacement Calendar window can run, and I want you to think about the end state before you write a paragraph of it. The end state is one Face chat that covers mail and hours. Calendar is the one long-running Google writer after the transfer. Hermes will packet an hour to you. You never send mail. You do not learn durations over time. You do not keep leftover hours, Innovator cost, or buffer-as-a-type. Time Mapper is the shape you are cutting from, not a file you paste.

Read this operator prompt in full before you write:
~/projects/A-OS-V2/Operator-Workspace/Internal AI development/Grok Build UNI Partner/Prompts/Hermes-Logis/Hermes and Calendar document one-shot.md

Also read engineers/iris/PRINCIPLES.md, DESIGN.md, ORCHESTRATION.md, SKILL-SURFACE.md, and TIME-MAPPER.md. Fern is out. Do not go read Fern to be thorough. He is tabled.

Name the packets you write and the packets you accept. Say what Face is allowed to show. Say what you may never do. Say how a mail that names an hour becomes a placement without me having to say "add that."

Put a tremendous amount of effort into this. Think about the system as a whole. Do not lazily clone Time Mapper and delete the sentences you were told to delete. Do not spawn anyone.

Write engineers/iris/CALENDAR.md and that is the write. When that file is a document a new Calendar window could actually run, you are done.
```

