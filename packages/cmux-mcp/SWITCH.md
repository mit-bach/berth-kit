Add this server beside cmux-agent-mcp. Do not remove the old server in this step.

```toml
[mcp_servers.cmux-mcp]
command = "python3"
args = ["-m", "cmux_mcp"]
enabled = true
startup_timeout_sec = 30

[mcp_servers.cmux-mcp.env]
PYTHONPATH = "/Users/olduser/FORGE/mcps/cmux-mcp"
```

`python3 -m cmux_mcp` needs that `PYTHONPATH` because the package lives in this directory. Leave `[mcp_servers]` entries for cmux-agent-mcp as they are.
