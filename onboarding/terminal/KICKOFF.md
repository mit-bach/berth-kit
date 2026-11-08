# Every next chat

The first prompt is in the README at the kit's top level, under "Terminal". At the end of each stage the agent fills this in and gives it to the operator:

```text
Continue my Berth onboarding. The kit is at <kit path>. Read onboarding/terminal/START-HERE.md there, then ~/.berth-onboarding/PROGRESS.md, then do the next stage it names. Ask me before you change anything on my Mac.
```

From stage 2 on, that chat starts inside cmux: a new cmux workspace, `cd` into the kit (the agent says its path), and `claude` there, so the new chat is one that cmux and Berth can see.
