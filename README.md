<!-- mcp-name: io.github.alcastaro/colombian-open-data-mcp -->

**[English](README.md) · [Español](README.es.md)**

---

# colombian-open-data-mcp

**MCP server for Colombia's open government data — five portals, three data
sources: the national [datos.gov.co](https://www.datos.gov.co) (Socrata, 8,391
datasets), [Bogotá](https://datosabiertos.bogota.gov.co) (CKAN, ~1,900),
[Cali](https://datos.cali.gov.co) (657),
[Valle del Cauca](https://datosabiertos.valledelcauca.gov.co) (50) and
[Cartagena](https://datosabiertos.cartagena.gov.co) (38).**

The first MCP server for Colombian open data that installs and runs **on your
own machine** — no gateway, no intermediary, no account. It connects any
MCP-compatible assistant (Claude Desktop, Claude Code, Cursor, VS Code Copilot,
Gemini CLI) straight to the catalogues and the live data, with filtering and
aggregation executed by the portals themselves rather than by the model.

**24 tools · 529 hermetic tests · 34 live tests · 92% coverage · MIT**

---

## Why the platforms are not interchangeable

Colombia's national portal runs **Socrata**, which makes it unusual in Latin
America — Argentina, Chile, Mexico, Uruguay and the Dominican Republic all run
CKAN. Socrata ships a real query language, **SoQL**, so `WHERE`, `GROUP BY`,
`count()` and `sum()` all run on the server and only the rolled-up rows travel.

The four territorial portals run **CKAN**. Their DataStore supports typed
filtering, but **none exposes `datastore_search_sql`** — verified against all
four live APIs, where Bogotá answers 400 (the action is unregistered, and a WAF
separately blocks the GET form), Cali answers 403, and Valle and Cartagena
answer 400.

That difference is real, so this server exposes it rather than papering over
it. There is an `aggregate_dataset` for the national portal and **no DataStore
equivalent for the cities**, because offering one would advertise something
those portals cannot do. A live test asserts the absence on each; if any ever
enables SQL, the build says so.

The Socrata and CKAN families stay separate rather than folding into one tool
with a `portal` switch: Socrata identifiers are 4x4 codes (`abcd-1234`), CKAN
identifiers are UUIDs or slugs, and a shared parameter would have to branch its
validation — and identifier validation is the defence against URL injection.
Between the four CKAN portals none of that applies, which is why those *did*
collapse into a single `city` parameter in 0.4.

## Three ways into the data

A Colombian territorial catalogue publishes the same dataset in several forms,
and only one of them is a database table. This server reads all three, in the
order a model should try them:

1. **The CKAN DataStore** — a typed query against a table the portal already
   built. Nothing is transferred but the answer.
2. **ArcGIS REST services** — 334 of Bogotá's 1,917 datasets are published as
   ESRI layers. These are APIs, not files: they filter, project, paginate, and
   **compute GROUP BY on the server**. `city_esri_aggregate` is the only
   territorial rollup here that is not summed over rows in the model's context.
3. **The published file** — a CSV, XLSX, JSON or GeoJSON at a download URL, for
   the datasets that have no table and no service. Streamed under a 12 MB cap,
   parsed, answered, discarded. Nothing is cached and nothing touches disk.

That third avenue is what the sibling Dominican server does for its *whole*
catalogue, because `datos.gob.do` runs CKAN with no DataStore extension at all.
Here it is the last resort, which is why it costs a fraction of the code.

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

### Territorial portals — one family, four catalogues (12)

Every tool below takes a `city` parameter: `bogota`, `cali`, `valle` or
`cartagena`.

| Tool | What it does |
|---|---|
| `city_search_datasets` | Catalogue search, filterable by organization, group or tag. |
| `city_get_dataset` | Full metadata and every resource, each flagged `queryable`. |
| `city_list_organizations` | Entities that publish, with dataset counts. |
| `city_list_groups` | Thematic groups. |
| `city_list_tags` | Portal tags. |
| `city_get_site_stats` | Portal totals, and what the DataStore can and cannot do. |
| `city_resource_preview` | First N rows of a DataStore-backed resource, with column types. |
| `city_filter_resource` | Typed server-side filter, projection and sort. |
| `city_esri_service_info` | Fields and capabilities of an ArcGIS REST layer. |
| `city_esri_query` | Rows from an ArcGIS layer, filtered and paginated server-side. |
| `city_esri_aggregate` | **Server-side GROUP BY** on an ArcGIS layer. |
| `city_read_resource_file` | Download and parse a published CSV / XLSX / JSON resource. |

Through 0.3 each portal had its own family of eight prefixed tools. Four
portals that way would be thirty-two near-identical schemas, so 0.4 collapsed
them: twelve tools covering twice the ground, and a fifth portal is now a
descriptor and nothing else.

## What each portal can and cannot answer

These figures come from running the actual tools against a random sample —
`sweep/stress_test.py --total 600 --seed 60606` — not from reading
documentation. Valle del Cauca and Cartagena are small enough that their whole
catalogues were walked rather than sampled.

| Portal | Platform | Sample | Returned real rows | Rate | DataStore | ESRI | File |
|---|---|---|---|---|---|---|---|
| `datos.gov.co` | Socrata | 120 | 120 | **100.0%** | — | — | — |
| `datos.cali.gov.co` | CKAN | 120 | 74 | **61.7%** | 74 | — | — |
| `datosabiertos.bogota.gov.co` | CKAN | 120 | 107 | **89.2%** | 20 | 21 | 66 |
| `datosabiertos.cartagena.gov.co` | CKAN | 38 | 38 | **100.0%** | 37 | — | 1 |
| `datosabiertos.valledelcauca.gov.co` | CKAN | 50 | 50 | **100.0%** | 50 | — | — |

The three rightmost columns say **which avenue** delivered the rows. That
breakdown is deliberate: a coverage number that rises when new tools land,
without saying which tool did the work, is not a number anyone can check.

Bogotá is the portal this release was aimed at, and the shape of its catalogue
explains why. Half its datasets carry a resource the catalogue *flags* as
DataStore-backed, and the flag is wrong more often than it is right: in this
run, **37 of those 59 datasets answered HTTP 404** because no table exists
behind the flag. So the DataStore alone reached 20 of 120. The server rewrites
that 404 into an explanation naming the portal's metadata as the cause, so a
model is told the catalogue was wrong instead of assuming it made a mistake.
`city_get_dataset` marks every resource `queryable: true/false`; treat it as a
hint, not a promise.

What closes the gap is that the rest of the catalogue is not missing, only
published differently — as ArcGIS services and as plain files. Reading both is
what took Bogotá from 27% to 89%.

Across every tool call in that run, **not one raised an exception** — every
failure arrived as an error envelope the model can act on.

### What is still out of reach

Genuinely geospatial archives — SHP, GPKG, DXF, KML, DWG — are refused with an
explanation rather than parsed badly. Reading them would need a GIS stack this
server has no business carrying, and where a dataset publishes an ESRI service
alongside its shapefile, the service already answers the question.

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

> Cuenta cuántos parques hay por localidad en la capa ESRI de Bogotá — que lo
> agrupe el servidor, no tú.

> Ese dataset de Bogotá no está en el DataStore. Lee el archivo publicado y
> muéstrame las columnas.

> ¿Qué publica el portal de Cartagena y qué de eso se puede consultar?

## Design notes

**The portals are the query engines, wherever possible.** Socrata runs SoQL,
CKAN's DataStore runs typed filters, and ArcGIS computes statistics — so three
of the four data paths transfer only the answer. The sibling
[`dominican-open-data-mcp`](https://github.com/alcastaro/datos.gob.do-MCP-server)
carries about 4,500 lines of caching, parsing and link-repair machinery,
because `datos.gob.do` runs CKAN with **no DataStore at all** and every row it
serves has to come out of a downloaded file. This server needs a download for
the last resort only, and needs no cache for any of it.

**Nothing is stored.** `city_read_resource_file` streams under a cap, parses the
first rows, answers and discards the bytes. That is a deliberate limit as much
as a design choice: persisting these datasets would make this server a
*responsable del tratamiento* under Ley 1581 de 2012 for any of them containing
identifiable people. That is a decision to take explicitly and separately, not
to acquire as a side effect of a performance optimisation.

**No tool accepts a URL.** The ESRI and file tools take a resource UUID and look
the address up through the portal's own catalogue, so the set of hosts this
server can reach is bounded by what a Colombian government catalogue publishes.
On top of that, `netguard.py` requires every resolved address to be globally
routable — refusing loopback, RFC-1918, IPv6 unique-local and the cloud metadata
endpoint at `169.254.169.254` — and is installed as an httpx request hook so
redirect hops are checked too. See **[SECURITY.md](SECURITY.md)**, whose "No
SSRF surface" section was **removed in 0.4 because it stopped being true**.

**Every tool returns, none raises.** A portal outage arrives as
`{"error": ..., "hint": ...}` — an exception escaping a tool would reach the
model as an opaque protocol error it cannot act on. A parameterised test
asserts this for all 24, and the stress harness confirms it against the live
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
uv run pytest                                  # 529 hermetic tests, 85% coverage floor
uv run ruff check src/ tests/ sweep/
uv run mypy src/colombian_open_data_mcp/
RUN_LIVE_TESTS=1 uv run pytest tests/test_live.py -v   # 34 live tests, opt-in
uv run python sweep/stress_test.py --total 600 --seed 60606   # all five portals
```

Live tests never run in CI. All five portals are third-party infrastructure and
Bogotá's sits behind a WAF; a build that goes red because someone else's rate
limiter had a bad minute is a build people learn to ignore.

The stress harness is not a test — it is an occasional measurement against live
catalogues, and it is deliberately polite: concurrency 4, a delay between
probes, and an avenue order that tries the cheapest request first. Do not raise
the concurrency to go faster.

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
and as far as we can tell the only one covering Colombia's territorial
catalogues — Bogotá, Cali, Valle del Cauca and Cartagena — alongside the
national portal.

## Licence

MIT. See [LICENSE](LICENSE).

The data itself belongs to the publishing Colombian institutions and is
governed by their own licences. See **[docs/PRIVACY.md](docs/PRIVACY.md)**.
