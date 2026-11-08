"""cmux-mcp inside Berth's one shared server (berth mcp --http, 2026-11-06): respond() and a per-request caller."""

from cmux_mcp import berth_api, server


def test_the_caller_is_the_requests_and_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-env")
    assert berth_api.caller_workspace() == "ws-env"  # a stdio server: its own session
    token = berth_api.CALLER.set("ws-request")
    try:
        assert berth_api.caller_workspace() == "ws-request"
    finally:
        berth_api.CALLER.reset(token)
    token = berth_api.CALLER.set("")
    try:
        assert berth_api.caller_workspace() is None  # an empty header: not in cmux, never the server's own env
    finally:
        berth_api.CALLER.reset(token)


def test_respond_answers_without_a_transport():
    ops = object()
    hello = server.respond(ops, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert hello["id"] == 1 and hello["result"]["serverInfo"]["name"]
    assert server.respond(ops, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert len(server.respond(ops, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]) > 10
    assert server.respond(ops, {"jsonrpc": "2.0", "id": 3, "method": "server/discover"})["error"]["code"] == -32601
