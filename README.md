<!-- mcp-name: io.github.alcastaro/colombian-open-data-mcp -->

**[English](README.md) · [Español](README.es.md)**

---

# colombian-open-data-mcp

**MCP server for Colombia's open government data — three portals, two
platforms: the national [datos.gov.co](https://www.datos.gov.co) (Socrata,
8,391 datasets), [Bogotá](https://datosabiertos.bogota.gov.co) (CKAN, ~1,900)
and [Cali](https://datos.cali.gov.co) (CKAN, 657).**

The first MCP server for Colombian open data that installs and runs **on your
own machine** — no gateway, no intermediary, no account. It connects any
MCP-compatible assistant (Claude Desktop, Claude Code, Cursor, VS Code Copilot,
Gemini CLI) straight to the catalogues and the live data, with filtering and
aggregation executed by the portals themselves rather than by the model.

**28 tools · 281 hermetic tests · 18 live tests · MIT**

---

## Why two portals, and why they are not interchangeable

Colombia's national portal runs **Socrata**, which makes it unusual in Latin
America — Argentina, Chile, Mexico, Uruguay and the Dominican Republic all run
CKAN. Socrata ships a real query language, **SoQL**, so `WHERE`, `GROUP BY`,
`count()` and `sum()` all run on the server and only the rolled-up rows travel.

The two city portals run **CKAN 2.10.4**. Their DataStore supports typed
filtering, but neither exposes `datastore_search_sql` — verified against both
live APIs, where Bogotá answers 400 (the action is unregistered, and a WAF
separately blocks the GET form) and Cali answers 403.

That difference is real, so this server exposes it rather than papering over
it. There is an `aggregate_dataset` for the national portal and **no city
equivalent**, because offering one would advertise something those portals
cannot do. A live test asserts the absence for each; if either ever enables
SQL, the build says so.

For the same reason the families are separate tools rather than one tool with a
`portal` switch: Socrata identifiers are 4x4 codes (`abcd-1234`), CKAN
identifiers are UUIDs or slugs, and a shared parameter would have to branch its
validation — and identifier validation is the defence against URL injection.

## Tools

### National portal — `datos.gov.co` (12)

| Tool | What it does |
|---|---|
| `search_datasets` | Catalogue search by keyword, category, tag and asset type. |
| `get_dataset` | Full metadata: columns, types, owner, licence, URL. |
| `list_recent_datasets` | Most recently updated datasets. |
| `list_categories` | Top-level portal categories. |
| `list_tags` | All tags on the portal. |
| `list_owners` | Publishing entities with dataset counts. |
| `autocomplete` | Resolve a partial name to a real dataset / tag / category / owner. |
| `get_site_stats` | Portal totals and which asset types are queryable. |
| `download_dataset_preview` | First N rows, straight from Socrata. |
| `filter_dataset` | Typed WHERE / SELECT / ORDER BY. |
| `aggregate_dataset` | Typed GROUP BY + count / sum / avg / median / min / max / stddev. |
| `query_dataset_soql` | Power-user escape hatch: raw SoQL, read-only, validated. |

### City portals — Bogotá and Cali (8 each)

| Tool (per city) | What it does |
|---|---|
| `<city>_search_datasets` | Catalogue search, filterable by organization, group or tag. |
| `<city>_get_dataset` | Full metadata and every resource, each flagged `queryable`. |
| `<city>_list_organizations` | City entities that publish, with dataset counts. |
| `<city>_list_groups` | Thematic groups. |
| `<city>_list_tags` | Portal tags. |
| `<city>_get_site_stats` | Portal totals, and what the DataStore can and cannot do. |
| `<city>_resource_preview` | First N rows of a DataStore-backed resource, with column types. |
| `<city>_filter_resource` | Typed server-side filter, projection and sort. |

`<city>` is `bogota` or `cali`. Both portals run CKAN 2.10.4 and the eight
tools are generated from one definition, so their shapes are identical — a test
asserts that. Neither has an aggregation tool, because neither portal exposes
`datastore_search_sql`.

## What each portal can and cannot answer

These figures come from running the actual tools against randomly sampled
datasets (`sweep/stress_test.py`), not from reading documentation:

| | datos.gov.co | Bogotá | Cali |
|---|---|---|---|
| Platform | Socrata | CKAN 2.10.4 | CKAN 2.10.4 |
| Filter server-side | yes | yes | yes |
| **Aggregate server-side** | **yes** | no | no |
| Datasets that returned real rows | ~100% | ~13% | see report |

For the CKAN portals that last row is a property of the portal, not of this
server. Two things cause it. Most of each catalogue is published as files
rather than through the DataStore — heavily geospatial in Bogotá's case (SHP,
GPKG, GEOJSON, DXF, KML, the IDECA layers). And the catalogue's own
`datastore_active` flag is unreliable: of 80 resources measured that carried
it, 27 answered HTTP 404 because no table exists. The server rewrites that 404
into an explanation naming the portal's metadata as the cause, so a model is
told the catalogue was wrong instead of assuming it made a mistake.

`<city>_get_dataset` marks every resource `queryable: true/false`. Treat it as a
hint, not a promise.

About 9% of Bogotá's resources are queryable **services** rather than files —
ESRI REST, WFS and WMS endpoints that accept `?query=`, support pagination and,
in the ESRI case, statistics. Reading those needs no download at all, and it is
the highest-value coverage work still open.

This server deliberately does **not** download resource files. Doing so would
mean roughly 750 more lines plus an SSRF guard, and the CSV/XLSX parsers it
would bring could not read SHP or GPKG anyway — so the gap would stay almost as
wide. See `CHANGELOG.md` for the full reasoning.

## Install

```bash
uvx colombian-open-data-mcp
```

Then add it to your client. Claude Code:

```bash
claude mcp add colombia -- uvx colombian-open-data-mcp
```

Claude Desktop, Cursor, VS Code, Gemini CLI and others:
see **[docs/clients.md](docs/clients.md)**.

No API key is required. `SOCRATA_APP_TOKEN` is optional and only raises the
national portal's anonymous rate limit.

## Examples

Ask your assistant, in Spanish or English:

> ¿Cuáles son las diez entidades que más datasets publican en datos.gov.co?

> Busca datasets de presupuesto en datos.gov.co y muéstrame las columnas del
> primero.

> En datos.gov.co, agrupa el dataset de contratación por departamento y suma el
> valor total.

> ¿Qué datasets de movilidad publica Bogotá, y cuáles se pueden consultar fila
> por fila?

> Compara qué entidades publican más datos abiertos en Bogotá y en Cali.

> Filtra el recurso de casos de Bogotá por localidad Bosa y muéstrame las
> primeras 20 filas.

## Design notes

**Socrata and CKAN are the query engines.** Neither portal needs a local cache,
a DuckDB layer or a download step, because both execute the query themselves.
The sibling [`dominican-open-data-mcp`](https://github.com/alcastaro/datos.gob.do-MCP-server)
carries about 4,500 lines of caching, parsing and link-repair machinery that a
CKAN portal without a DataStore forces on you. This one does not need them,
which is why it covers 20 tools in far less code. Fewer lines here is the
result of a better substrate, not a thinner product.

**Every tool returns, none raises.** A portal outage arrives as
`{"error": ..., "hint": ...}` — an exception escaping a tool would reach the
model as an opaque protocol error it cannot act on. A parameterised test
asserts this for all 28, and the stress harness confirms it against the live
catalogues: zero raised exceptions across thousands of real calls.

**Transient failures are retried; definitive ones are not.** A dropped
connection gets three attempts with real backoff. A `ReadTimeout` gets one,
because the server accepted the request and is still working — retrying a
20-second query three times just makes the caller wait a minute for the same
answer.

**Every input is validated before a URL is built.** Socrata 4x4 codes against
an exact regex; CKAN identifiers as UUID or slug; every SoQL identifier through
an allowlist *and* a denylist of comment and statement-break sequences; every
literal escaped. The raw-SoQL escape hatch rejects write keywords and
multi-statement queries. See **[SECURITY.md](SECURITY.md)**.

## Development

```bash
uv sync --group dev --extra dev
uv run pytest                                  # 281 hermetic tests, 85% coverage floor
uv run ruff check src/ tests/
uv run mypy src/colombian_open_data_mcp/
RUN_LIVE_TESTS=1 uv run pytest tests/test_live.py -v   # 18 live tests, opt-in
uv run python sweep/stress_test.py             # 450 random datasets, all three portals
```

Live tests never run in CI. Both portals are third-party infrastructure and
Bogotá's sits behind a WAF; a build that goes red because someone else's rate
limiter had a bad minute is a build people learn to ignore.

See **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## Related projects

- **[dominican-open-data-mcp](https://github.com/alcastaro/datos.gob.do-MCP-server)**
  — the same idea for `datos.gob.do` (CKAN 2.11), already on PyPI and in the
  official MCP Registry.
- **opendata-latam-mcp** — a multi-country adapter. Portal-abstraction work
  belongs there, not here.

## Prior art

Other MCP servers touch Colombian open data, and it is worth being precise
about what each one is:

- **`io.github.pipeworx-io/datos-co`** reached the official registry first, on
  2026-06-02. It offers 3 tools and is **remote-only** — reachable through a
  commercial gateway, with no npm or PyPI package to install.
- **`io.github.cyanheads/socrata-mcp-server`** is a well-built **generic**
  Socrata client for any portal, defaulting to `data.seattle.gov`. It can be
  pointed at Colombia; it does not know Colombia.
- **SECOP servers** (`juandavidsernav`, `pipeworx-io`) and
  **`matematicsolutions/co-eli-mcp`** are verticals covering public procurement
  and Constitutional Court rulings respectively. They complement this server
  rather than overlap it.

This one is the Colombia-specific, whole-portal, locally installable option,
and as far as we can tell the only one covering Bogotá's city catalogue.

## Licence

MIT. See [LICENSE](LICENSE).

The data itself belongs to the publishing Colombian institutions and is
governed by their own licences. See **[docs/PRIVACY.md](docs/PRIVACY.md)**.
