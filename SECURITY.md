# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.5.x   | ✅        |
| < 0.5   | ❌        |

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

Neither city portal exposes `datastore_search_sql`, so **no SQL string is ever
constructed for them**. Filters are sent as a JSON object that CKAN matches
itself, and column names go into query-string parameters that httpx
percent-encodes before CKAN quotes them against its own schema.

That fact decides the rule for column names, and the rule is deliberately
different from the SoQL one. It began as a character allowlist and was wrong
twice — it refused `MES:` and then `Fecha & Hora`, both real columns on these
portals. Surveying 1,308 live column names showed why an allowlist cannot work
here: a government catalogue publishes headers with `? $ & % ° # : ( ) / . -`,
with accents, and with mojibake from its own encoding bugs (`Correo
electr¢nico`). Enumerating that set is a losing game, and each miss costs a user
a column they needed.

So the CKAN rule is a **denylist**, and a short one — statement and comment
sequences (`;`, `--`, `/*`, `*/`), control characters including newlines, and a
120-character cap. Everything else printable is allowed, because on this path
there is nothing for it to break: a quote-and-OR string reaches CKAN as a field
name that does not exist and comes back as "no such field".

The same string **is** refused by `soql.quote_ident()`, because on the Socrata
path it would be composed into a query language the portal executes. A test
asserts both behaviours together, so that a future reader does not "fix" one
rule to match the other. Sort directions are additionally restricted to
`asc`/`desc`.

Refusing a column costs projection, never access: omitting `columns` returns
every field, including names the rule will not express.

### Outbound requests and the SSRF guard

**This section changed in 0.4 and the change matters.** Through 0.3 this server
contacted only hosts written in its own source, and said so here. That is no
longer true, deliberately.

Two capabilities added in 0.4 follow addresses that come out of a portal
catalogue rather than out of this code. `city_esri_query` and its siblings reach
ArcGIS REST services, which for Bogotá live on `serviciosgis.catastrobogota.gov.co`,
`portalgis.habitatbogota.gov.co` and several more — none of them under
`bogota.gov.co`. `city_read_resource_file` reaches a published file at its own
address; 88% of Bogotá's are on `*.bogota.gov.co` and the rest are not. Refusing
the remainder would have meant dropping real datasets, so instead the address
comes from data and two things stand between the catalogue and the socket.

**First, no tool accepts a URL.** Every one of these tools takes a resource
UUID, and `src/colombian_open_data_mcp/ckan_tools.py` looks the address up
through the portal's own `resource_show`. The set of reachable hosts is
therefore bounded by what a Colombian government catalogue publishes, not by
what an argument can express. A test asserts that no tool signature has a `url`
parameter.

**Second, `netguard.py` enforces a network policy on top of that.** The default
mode, `public-only`, requires the scheme to be http or https and requires
*every* address the hostname resolves to be globally routable — which refuses
loopback, the RFC-1918 ranges, IPv6 unique-local, carrier-grade NAT and the
cloud instance-metadata endpoint at `169.254.169.254`. Resolution happens even
for bare IP literals, so the obfuscated spellings (decimal, octal,
IPv4-mapped-IPv6) are normalised before classification. The guard is installed
as an httpx *request* event hook, which is what makes it validate every redirect
hop rather than only the first request — a URL answering `302 Location:
http://127.0.0.1/` is refused on the hop.

Two settings change the policy:

| `CO_MCP_NETGUARD` | Behaviour |
|---|---|
| `public-only` (default) | Scheme check plus resolve-and-require-globally-routable. |
| `strict` | Additionally the host must match `DEFAULT_STRICT_HOSTS` (the five portals, `*.gov.co`, `*.arcgis.com`) or `CO_MCP_ALLOW_HOSTS`. For hosted deployments. |
| `off` | No checks. Trusted local use and test suites only. |

`CO_MCP_ALLOW_HOSTS` is a comma-separated list of hostnames or `*.` wildcards
that bypass resolution entirely. An unrecognised mode raises rather than falling
back, so a typo on a hosted deployment fails loudly instead of silently
downgrading `strict`.

**Known limit, stated rather than implied away.** The guard resolves the
hostname and httpx resolves it again to connect. An attacker controlling a
domain's DNS who can flip the answer between those two lookups is not blocked.
Closing that window means pinning the validated address at the transport layer;
it is a known residual risk, not an oversight.

**Nothing downloaded is retained.** `city_read_resource_file` streams under a
12 MB cap, parses the first rows, answers and discards the bytes. There is no
cache and no disk state. That is a deliberate limit as much as a performance
choice: persisting these datasets would make this server a *responsable del
tratamiento* under Ley 1581 de 2012 for any of them containing identifiable
people, which is a decision to take explicitly and separately.

### Output bounds

Row limits are clamped server-side in the client (Socrata `$limit` ≤ 1000, CKAN
`limit` ≤ 1000) and descriptions are truncated to 300 characters, so a single
tool call cannot be used to exhaust the model's context.

### Credentials

The only secret this server understands is the optional `SOCRATA_APP_TOKEN`,
which raises Socrata's anonymous rate limit. It is read once at client
construction, sent only as an `X-App-Token` header to `datos.gov.co` and the
Socrata catalog — never to either city portal, which have no equivalent — and
never logged. The server works without it.

## What this server does not protect against

- **The portals' own data.** Dataset contents are public government data
  returned verbatim. A malicious description field reaching the model is a
  prompt-injection vector this server cannot neutralise, only bound.
- **Availability.** Both portals are third-party infrastructure. Bogotá's sits
  behind a WAF that will refuse requests under its own rules.
