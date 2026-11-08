// Berth's cmux sidebar: one row per workspace, one status, one color (2026-10-29).
//
// The status is Berth's, not cmux's: Berth's watcher reads every pane
// (berth/activity.py) and writes one state per workspace into its progress
// label, "berth1|state|label|folder|mode|epoch" (berth/cmux_sidebar.py). A
// custom sidebar cannot read status lines, and nothing but `cmux set-progress`
// writes progress, so the label is Berth's to use.
//
//   red     Urgent                 it said it needs you urgently; listed first, under Urgent
//   brown   API error              its turn ended on an API error (no network, overloaded); a prompt resumes it
//   magenta Follow up              its turn ended but the work did not (a backlog, or something for you);
//                                  it said so, and it stays in its place until your next prompt
//   amber   Question, Permission, Plan to approve, Needs you, Unclear
//                                  Claude stopped and asks you; go and answer it
//   cyan    Answering /btw, /btw answered
//                                  a side question: being answered (blinking), then open to read
//   green   Done (unread), Ready   your turn; Done is a turn you have not seen
//   blue    Working, Running ...   a turn (with or without subagents) or a command is running
//   purple  Waiting on 2 subagents (blinking), 1 shell
//                                  its turn ended, work it started still runs; it comes back
//   gray    Shell, Menu open, Starting, Usage limit
//   yellow  Berth (a square)       the Berth screen
//
// A workspace Berth does not hold falls back to cmux's own agent status.
// In the "folders" order (the Berth screen's o) a heading names each folder.
// Rows keep cmux's order, which Berth's watcher sets, so there is no dragging.
//
// Installed by `berth sidebar install` as a link to this file in
// ~/.config/cmux/sidebars/berth.js; pick it with `cmux sidebar select berth`.

const LOOK = {
  urgent: { color: "#FF453A", talk: true },
  follow_up: { color: "#FF2DD1", talk: true },
  api_error: { color: "#AC8E68", talk: true },
  needs_you: { color: "#FF9F0A", talk: true },
  unclear: { color: "#FF9F0A", talk: true },
  ready: { color: "#30D158", talk: true },
  btw_answered: { color: "#64D2FF", talk: true },
  btw_answering: { color: "#64D2FF", pulse: true },
  working: { color: "#0A84FF", pulse: true },
  running: { color: "#0A84FF", pulse: true },
  background: { color: "#BF5AF2" },
  limited: { color: "#8E8E93" },
  shell: { color: "#8E8E93" },
  menu: { color: "#8E8E93" },
  starting: { color: "#8E8E93" },
  screen: { color: "#FFD60A", square: true },
  screen_twice: { color: "#FF453A", square: true },
  none: { color: "#636366" },
};
// No word from Berth's watcher for this long: its statuses may be old.
const STALE_SECONDS = 90;
// A working dot blinks on and off with cmux's clock, once a second. (A smooth fade cannot be had:
// cmux's sidebar does not animate opacity and gives a sidebar no timers. Stepped fading was tried
// and dropped, 2026-10-29.)
function blink() {
  return (data.clock()?.second ?? 0) % 2 === 1 ? 0.45 : 1;
}

function berthOf(w) {
  const label = w && w.progress && w.progress.label;
  if (!label || !label.startsWith("berth1|")) return null;
  const [, state, text, folder, mode, epoch, pin] = label.split("|");
  return { state: LOOK[state] ? state : "none", text: text || "", folder: folder || "", mode: mode || "tree",
           epoch: Number(epoch) || 0, pin: pin === "1" };
}

function fallbackOf(w) {
  const agent = ((w && w.agents) || [])[0];
  const status = agent ? agent.status : "";
  if (status === "needs_input") return { state: "needs_you", text: "Needs you" };
  if (status === "working") return { state: "working", text: "Working" };
  if (status === "idle") return { state: "ready", text: "Ready" };
  return { state: "none", text: "" };
}

const beat = computed(() => {
  let newest = 0;
  for (const w of data.workspaces() ?? []) newest = Math.max(newest, berthOf(w)?.epoch ?? 0);
  return newest;
});

function stale() {
  const now = data.clock()?.epoch ?? 0;
  return beat() === 0 || (now > 0 && now - beat() > STALE_SECONDS);
}

function statusOf(w) {
  if (!w) return { state: "none", text: "", color: LOOK.none.color };
  const b = berthOf(w) ?? fallbackOf(w);
  let text = b.text;
  const unseen = b.state === "ready" && w.unread > 0;
  if (unseen) text = "Done";
  const look = LOOK[b.state] ?? LOOK.none;
  // Waiting on subagents blinks too: work is going on, just not in main (2026-10-30). Waiting on shells does not.
  const pulse = !!look.pulse || (b.state === "background" && text.includes("subagent"));
  return { state: b.state, text, color: look.color, talk: !!look.talk, pulse, unseen, ring: b.state === "ready" && !unseen,
           square: !!look.square };
}

// Rows, with a folder heading over each folder's sessions in the "folders" order. Urgent sessions come
// first, under their own heading, whatever the order: they are the ones that got lost among the ready.
// The "folders" order (the Berth screen's o): a heading over each folder, and the pinned ones under "Pinned".
const foldersMode = computed(() => (data.workspaces() ?? []).some((w) => berthOf(w)?.mode === "folders"));

// Berth's pin (berth/pins.py): the only pin. cmux's own is cleared by Berth; a workspace Berth doesn't know keeps it.
function pinnedOf(w) {
  const b = berthOf(w);
  return b ? b.pin : !!(w && w.pinned);
}

const entries = computed(() => {
  const all = data.workspaces() ?? [];
  const urgent = all.filter((w) => berthOf(w)?.state === "urgent");
  const ws = all.filter((w) => berthOf(w)?.state !== "urgent");
  const folders = foldersMode();
  const out = [];
  if (urgent.length > 0) {
    out.push({ kind: "heading", id: "h:Urgent", text: "Urgent" });
    for (const w of urgent) out.push({ kind: "row", id: w.id });
  }
  if (!folders) {
    for (const w of ws) out.push({ kind: "row", id: w.id });
    return out;
  }
  // Each folder once, its rows together, in the order cmux lists them (2026-11-03). A cycled session's new
  // workspace lands out of its folder's run until Berth's watcher reorders cmux, and the folder showed twice.
  const groups = new Map();
  for (const w of ws) {
    const heading = pinnedOf(w) ? "Pinned" : (berthOf(w)?.folder || "Not in Berth");
    if (!groups.has(heading)) groups.set(heading, []);
    groups.get(heading).push(w);
  }
  for (const [heading, rows] of groups) {
    out.push({ kind: "heading", id: "h:" + heading, text: heading });
    for (const w of rows) out.push({ kind: "row", id: w.id });
  }
  return out;
});

const counts = computed(() => {
  const c = { urgent: 0, follow: 0, apiError: 0, you: 0, btw: 0, ready: 0, busy: 0, waiting: 0 };
  for (const w of data.workspaces() ?? []) {
    const s = statusOf(w);
    if (s.state === "urgent") c.urgent += 1;
    else if (s.state === "follow_up") c.follow += 1;
    else if (s.state === "api_error") c.apiError += 1;
    else if (s.state === "needs_you" || s.state === "unclear") c.you += 1;
    else if (s.state === "btw_answered") c.btw += 1;
    else if (s.state === "ready") c.ready += 1;
    else if (s.state === "working" || s.state === "running" || s.state === "btw_answering") c.busy += 1;
    else if (s.state === "background") c.waiting += 1;
  }
  return c;
});

function select(id) {
  if (id) cmux("workspace.select", { workspace_id: id });
}

function count(n, label, color) {
  return Text(() => (n() > 0 ? n() + " " + label : ""))
    .font(11).weight("semibold").color(color).lineLimit(1).fixedSize();
}

function summary() {
  return HStack({ spacing: 10 }, [
    count(() => counts().urgent, "urgent", LOOK.urgent.color),
    count(() => counts().follow, "follow up", LOOK.follow_up.color),
    count(() => counts().apiError, "API error", LOOK.api_error.color),
    count(() => counts().you, "need you", LOOK.needs_you.color),
    count(() => counts().btw, "/btw", LOOK.btw_answered.color),
    count(() => counts().ready, "ready", LOOK.ready.color),
    count(() => counts().busy, "working", LOOK.working.color),
    count(() => counts().waiting, "waiting", LOOK.background.color),
    Spacer(),
  ])
    .paddingHorizontal(12)
    .paddingVertical(4)
    .frame({ maxWidth: "infinity" });
}

function warning() {
  return Text(() => (stale() ? "Berth's watcher is not writing statuses: they may be old" : ""))
    .font(11).color(LOOK.needs_you.color).lineLimit(2)
    .paddingHorizontal(() => (stale() ? 12 : 0))
    .paddingVertical(() => (stale() ? 4 : 0));
}

function heading(entry) {
  return HStack({ spacing: 0 }, [
    Text(() => entry().text).font(10).weight("semibold").color("tertiary").lineLimit(1).truncation("head"),
    Spacer({ minLength: 0 }),
  ])
    .paddingHorizontal(12)
    .paddingTop(8)
    .paddingBottom(1)
    .frame({ maxWidth: "infinity" });
}

function row(entry) {
  const w = () => (data.workspaces() ?? []).find((x) => x.id === entry().id);
  const s = () => statusOf(w());
  const dim = () => (stale() && berthOf(w()) ? 0.4 : 1);
  return HStack({ spacing: 8 }, [
    Capsule({ width: 3, height: 16 }).fill(() => w()?.color ?? "#00000000"),
    // A dot for a session; the Berth screen gets a square, since it is not one.
    ZStack({}, [
      Circle({ size: 9 })
        .fill(() => (s().ring ? "#00000000" : s().color))
        .stroke(() => s().color)
        .strokeWidth(1.5)
        .opacity(() => (s().square ? 0 : (s().pulse ? blink() : dim()))),
      RoundedRectangle({ width: 9, height: 9, cornerRadius: 2 })
        .fill(() => s().color)
        .opacity(() => (s().square ? 1 : 0)),
    ]),
    Text(() => w()?.title ?? "")
      .font(13)
      .weight(() => (s().talk && !s().ring ? "semibold" : "regular"))
      .lineLimit(1)
      .truncation("tail")
      .color(() => (w()?.selected || (s().talk && !s().ring) ? "primary" : "secondary")),
    Spacer({ minLength: 4 }),
    // Under the "Pinned" heading a pin says nothing more (Dominik, 2026-10-30); in the tree order there is no heading.
    Image("pin.fill").font(8).color("tertiary").opacity(() => (pinnedOf(w()) && !foldersMode() ? 1 : 0)),
    Text(() => s().text)
      .font(11)
      .weight(() => (s().talk && !s().ring ? "semibold" : "regular"))
      .color(() => s().color)
      .opacity(dim)
      .lineLimit(1)
      .fixedSize()
      .layoutPriority(2),
  ])
    .paddingLeading(6)
    .paddingTrailing(10)
    .paddingVertical(5)
    .cornerRadius(7)
    .background(() => (w()?.selected ? "#7f7f7f3d" : null))
    .hoverBackground(() => (w()?.selected ? "#7f7f7f3d" : "#7f7f7f24"))
    .frame({ maxWidth: "infinity" })
    .cursor("pointer")
    .onTap(() => select(w()?.id))
    .contextMenu([
      // cmux's pin is a request to Berth, which turns its own pin over and clears cmux's (berth/pins.py).
      Button(() => (pinnedOf(w()) ? "Unpin" : "Pin"), () =>
        cmux("workspace.action", { action: "pin", workspace_id: w().id })),
      Button(() => (w()?.unread > 0 ? "Mark as Read" : "Mark as Unread"), () =>
        cmux("workspace.action", { action: w()?.unread > 0 ? "mark_read" : "mark_unread", workspace_id: w().id })),
      Divider(),
      Button("Close", () => cmux("workspace.close", { workspace_id: w().id })).destructive(),
    ]);
}

sidebar(() =>
  VStack({ spacing: 1 }, [
    warning(),
    summary(),
    ForEach({ items: entries, key: (e) => e.id }, (e) => (e().kind === "heading" ? heading(e) : row(e))),
  ])
)
