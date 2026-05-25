<!-- mcp-name: io.github.alcastaro/colombian-open-data-mcp -->

**[English](README.md) · [Español](README.es.md)**

---

# colombian-open-data-mcp

**MCP server for [datos.gov.co](https://www.datos.gov.co) — Colombia's open government data portal (10,000+ datasets, Socrata SODA).**

The first MCP server in the official [MCP Registry](https://registry.modelcontextprotocol.io) for Colombia's open data. Connects any MCP-compatible AI assistant (Claude Desktop, Claude Code, Cursor, Gemini CLI, ChatGPT Desktop) directly to the dataset catalog and the live data — including server-side SoQL queries, aggregations, and filters that hit Socrata's database, not the LLM's context.

---

## Why this matters

Colombia's `datos.gov.co` runs **Socrata**, not CKAN. That makes it different from most LatAm portals (Argentina, Chile, Mexico, Uruguay, DR all run CKAN). Socrata has a real query language — SoQL — that lets you `WHERE`, `GROUP BY`, `count()`, `sum()` directly against the data without downloading it. This MCP exposes that power as typed tools so the LLM doesn't need to learn SQL.

## Tools (12)

| Tool | What it does |
|---|---|
| `search_datasets` | Catalog search by keyword, category, tag. |
| `get_dataset` | Full metadata: columns, types, owner, license, URL. |
| `list_recent_datasets` | Most recently updated datasets. |
| `list_categories` | Top-level portal categories. |
| `list_tags` | All tags on the portal. |
| `list_owners` | Publishing entities with dataset counts. |
| `autocomplete` | Resolve partial names for datasets / tags / categories / owners. |
| `get_site_stats` | Portal totals. |
| `download_dataset_preview` | First N rows of a dataset (direct from Socrata, no cache needed). |
| `filter_dataset` | Typed WHERE / SELECT / ORDER BY against any dataset. |
| `aggregate_dataset` | Typed GROUP BY + count / sum / avg / median / min / max / stddev. |
| `query_dataset_soql` | Power-user escape hatch: raw SoQL query against any dataset. |

## Install

```bash
uvx --from git+https://github.com/alcastaro/colombian-open-data-mcp.git colombian-open-data-mcp
```

(PyPI publish will follow the v0.1.0 release.)

## Claude Desktop config

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "colombian-open-data": {
      "command": "/Users/YOUR_USERNAME/.local/bin/uvx",
      "args": [
        "--from",
        "git+https://github.com/alcastaro/colombian-open-data-mcp.git",
        "colombian-open-data-mcp"
      ]
    }
  }
}
```

Cmd+Q (macOS) / Quit from system tray (Windows) and reopen.

## Optional — Socrata App Token

The Socrata API is open without auth but rate-limited. For higher limits, register at https://dev.socrata.com/register and set:

```bash
export SOCRATA_APP_TOKEN="your-token"
```

The MCP reads `SOCRATA_APP_TOKEN` automatically on startup.

## Architecture

```
src/colombian_open_data_mcp/
├── server.py        FastMCP entry + tool definitions
├── socrata.py       Async Socrata client (catalog + view + resource APIs)
└── soql.py          Safe SoQL builder + raw-query validator
```

### Defence in depth

- **Identifier allowlist** + denylist of comment / statement-break substrings on every field name passed to SoQL.
- **Literal escaping** that doubles single quotes properly.
- **Raw SoQL validator** rejects DDL/DML keywords and multi-statement queries before sending.
- **System trust store** via `truststore` for portals with incomplete TLS chains.
- **stderr-only logging** per MCP spec (stdout is the protocol stream).

## Roadmap

This MCP is a **single-country, Socrata-specialized** server. For unified queries across multiple LatAm portals see [`opendata-latam-mcp`](https://github.com/alcastaro/opendata-latam-mcp) (when published) which will integrate this same Socrata client as Colombia's adapter.

## Credits

Built by [@alcastaro](https://github.com/alcastaro). Companion to [`dominican-open-data-mcp`](https://github.com/alcastaro/datos.gob.do-MCP-server) (CKAN-based, single-country) and the future [`opendata-latam-mcp`](https://github.com/alcastaro/opendata-latam-mcp) (multi-country).

Data from datos.gov.co is published by Colombian government institutions and administered by MinTIC.

## License

MIT for the source code. Data accessed through this MCP is subject to each publisher's terms. See [LICENSE](LICENSE).
