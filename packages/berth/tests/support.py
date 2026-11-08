from datetime import datetime, timezone

from berth.app import App

OPENED = datetime(2026, 10, 2, 21, 0, tzinfo=timezone.utc)
FROZEN = datetime(2026, 10, 2, 21, 30, tzinfo=timezone.utc)


def frozen_clock() -> datetime:
    return FROZEN


def make_app(path, **kwargs) -> App:
    kwargs.setdefault("width", 96)
    kwargs.setdefault("height", 18)
    kwargs.setdefault("opened_at", OPENED)
    kwargs.setdefault("clock", frozen_clock)
    return App(path, **kwargs)


def search(app: App, text: str) -> None:
    app.handle("/")
    for char in text:
        app.handle(char)
    app.handle("enter")


def clear(app: App) -> None:
    app.handle("esc")


def write_claude_conversation(session_id: str, cwd: str, *, root: str = "root-message", title: str | None = None):
    """A transcript with one message, where berth.identity looks for it."""
    import json
    import re

    from berth.identity import claude_projects_root

    folder = claude_projects_root() / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    lines = [{"type": "user", "uuid": root, "cwd": cwd, "timestamp": "2026-10-01T00:00:00.000Z", "sessionId": session_id}]
    if title:
        lines.append({"type": "custom-title", "customTitle": title, "sessionId": session_id})
    path.write_text("\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8")
    return path


def write_claude_process(pid: int, session_id: str, cwd: str, *, kind: str = "interactive", name: str = "", job_id=None):
    import json

    from berth import claude

    directory = claude.sessions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    record = {"pid": pid, "sessionId": session_id, "cwd": cwd, "kind": kind, "name": name, "updatedAt": 1}
    if job_id:
        record["jobId"] = job_id
    (directory / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")
