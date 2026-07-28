# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅        |
| < 0.2   | ❌        |

Always run the latest release. Security fixes land on the newest minor only.

**0.1.0 was never published, and would have been broken on install.** It pinned
`mcp>=1.2.0` with no upper bound, and MCP Python SDK 2.0 removed the
`mcp.server.fastmcp` import path this server depends on. Anyone resolving that
range today gets `mcp` 2.x and a `ModuleNotFoundError` on the first import.
0.2.0 pins `mcp>=1.9.0,<2`.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.

- Open a [GitHub private security advisory](https://github.com/alcastaro/colombian-open-data-mcp/security/advisories/new), or
- Email the maintainer (see the `authors` field in `pyproject.toml`).

Include: affected version, a reproduction, and the impact you observed. Expect
an acknowledgement within a few days. Coordinated disclosure is appreciated.

## Threat model

This is a **local stdio MCP server**. It runs on the user's machine with the
user's privileges and is driven by an LLM. The design assumptions:

1. **The LLM is not trusted to produce safe queries.** Anything a model can put
   into a tool argument is treated as hostile input, because a prompt injection
   in a dataset description is enough to make a model try.
2. **The portals are not trusted to return safe bodies.** A 200 can carry HTML
   from a WAF, a JSON object where an array was promised, or a body large enough
   to exhaust memory. Each is handled explicitly rather than assumed away.
3. **The server never writes anywhere.** Every tool is a GET. There is no cache
   directory, no file download, no credential store.

## Specific defences

### Identifier validation before any URL is built

Dataset and resource identifiers are interpolated into request paths, which
makes them a path-traversal and request-splitting surface.

- **Socrata 4x4 ids** must match `^[a-z0-9]{4}-[a-z0-9]{4}$` exactly
  (`socrata.is_valid_4x4`).
- **CKAN identifiers** must be either a canonical UUID or a URL slug matching
  `^[a-z0-9][a-z0-9._-]{1,99}$` (`ckan.is_valid_dataset_id`); resource ids must
  be UUIDs (`ckan.is_valid_uuid`).

Both gates run **before** the client is touched, so a rejected id never reaches
the network.

### SoQL: allowlist plus denylist, and escaped literals

`datos.gov.co` accepts a real query language, so its inputs get the strictest
handling in the codebase:

- Every column name goes through `soql.quote_ident()`, which enforces an
  allowlist regex **and** rejects `--`, `/*`, `*/` and `;`. The allowlist alone
  is not enough: it has to admit characters that also compose a comment opener.
- Every literal goes through `soql.quote_literal()`, which doubles single
  quotes. Literals are never formatted into a query by hand.
- The raw-query escape hatch (`query_dataset_soql`) runs `soql.validate_soql()`,
  which rejects every keyword in the write denylist (INSERT, UPDATE, DELETE,
  DROP, CREATE, ALTER, COPY, ATTACH, PRAGMA and others) and rejects
  multi-statement queries.
- At the HTTP layer the server only ever issues GET, so even a bypass of the
  above could not reach Socrata's authenticated write endpoints.

### CKAN: structured filters, never interpolated SQL

Bogotá's DataStore does **not** expose `datastore_search_sql`, so no SQL string
is ever constructed for that portal. Filters are sent as a JSON object that
CKAN matches itself. Column names in `fields`, `filters` and `sort` are still
allowlist-checked and denylist-checked (`ckan.is_valid_column`), and sort
directions are restricted to `asc`/`desc` — defence in depth for a surface that
should already be closed.

### No SSRF surface

This is worth stating explicitly because the sibling Dominican server needs a
whole SSRF guard module and this one does not.

That server downloads CKAN resource files, which live on arbitrary ministry
hosts, so it must resolve every hostname and refuse link-local, loopback and
RFC-1918 addresses. This server never downloads a resource file. Every outbound
request goes to one of three fixed hosts baked into the source:
`api.us.socrata.com`, `www.datos.gov.co`, `datosabiertos.bogota.gov.co`. No
tool takes a URL.

### Output bounds

Row limits are clamped server-side in the client (Socrata `$limit` ≤ 1000, CKAN
`limit` ≤ 1000) and descriptions are truncated to 300 characters, so a single
tool call cannot be used to exhaust the model's context.

### Credentials

The only secret this server understands is the optional `SOCRATA_APP_TOKEN`,
which raises Socrata's anonymous rate limit. It is read once at client
construction, sent only as an `X-App-Token` header to `datos.gov.co` and the
Socrata catalog, and never logged. The server works without it.

## What this server does not protect against

- **The portals' own data.** Dataset contents are public government data
  returned verbatim. A malicious description field reaching the model is a
  prompt-injection vector this server cannot neutralise, only bound.
- **Availability.** Both portals are third-party infrastructure. Bogotá's sits
  behind a WAF that will refuse requests under its own rules.
