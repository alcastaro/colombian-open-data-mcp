<!-- mcp-name: io.github.alcastaro/colombian-open-data-mcp -->

**[English](README.md) · [Español](README.es.md)**

---

# colombian-open-data-mcp

**MCP server for Colombia's open government data — the national portal
[datos.gov.co](https://www.datos.gov.co) (Socrata, 10,000+ datasets) and
Bogotá's city portal
[datosabiertos.bogota.gov.co](https://datosabiertos.bogota.gov.co)
(CKAN, ~1,900 datasets).**

The first MCP server for Colombian open data that installs and runs **on your
own machine** — no gateway, no intermediary, no account. It connects any
MCP-compatible assistant (Claude Desktop, Claude Code, Cursor, VS Code Copilot,
Gemini CLI) straight to the catalogues and the live data, with filtering and
aggregation executed by the portals themselves rather than by the model.

**20 tools · 212 hermetic tests · MIT**

---

## Why two portals, and why they are not interchangeable

Colombia's national portal runs **Socrata**, which makes it unusual in Latin
America — Argentina, Chile, Mexico, Uruguay and the Dominican Republic all run
CKAN. Socrata ships a real query language, **SoQL**, so `WHERE`, `GROUP BY`,
`count()` and `sum()` all run on the server and only the rolled-up rows travel.

Bogotá's city portal runs **CKAN 2.10.4**. Its DataStore supports typed
filtering, but it does **not** expose `datastore_search_sql` — verified against
the live API, where the action is unregistered and a WAF separately blocks the
GET form of that path.

That difference is real, so this server exposes it rather than papering over
it. There is an `aggregate_dataset` for the national portal and **no Bogotá
equivalent**, because offering one would advertise something the portal cannot
do. A live test asserts the absence; if Bogotá ever enables SQL, the build says
so.

For the same reason the two families are separate tools rather than one tool
with a `portal` switch: Socrata identifiers are 4x4 codes (`abcd-1234`), CKAN
identifiers are UUIDs or slugs, and a shared parameter would have to branch its
validation — and identifier validation is the defence against URL injection.

## Tools

### National portal — `datos.gov.co` (12)

| Tool | What it does |
|---|---|
| `search_datasets` | Catalogue search by keyword, category, tag. |
| `get_dataset` | Full metadata: columns, types, owner, licence, URL. |
| `list_recent_datasets` | Most recently updated datasets. |
| `list_categories` | Top-level portal categories. |
| `list_tags` | All tags on the portal. |
| `list_owners` | Publishing entities with dataset counts. |
| `autocomplete` | Resolve a partial name to a real dataset / tag / category / owner. |
| `get_site_stats` | Portal totals. |
| `download_dataset_preview` | First N rows, straight from Socrata. |
| `filter_dataset` | Typed WHERE / SELECT / ORDER BY. |
| `aggregate_dataset` | Typed GROUP BY + count / sum / avg / median / min / max / stddev. |
| `query_dataset_soql` | Power-user escape hatch: raw SoQL, read-only, validated. |

### Bogotá — `datosabiertos.bogota.gov.co` (8)

| Tool | What it does |
|---|---|
| `bogota_search_datasets` | Catalogue search, filterable by organization, group or tag. |
| `bogota_get_dataset` | Full metadata and every resource, each flagged `queryable`. |
| `bogota_list_organizations` | City entities that publish, with dataset counts. |
| `bogota_list_groups` | Thematic groups. |
| `bogota_list_tags` | Portal tags (~3,200). |
| `bogota_get_site_stats` | Portal totals, and what the DataStore can and cannot do. |
| `bogota_resource_preview` | First N rows of a DataStore-backed resource, with column types. |
| `bogota_filter_resource` | Typed server-side filter, projection and sort. |

## What Bogotá can and cannot answer

Worth knowing before you ask it something it cannot do. Sampling 300 datasets
across six points of the catalogue: **128 (43%) have at least one
DataStore-backed resource**, and those can be read row by row. The other 57% is
mostly geospatial — SHP, GPKG, GEOJSON, DXF, KML, WMS/WFS, Bogotá's IDECA
layers — published as files rather than through the DataStore.

Those datasets remain fully discoverable: you get the metadata, the resource
list and the download URLs. They are just not queryable from here.
`bogota_get_dataset` marks every resource with `queryable: true/false` so the
model knows before it tries.

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
asserts this for all 20.

**Every input is validated before a URL is built.** Socrata 4x4 codes against
an exact regex; CKAN identifiers as UUID or slug; every SoQL identifier through
an allowlist *and* a denylist of comment and statement-break sequences; every
literal escaped. The raw-SoQL escape hatch rejects write keywords and
multi-statement queries. See **[SECURITY.md](SECURITY.md)**.

## Development

```bash
uv sync --group dev --extra dev
uv run pytest                                  # 212 hermetic tests, 85% coverage floor
uv run ruff check src/ tests/
uv run mypy src/colombian_open_data_mcp/
RUN_LIVE_TESTS=1 uv run pytest tests/test_live.py -v   # 11 live tests, opt-in
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
