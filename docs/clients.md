# Connecting from each MCP client

The server speaks stdio. Every client below launches it the same way — the only
differences are where the configuration file lives and what the key is called.

Nothing here needs an API key. `SOCRATA_APP_TOKEN` is optional and only raises
`datos.gov.co`'s anonymous rate limit; the server works without it.

## Install

Two options. `uvx` needs no install step and always fetches the published
version:

```bash
uvx colombian-open-data-mcp
```

Or install it into an environment you control:

```bash
pip install colombian-open-data-mcp
colombian-open-data-mcp
```

If the command prints its startup banner to stderr and then waits, it is
working. A stdio server with no client attached looks like it has hung; that is
correct behaviour. Press Ctrl-D to exit.

## Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows.

```json
{
  "mcpServers": {
    "colombia": {
      "command": "uvx",
      "args": ["colombian-open-data-mcp"]
    }
  }
}
```

Restart Claude Desktop after editing. The tools appear under the connector icon.

## Claude Code

```bash
claude mcp add colombia -- uvx colombian-open-data-mcp
```

Add `--scope project` to write it into the repository's `.mcp.json` instead of
your user configuration, so collaborators get it too.

## Cursor

`~/.cursor/mcp.json`, or `.cursor/mcp.json` inside a project:

```json
{
  "mcpServers": {
    "colombia": {
      "command": "uvx",
      "args": ["colombian-open-data-mcp"]
    }
  }
}
```

## VS Code (GitHub Copilot agent mode)

`.vscode/mcp.json`:

```json
{
  "servers": {
    "colombia": {
      "type": "stdio",
      "command": "uvx",
      "args": ["colombian-open-data-mcp"]
    }
  }
}
```

## Gemini CLI

`~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "colombia": {
      "command": "uvx",
      "args": ["colombian-open-data-mcp"]
    }
  }
}
```

## Anything else

Any MCP client that can launch a stdio subprocess works. The command is
`uvx colombian-open-data-mcp`, or the absolute path to the installed
`colombian-open-data-mcp` binary if `uvx` is not on the PATH.

## Optional: a Socrata app token

Without a token, `datos.gov.co` throttles by source IP and shares that budget
with everyone else on it. A free token removes that. Register at
<https://evergreen.data.socrata.com/signup> and pass it as an environment
variable:

```json
{
  "mcpServers": {
    "colombia": {
      "command": "uvx",
      "args": ["colombian-open-data-mcp"],
      "env": { "SOCRATA_APP_TOKEN": "your-token" }
    }
  }
}
```

The token is sent only to `datos.gov.co` and Socrata's catalog API, never
logged, and never used for the Bogotá portal — CKAN has no equivalent.

## Troubleshooting

**"An executable named colombian-open-data-mcp is not provided by package…"**
Your `uvx` cache is stale. `uvx --refresh colombian-open-data-mcp`.

**`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`**
You are on a build older than 0.2.0 that resolved MCP SDK 2.x. Upgrade:
`pip install -U colombian-open-data-mcp`.

**A Bogotá tool returns an error mentioning a WAF.**
That portal refuses some programmatic requests by its own rules. It is not a
bug in this server and retrying often works. National-portal tools are
unaffected.

**A Bogotá dataset has no queryable resource.**
Most of that catalogue is published as files rather than through the DataStore,
much of it geospatial: measured over 300 random datasets, about 13% return rows.
`bogota_get_dataset` marks each resource `queryable: true/false`; when it is
false the download URL is all there is.

**And sometimes `queryable: true` is wrong.** The portal's catalogue flags
resources as DataStore-backed that have no table behind them — about a third of
the flagged ones. You will get an error saying exactly that. It is Bogotá's
metadata, not your request.
