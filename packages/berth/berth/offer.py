"""A read-only Berth catalog on the tailnet, and a merge that only adds.

The snapshot drops cmux workspace ids. A live row is exported as parked.
Merging creates missing folders and parked rows. It does not close a
workspace, delete a local row, or replace a row that is already here.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from berth.db import BERTH_COLUMNS, OPTIONAL_COLUMNS, Catalog
from berth.errors import BerthError
from berth.model import INBOX_NAME
from berth.ops import find_folder_path, folder_path, mkdir_path
from berth.timeutil import utc_now

OFFER_PORT = 4791
_EXCERPT_LIMIT = 4000
_KINDS = frozenset({"grok", "other-agent", "shell", "process"})
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _norm_path(path: str) -> str:
    return "/".join(part for part in (path or "").split("/") if part)


def _color(value: object) -> str | None:
    if isinstance(value, str) and _COLOR.match(value.strip()):
        return value.strip().lower()
    return None


def _argv(raw: object) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def session_hint(row: dict) -> str:
    """Where the session files live. The catalog merge does not copy them."""
    session_id = row.get("agent_session_id") or ""
    if not isinstance(session_id, str) or not session_id.strip():
        return ""
    session_id = session_id.strip()
    cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else ""
    agent = (row.get("agent_name") or "").casefold()
    kind = row.get("kind")
    if kind == "grok":
        encoded = quote(cwd or "/", safe="")
        return f"~/.grok/sessions/{encoded}/{session_id}"
    if agent == "claude":
        encoded = re.sub(r"[^A-Za-z0-9]", "-", cwd or "")
        return f"~/.claude/projects/{encoded}/{session_id}.jsonl"
    if agent == "cursor":
        return f"~/.cursor/chats/*/{session_id}/meta.json"
    if agent == "codex":
        return f"~/.codex thread {session_id}"
    if agent == "antigravity":
        return f"~/.gemini/antigravity-cli/conversations/{session_id}.db"
    return session_id


def snapshot(db: Catalog, *, host: str = "") -> dict:
    """Portable folders and parked rows. Workspace ids stay on this Mac."""
    folders = []
    for row in db.all_folders():
        path = folder_path(db, row["id"])
        if not path:
            continue
        folders.append(
            {
                "path": path,
                "note": row["note"] or "",
                "archived": bool(row["archived"]),
                "default_cwd": row["default_cwd"],
                "color": _color(row["color"]) if "color" in row.keys() else None,
            }
        )
    folders.sort(key=lambda item: item["path"])
    berths = []
    for row in db.all_berths():
        if row["kind"] == "berth" or row["kind"] not in _KINDS:
            continue
        path = folder_path(db, row["folder_id"])
        if not path:
            continue
        excerpt = (row["excerpt"] or "")[:_EXCERPT_LIMIT]
        berths.append(
            {
                "id": row["id"],
                "folder": path,
                "title": row["title"],
                "kind": row["kind"],
                "agent_name": row["agent_name"],
                "agent_session_id": row["agent_session_id"],
                "agent_version": row["agent_version"],
                "cwd": row["cwd"],
                "recipe_argv": _argv(row["recipe_argv"]),
                "recipe_display": row["recipe_display"] or "",
                "note": row["note"] or "",
                "protect": bool(row["protect"]),
                "excerpt": excerpt,
                "state": "parked",
                "session_hint": session_hint(
                    {
                        "kind": row["kind"],
                        "agent_name": row["agent_name"],
                        "agent_session_id": row["agent_session_id"],
                        "cwd": row["cwd"],
                    }
                ),
            }
        )
    berths.sort(key=lambda item: (item["folder"], item["title"], item["id"]))
    return {
        "version": 1,
        "host": host or socket.gethostname(),
        "folders": folders,
        "berths": berths,
    }


def _chosen(path: str, folders: list[str] | None, take_all: bool) -> bool:
    if take_all:
        return True
    if not folders:
        return False
    for item in folders:
        norm = _norm_path(item)
        if path == norm or path.startswith(norm + "/"):
            return True
    return False


def _local_sessions(db: Catalog) -> dict[str, str]:
    found: dict[str, str] = {}
    for row in db.all_berths():
        session_id = row["agent_session_id"]
        if isinstance(session_id, str) and session_id:
            found[session_id] = row["id"]
    return found


def plan(db: Catalog, incoming: dict, *, folders: list[str] | None = None, take_all: bool = False) -> dict:
    """Say what a merge would add. Nothing is written."""
    if incoming.get("version") != 1 or not isinstance(incoming.get("folders"), list):
        raise BerthError("NOT_FOUND", "that snapshot is not a Berth offer", {})
    known_paths = {_norm_path(item.get("path") or "") for item in incoming["folders"] if isinstance(item, dict)}
    if folders and not take_all:
        missing = [_norm_path(item) for item in folders if _norm_path(item) not in known_paths]
        if missing:
            raise BerthError("NOT_FOUND", f"no folder {missing[0]}", {"folder": missing[0]})
    local_ids = {row["id"] for row in db.all_berths()}
    sessions = _local_sessions(db)
    local_folders = {folder_path(db, row["id"]): row for row in db.all_folders()}
    folder_rows = []
    for item in incoming["folders"]:
        if not isinstance(item, dict):
            continue
        path = _norm_path(item.get("path") or "")
        if not path:
            continue
        selected = _chosen(path, folders, take_all)
        local = local_folders.get(path)
        color = _color(item.get("color"))
        if local is None:
            color_action = "set" if color else "none"
            action = "add"
        else:
            action = "keep"
            local_color = _color(local["color"]) if "color" in local.keys() else None
            color_action = "set" if color and not local_color else "keep" if color else "none"
        folder_rows.append(
            {
                "path": path,
                "action": action,
                "selected": selected,
                "color": color,
                "color_action": color_action if selected else "none",
                "archived": bool(item.get("archived")),
            }
        )
    berth_rows = []
    for item in incoming.get("berths") or []:
        if not isinstance(item, dict):
            continue
        path = _norm_path(item.get("folder") or "")
        selected = _chosen(path, folders, take_all)
        berth_id = item.get("id") if isinstance(item.get("id"), str) else ""
        session_id = item.get("agent_session_id") if isinstance(item.get("agent_session_id"), str) else ""
        if not selected:
            action = "skip"
            reason = "folder not chosen"
        elif not berth_id or item.get("kind") not in _KINDS:
            action = "skip"
            reason = "unusable row"
        elif berth_id in local_ids:
            action = "keep"
            reason = "that id is already here"
        elif session_id and session_id in sessions:
            action = "session_kept"
            reason = "that session id is already on a row"
        else:
            action = "add"
            reason = ""
        berth_rows.append(
            {
                "id": berth_id,
                "folder": path,
                "title": item.get("title") or "",
                "kind": item.get("kind") or "",
                "agent_name": item.get("agent_name"),
                "session_id": session_id,
                "session_hint": item.get("session_hint") or session_hint(item),
                "selected": selected,
                "action": action,
                "reason": reason,
            }
        )
    return {
        "host": incoming.get("host") or "",
        "folders": folder_rows,
        "berths": berth_rows,
        "add": sum(1 for row in berth_rows if row["action"] == "add"),
        "keep": sum(1 for row in berth_rows if row["action"] == "keep"),
    }


def merge(db: Catalog, incoming: dict, *, folders: list[str] | None = None, take_all: bool = False) -> dict:
    """Add the chosen folders and their parked rows. Local rows stay."""
    if not take_all and not folders:
        raise BerthError(
            "NOT_FOUND",
            "name the folders to add, or pass all",
            {},
        )
    report = plan(db, incoming, folders=folders, take_all=take_all)
    db.ensure_inbox()
    folder_ids: dict[str, str] = {}
    for item in report["folders"]:
        if not item["selected"]:
            continue
        path = item["path"]
        if path == INBOX_NAME:
            folder_ids[path] = db.ensure_inbox()
        else:
            made = mkdir_path(db, path, inbox_children=True)
            folder_ids[path] = made["id"]
        _fill_folder(db, incoming, path, folder_ids[path], item)
    added = []
    by_id = {item.get("id"): item for item in incoming.get("berths") or [] if isinstance(item, dict)}
    for row in report["berths"]:
        if row["action"] != "add":
            continue
        source = by_id.get(row["id"])
        if source is None:
            continue
        folder_id = folder_ids.get(row["folder"])
        if folder_id is None:
            found = find_folder_path(db, row["folder"])
            if found is None:
                continue
            folder_id = found
        _insert_parked(db, source, folder_id)
        added.append(row["id"])
    report["added"] = added
    return report


def _source_folder(incoming: dict, path: str) -> dict | None:
    for item in incoming.get("folders") or []:
        if isinstance(item, dict) and _norm_path(item.get("path") or "") == path:
            return item
    return None


def _fill_folder(db: Catalog, incoming: dict, path: str, folder_id: str, planned: dict) -> None:
    source = _source_folder(incoming, path)
    if source is None:
        return
    row = db.folder_by_id(folder_id)
    if row is None:
        return
    fields: list[tuple[str, object]] = []
    if planned["color_action"] == "set" and planned["color"]:
        fields.append(("color", planned["color"]))
    if not (row["note"] or "") and isinstance(source.get("note"), str) and source["note"].strip():
        fields.append(("note", source["note"].strip()))
    if not row["default_cwd"] and isinstance(source.get("default_cwd"), str) and source["default_cwd"].strip():
        fields.append(("default_cwd", source["default_cwd"].strip()))
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name, _value in fields)
    values = [value for _name, value in fields] + [folder_id]
    with db.immediate():
        db.conn.execute(f"UPDATE folder SET {assignments} WHERE id = ?", values)


def _insert_parked(db: Catalog, source: dict, folder_id: str) -> None:
    stamp = utc_now()
    excerpt = source.get("excerpt") if isinstance(source.get("excerpt"), str) else ""
    excerpt = excerpt[:_EXCERPT_LIMIT]
    argv = _argv(source.get("recipe_argv"))
    display = source.get("recipe_display") if isinstance(source.get("recipe_display"), str) else ""
    if not display:
        display = " ".join(str(part) for part in argv)
    session_id = source.get("agent_session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = None
    fields = {
        "id": source["id"],
        "folder_id": folder_id,
        "title": source.get("title") or "untitled",
        "kind": source["kind"],
        "state": "parked",
        "protect": 1 if source.get("protect") else 0,
        "cwd": source.get("cwd") if isinstance(source.get("cwd"), str) and source["cwd"] else "/",
        "note": source.get("note") if isinstance(source.get("note"), str) else "",
        "recipe_argv": json.dumps(argv),
        "recipe_display": display,
        "agent_name": source.get("agent_name") if isinstance(source.get("agent_name"), str) else None,
        "agent_session_id": session_id,
        "agent_version": source.get("agent_version") if isinstance(source.get("agent_version"), str) else None,
        "cmux_workspace_id": None,
        "cmux_surface_id": None,
        "cmux_pid": None,
        "attachments": "{}",
        "excerpt": excerpt,
        "excerpt_at": stamp if excerpt else None,
        "created_at": stamp,
        "updated_at": stamp,
        "parked_at": stamp,
        "played_at": None,
    }
    missing = [name for name in BERTH_COLUMNS if name not in fields and name not in OPTIONAL_COLUMNS]
    if missing:
        raise RuntimeError(f"berth insert missing {missing}")
    from berth import machine

    fields["park_reason"] = "merged"
    machine.insert(
        db,
        fields,
        event="import",
        log_kind="merge",
        detail={"folder": folder_path(db, folder_id), "via": "offer"},
    )


def format_plan(report: dict) -> str:
    lines = [f"host {report.get('host') or ''}"]
    for folder in report.get("folders") or []:
        if not folder.get("selected") and folder.get("action") == "keep":
            continue
        mark = "chosen" if folder.get("selected") else "not chosen"
        lines.append(f"folder {folder['path']}  {folder['action']}  {mark}")
    for row in report.get("berths") or []:
        if not row.get("selected"):
            continue
        hint = f"  {row['session_hint']}" if row.get("session_hint") else ""
        lines.append(f"  {row['action']}  {row['title']}  {row['kind']}{hint}")
    lines.append(f"add {report.get('add', 0)}  keep {report.get('keep', 0)}")
    return "\n".join(lines)


def make_server(catalog: Path, host: str = "127.0.0.1", port: int = OFFER_PORT) -> ThreadingHTTPServer:
    catalog_path = Path(catalog)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/v1/health":
                body = {"ok": True, "port": self.server.server_address[1]}
            elif path == "/v1/snapshot":
                db = Catalog(catalog_path).open()
                try:
                    body = snapshot(db)
                finally:
                    db.close()
            else:
                self.send_error(404)
                return
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def self_tailscale_ip() -> str:
    try:
        data = tailscale_status()
    except BerthError:
        return ""
    info = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    addresses = info.get("TailscaleIPs") if isinstance(info.get("TailscaleIPs"), list) else []
    for address in addresses:
        if isinstance(address, str) and "." in address:
            return address
    return ""


def serve_forever(catalog: Path, port: int = OFFER_PORT) -> None:
    servers = [make_server(catalog, port=port)]
    address = self_tailscale_ip()
    if address:
        try:
            servers.append(make_server(catalog, host=address, port=port))
        except OSError:
            address = ""
    pid_path().write_text(str(os.getpid()), encoding="utf-8")
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def pid_path() -> Path:
    directory = Path.home() / ".local" / "share" / "berth"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    return directory / "offer.pid"


def log_path() -> Path:
    return pid_path().parent / "offer.log"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def start_offer(catalog: Path, port: int = OFFER_PORT) -> int:
    """Start the offer if it is not already listening. Return the pid."""
    path = pid_path()
    if path.is_file():
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if pid and _pid_alive(pid):
            return pid
    handle = log_path().open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "berth", "offer", "--serve", "--port", str(port), "--catalog", str(catalog)],
        start_new_session=True,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    return process.pid


def tailscale_status() -> dict:
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise BerthError("CMUX_DOWN", "tailscale is not installed", {}) from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tailscale status failed").strip()
        raise BerthError("CMUX_DOWN", message, {})
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise BerthError("CMUX_DOWN", "tailscale status was not JSON", {}) from exc
    return data if isinstance(data, dict) else {}


def peers() -> list[dict]:
    """Other machines on this tailnet. This Mac is not in the list."""
    data = tailscale_status()
    found = []
    peer_map = data.get("Peer") if isinstance(data.get("Peer"), dict) else {}
    for item in peer_map.values():
        if not isinstance(item, dict):
            continue
        name = item.get("DNSName") or item.get("HostName") or ""
        if isinstance(name, str):
            name = name.rstrip(".")
        found.append(
            {
                "name": name,
                "host": item.get("HostName") or "",
                "address": (item.get("TailscaleIPs") or [""])[0],
                "online": bool(item.get("Online")),
            }
        )
    found.sort(key=lambda row: row["name"])
    return found


def peer_snapshot_url(name: str) -> str:
    text = name.strip().rstrip("/")
    if text.startswith("http://") or text.startswith("https://"):
        if text.endswith("/v1/snapshot"):
            return text
        return text + "/v1/snapshot"
    if "/" in text or text.endswith(".json"):
        return text
    host = text if "." in text else text
    if not host.endswith(".ts.net"):
        data = tailscale_status()
        self_name = ""
        self_info = data.get("Self") if isinstance(data.get("Self"), dict) else {}
        dns = self_info.get("DNSName") or ""
        if isinstance(dns, str) and ".ts.net" in dns:
            suffix = dns.rstrip(".").split(".", 1)[1]
            host = f"{text}.{suffix}"
    return f"https://{host}/v1/snapshot"


def _peer_address(spec: str) -> str:
    text = spec.strip()
    if text.startswith("http://") or text.startswith("https://") or "/" in text:
        return ""
    try:
        data = tailscale_status()
    except BerthError:
        return ""
    want = text.casefold().rstrip(".")
    peer_map = data.get("Peer") if isinstance(data.get("Peer"), dict) else {}
    items = list(peer_map.values())
    self_info = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    if self_info:
        items.append(self_info)
    for item in items:
        if not isinstance(item, dict):
            continue
        host = str(item.get("HostName") or "").casefold()
        dns = str(item.get("DNSName") or "").casefold().rstrip(".")
        if want not in {host, dns} and not dns.startswith(want + "."):
            continue
        for address in item.get("TailscaleIPs") or []:
            if isinstance(address, str) and "." in address:
                return address
    return ""


def fetch_snapshot(spec: str, *, opener=None) -> dict:
    """Load a snapshot from a peer name, an https URL, or a JSON file."""
    path = Path(spec).expanduser()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BerthError("NOT_FOUND", "that snapshot file cannot be read", {}) from exc
        if not isinstance(data, dict):
            raise BerthError("NOT_FOUND", "that snapshot is not an object", {})
        return data
    url = peer_snapshot_url(spec)
    if url.startswith("/") or url.endswith(".json"):
        raise BerthError("NOT_FOUND", f"no snapshot {spec}", {"from": spec})
    urls = [url]
    address = _peer_address(spec)
    if address:
        urls.append(f"http://{address}:{OFFER_PORT}/v1/snapshot")
    read = opener or urlopen
    last_error: OSError | None = None
    raw = b""
    url = urls[0]
    for candidate in urls:
        try:
            with read(candidate, timeout=15) as response:
                raw = response.read()
            url = candidate
            last_error = None
            break
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise BerthError("CMUX_DOWN", f"cannot read {url}", {"url": url}) from last_error
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BerthError("NOT_FOUND", "the offer was not JSON", {"url": url}) from exc
    if not isinstance(data, dict):
        raise BerthError("NOT_FOUND", "the offer was not an object", {"url": url})
    return data


def enable_tailscale(port: int = OFFER_PORT) -> str:
    """Ask Tailscale Serve to proxy the offer. A tailnet that has not turned Serve on is left alone."""
    try:
        result = subprocess.run(
            ["tailscale", "serve", "--bg", str(port)],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except OSError as exc:
        raise BerthError("CMUX_DOWN", "tailscale is not installed", {}) from exc
    except subprocess.TimeoutExpired as exc:
        message = ""
        if exc.stdout:
            message = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", "replace")
        return message.strip() or "Tailscale Serve did not finish"
    message = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return message or "Tailscale Serve is not enabled"
    return message


def publish(catalog: Path, port: int = OFFER_PORT) -> dict:
    pid = start_offer(catalog, port)
    message = enable_tailscale(port)
    address = self_tailscale_ip()
    self_name = ""
    try:
        data = tailscale_status()
        info = data.get("Self") if isinstance(data.get("Self"), dict) else {}
        dns = info.get("DNSName") or ""
        if isinstance(dns, str):
            self_name = dns.rstrip(".")
    except BerthError:
        self_name = ""
    https = f"https://{self_name}/v1/snapshot" if self_name else ""
    direct = f"http://{address}:{port}/v1/snapshot" if address else ""
    blocked = "not enabled" in message.lower() or "to enable" in message.lower() or "did not finish" in message.lower()
    return {
        "pid": pid,
        "port": port,
        "url": direct if blocked or not https else https,
        "direct": direct,
        "https": https,
        "serve": message,
    }
