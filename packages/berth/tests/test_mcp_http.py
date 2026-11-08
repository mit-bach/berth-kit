"""One shared MCP server for every session (S1, 2026-11-06): berth mcp --http."""

from __future__ import annotations

import json
import sys
import threading
import types
import urllib.error
import urllib.request

import pytest

from berth.mcp_http import HEADER, berth_mount, cmux_mount, make_server, serve_in_thread
from tests.fakes import FakeCmux
from tests.test_state import _insert


@pytest.fixture
def served(catalog):
    _insert(catalog, id="orch", title="Mail Orch", kind="other-agent", agent_name="claude", agent_session_id="s-o",
            state="live", cmux_workspace_id="ws-orch")
    _insert(catalog, id="tutor", title="Tutor", kind="other-agent", agent_name="claude", agent_session_id="s-t",
            state="live", cmux_workspace_id="ws-tutor")
    server = make_server(0, mounts={"/berth": berth_mount(catalog.path, cmux_factory=FakeCmux)})
    serve_in_thread(server)
    yield f"http://127.0.0.1:{server.server_address[1]}", server
    server.shutdown()
    server.server_close()


def _post(url, body, *, headers=None, workspace="ws-orch"):
    data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **({HEADER: workspace} if workspace is not None else {}),
                                              **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None


def _call(url, name, arguments, workspace):
    status, body = _post(url + "/berth", {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                          "params": {"name": name, "arguments": arguments}}, workspace=workspace)
    assert status == 200, body
    return json.loads(body["result"]["content"][0]["text"])


def test_it_speaks_mcp_over_http_without_state(served):
    url, _ = served
    status, body = _post(url + "/berth", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                          "params": {"protocolVersion": "2025-06-18"}})
    assert status == 200 and body["result"]["serverInfo"]["name"] and "Berth" in body["result"]["instructions"]
    status, body = _post(url + "/berth", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert status == 200 and len(body["result"]["tools"]) == 11
    assert _post(url + "/berth", {"jsonrpc": "2.0", "method": "notifications/initialized"})[0] == 202
    status, body = _post(url + "/berth", {"jsonrpc": "2.0", "id": 3, "method": "server/discover"})
    assert status == 200 and body["error"]["code"] == -32601  # Claude Code asks this first, then initializes
    status, body = _post(url + "/berth", [{"jsonrpc": "2.0", "id": 4, "method": "ping"},
                                          {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert status == 200 and [item["id"] for item in body] == [4]


def test_each_request_is_its_own_caller_even_at_once(served):
    url, _ = served
    results = {}

    def ask(workspace):
        answer = _call(url, "status", {}, workspace)
        results[workspace] = answer["you"]["id"] if "you" in answer else answer

    threads = [threading.Thread(target=ask, args=(ws,)) for ws in ("ws-orch", "ws-tutor") * 5]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == {"ws-orch": "orch", "ws-tutor": "tutor"}
    refused = _call(url, "control", {"action": "sleep", "sessions": "me"}, "")  # outside cmux: no "me"
    assert "error" in refused


def test_web_pages_and_strangers_are_refused(served):
    url, server = served
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert _post(url + "/berth", ping, headers={"Origin": "https://evil.example"})[0] == 403
    assert _post(url + "/berth", ping, workspace=None)[0] == 403  # a page can't add the header cross-site
    assert _post(url + "/berth", ping, headers={"Host": f"evil.example:{server.server_address[1]}"})[0] == 403
    assert _post(url + "/nothing", ping)[0] == 404
    with pytest.raises(urllib.error.HTTPError) as got:
        urllib.request.urlopen(url + "/berth", timeout=5)
    assert got.value.code == 405


def test_cmux_mcp_is_served_beside_it_with_its_caller_set_per_request(monkeypatch):
    import contextvars

    seen = []
    caller = contextvars.ContextVar("caller", default=None)
    fake_server = types.SimpleNamespace(respond=lambda ops, message: seen.append(caller.get()) or
                                        {"jsonrpc": "2.0", "id": message.get("id"), "result": {}})
    package = types.ModuleType("cmux_mcp")
    modules = {"cmux_mcp": package, "cmux_mcp.server": fake_server,
               "cmux_mcp.berth_api": types.SimpleNamespace(CALLER=caller),
               "cmux_mcp.client": types.SimpleNamespace(CmuxClient=lambda: None),
               "cmux_mcp.ops": types.SimpleNamespace(CmuxOps=lambda client: None)}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    respond = cmux_mount()
    assert respond("ws-a", {"id": 1, "method": "ping"})["id"] == 1 and seen == ["ws-a"]
    assert caller.get() is None  # reset after the request
    for name in modules:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "cmux-mcp" not in p])
    assert cmux_mount() is None  # not importable: /cmux-mcp is simply not served


def test_its_launchagent_runs_the_shared_server_with_cmux_mcp_on_its_path(tmp_path, monkeypatch):
    from berth import install

    home = tmp_path / "cmux-mcp"
    (home / "cmux_mcp").mkdir(parents=True)
    (home / "cmux_mcp" / "__init__.py").write_text("")
    monkeypatch.setattr(install, "CMUX_MCP_HOME", home)
    plist = install.mcp_plist("/x/.venv/bin/berth", 4792)
    assert plist["Label"] == "com.bach.berth.mcp" and plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    assert plist["ProgramArguments"] == ["/x/.venv/bin/berth", "mcp", "--http", "--port", "4792"]
    assert plist["EnvironmentVariables"]["PYTHONPATH"] == str(home)
    assert plist["StandardErrorPath"].endswith("mcp-http.err")
    assert install.mcp_plist("/x/berth", 4792)["Label"] != install.agent_plist("/x/berth")["Label"]  # apart from the watcher


def test_the_health_check_pings_each_path(served):
    from berth.mcp_http import check

    url, server = served
    health = check(server.server_address[1])
    assert health["berth"] == "up" and health["cmux-mcp"] == "not served"  # this test server mounts /berth only
    assert check(1, timeout=0.2)["berth"] == "down"


def test_berth_mcp_check_reports_and_exits_1_when_nothing_answers(capsys):
    from berth.cli import main

    assert main(["mcp", "--check", "--port", "1"]) == 1  # never starts a stdio server
    assert "berth down" in capsys.readouterr().out


def test_many_first_opens_at_once_never_find_the_catalog_unreadable(tmp_path):
    """2026-11-07, Kit Agent: one status call in ~30 failed "catalog unreadable": two threads both saw a table
    missing and both created it (``table status already exists``). Schema steps are now safe to race."""
    import sqlite3

    from berth.catalog import load
    from berth.db import Catalog
    from berth.sqlite_schema import schema_step

    for round_ in range(5):
        path = tmp_path / f"fresh-{round_}.sqlite"
        Catalog(path).open().close()
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE IF EXISTS status")
        conn.execute("DROP TABLE IF EXISTS runtime")
        conn.commit()
        conn.close()
        errors = []

        def first_look():
            try:
                load(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{exc} <- {exc.__cause__}")

        threads = [threading.Thread(target=first_look) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
    conn = sqlite3.connect(tmp_path / "fresh-0.sqlite")
    schema_step(conn, "ALTER TABLE folder ADD COLUMN probe TEXT")
    schema_step(conn, "ALTER TABLE folder ADD COLUMN probe TEXT")  # the second opener: fine
    with pytest.raises(sqlite3.OperationalError):
        schema_step(conn, "ALTER TABLE nowhere ADD COLUMN x TEXT")  # a real error still raises
