# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Both privacy notes described the 0.3 network model, not the current one.**
  They named two portals and three fixed hosts, and stated that the server
  "cannot be directed to a fourth host". That stopped being true in 0.4, when
  ESRI queries and tabular downloads began following addresses supplied by a
  portal's catalogue. The notes now list all five portals, describe the two
  tool families that reach further and why, and state the two things that bound
  that reach — no tool accepting a URL, and `netguard.py` validating every
  request including redirect hops. The `SOCRATA_APP_TOKEN` paragraph now says
  the token is never sent to any of the four territorial portals, to a map
  service, or to a file host, rather than naming Bogotá alone. A test in
  `tests/test_version_sync.py` fails if a portal is added without the notes
  following, or if the retired claim returns.

## [0.5.0] — 2026-08-29

### Added

- **A release workflow using PyPI Trusted Publishing.** No API token exists
  anywhere — not in the repository, not in organisation secrets, not on a laptop
  — so there is none to leak or rotate. It gates on the full test suite, refuses
  a tag that disagrees with the package version, refuses a source distribution
  carrying anything private, publishes to PyPI, waits for PyPI to actually serve
  the version, and only then publishes to the MCP Registry. Two tests fail if
  anyone reintroduces a stored credential or breaks the YAML.
- **`docs/submission.md`** — the dossier a directory reviewer needs, with five
  worked example prompts that each exercise a different tool family, and the
  data-handling position stated plainly rather than glossed.
- **`CODE_OF_CONDUCT.md`**, including two data-specific rules: never paste
  personal data into an issue or fixture, and do not propose features that would
  store or redistribute datasets containing identifiable people.
- **Manual installation for Gemini CLI and Google Antigravity** in both READMEs.
  Antigravity's MCP Store is curated by Google with no public self-submission
  route, but `~/.gemini/config/mcp_config.json` needs nobody's permission.
- OLDS named as maintainer in the package metadata. Attribution, not ownership
  transfer: the package and the registry namespace stay under the personal
  account for continuity with `dominican-open-data-mcp`.



Migrated to **MCP Python SDK v2**. No behaviour changes; the wire protocol is
identical. This is a dependency and API move made before the first publish, so
that the package ships current rather than pinned below a major it would have
had to cross later.

### Changed

- **`mcp>=1.9.0,<2` became `mcp>=2.1,<3`.** SDK 2.0 renamed `FastMCP` to
  `MCPServer` and replaced `mcp.server.fastmcp` with a stub that raises a
  ModuleNotFoundError naming the migration guide. Every call site moved:
  `mcp.server.mcpserver.MCPServer`, `_mcp_server` → `_lowlevel_server`, and the
  model fields the tests read are snake_case now (`inputSchema` →
  `input_schema`, `readOnlyHint` → `read_only_hint`). The names on the wire are
  unchanged — pydantic still serialises them camelCase — so no client sees a
  difference.
- **The `serverInfo.version` workaround is gone.** v1's FastMCP took no
  `version` argument, so the low-level server reported the installed SDK's
  version as ours and the fix was to reach past the wrapper and assign
  `_mcp_server.version`. v2 takes `version` in the constructor. The test stays,
  because that bug was invisible from inside the server — only a client ever
  saw it.
- The upper bound stays, now on the 3.x major, for the reason 2.0 demonstrated:
  a major can remove the import path this server is built on, and the lockfile
  protects this checkout rather than anyone installing the published wheel.

### Verified

536 hermetic tests and 34 live tests pass on the new SDK across Python 3.10,
3.11, 3.12 and 3.13, with the tool surface unchanged at 24 tools over five
portals and `server.json` still validating against the registry.

## [0.4.0] — 2026-08-29

Five portals, and two new ways to reach data no DataStore holds. **24 tools**
(down from 28, covering twice the portals), 536 hermetic tests, 34 live, 92%
coverage.

Measured coverage — datasets that returned rows a model can read, running the
real tools against a random sample (`sweep/stress_test.py --total 600 --seed
60606`), with zero of 1,428 calls raising an exception:

| Portal | Sample | Returned rows | DataStore | ESRI | File |
|---|---|---|---|---|---|
| `datos.gov.co` | 120 | **100.0%** | — | — | — |
| `datosabiertos.bogota.gov.co` | 120 | **89.2%** (was 27%) | 20 | 21 | 66 |
| `datos.cali.gov.co` | 120 | **61.7%** | 74 | — | — |
| `datosabiertos.valledelcauca.gov.co` | 50 | **100.0%** | 50 | — | — |
| `datosabiertos.cartagena.gov.co` | 38 | **100.0%** | 37 | — | 1 |

Cali's ceiling is the portal's, not this server's: an exhaustive count of all
657 of its datasets finds 167 that are cartographic bundles of JPEG, RAR and
WMS — none of them a table — and its own download URLs answer HTTP 403 to any
non-browser client, as does its GeoServer. Getting past that would mean
impersonating a browser to defeat a WAF, which this project does not do.

### Added

- **Valle del Cauca** (`datosabiertos.valledelcauca.gov.co`, 50 datasets) and
  **Cartagena de Indias** (`datosabiertos.cartagena.gov.co`, 38 datasets, CKAN
  2.11.3). Both catalogues were walked end to end rather than sampled: Valle
  returns rows for 100% of its datasets, Cartagena for 97%, with not a single
  resource wrongly flagged `datastore_active` in either.
- **`city_esri_service_info`, `city_esri_query`, `city_esri_aggregate`** — read
  the ArcGIS REST services that 334 of Bogotá's 1,917 datasets are published as.
  These are APIs, not files: they filter, project and paginate on the server,
  and `city_esri_aggregate` is the only territorial GROUP BY in this server that
  runs remotely instead of being summed over rows in the model's context.
- **`city_read_resource_file`** — download and parse a published CSV, XLSX,
  JSON or GeoJSON resource for the datasets that have no DataStore table and no
  service. Streamed under a 12 MB cap, parsed, answered, discarded; no cache and
  no disk state.
- **`netguard.py`** — the SSRF guard those two capabilities require. Default
  policy is public-internet-only: every resolved address must be globally
  routable, which refuses loopback, RFC-1918, IPv6 unique-local and the cloud
  metadata endpoint at `169.254.169.254`. Installed as an httpx request hook so
  redirect hops are validated too. `strict` and `off` modes via
  `CO_MCP_NETGUARD`. 48 tests.
- Excel support through a new `openpyxl` dependency. Cartagena publishes 34 of
  its 38 datasets as XLSX, so without it most of one portal would be unreadable.

### Changed

- **The per-portal tool families collapsed into eight `city_*` tools taking a
  `city` parameter.** Four portals the old way would have been 32 near-identical
  schemas; this is 12 city tools covering twice the ground, and a fifth portal
  is now a descriptor and nothing else. This does not reverse the earlier
  decision against a `portal` parameter — that argument was Socrata versus CKAN,
  which use different identifiers and offer different capabilities, and it still
  stands. Between CKAN portals none of it applies.
- `SECURITY.md`'s "No SSRF surface" section is **gone, because it stopped being
  true**. It has been replaced by an account of what the server now reaches, why,
  and what constrains it — including the residual DNS-rebinding window, stated
  rather than implied away.
- Tool descriptions now carry each portal's measured coverage, so a model can
  tell that Bogotá is thirty times larger than Cartagena but returns rows a
  third as often.
- **Cali's published coverage figure was corrected from 75% to 67%.** The old
  number came from a page-clustered sample; the new one is an exhaustive count
  of all 657 datasets (440 carry a DataStore resource). The stress harness's
  sampler was fixed at the same time — see below — because the same defect
  produced both a 75% and a 46% estimate of the same quantity.

### Fixed

- **ESRI queries went to the layer root instead of its `/query` endpoint.** A
  layer root answers HTTP 200 with its own description and ignores every
  parameter, so the tool reported zero rows for a layer holding eleven and
  nothing in the response indicated a problem. Found against the live service,
  not in review; a test now asserts the requested path.
- ArcGIS reports query errors under HTTP 200 with an `error` object in the body.
  The client checks the body, never the status code alone.
- A resource the Bogotá catalogue labels `ESRI REST` may be a zipped shapefile.
  Those are now refused with a message naming the tool that does read files.
- **A CSV value containing a newline raised `_csv.Error` out of the tool.**
  `io.StringIO` translates line endings before `csv.reader` sees them unless it
  is given `newline=""`, and `_csv.Error` is not a `TabularError`, so it escaped
  the error envelope entirely and reached the model as an opaque protocol error.
  Two real Bogotá resources hit it; the stress harness is what found them. Any
  parser error now becomes an envelope, and a CSV that breaks partway returns
  the rows it managed to read.

### Measurement

- **The stress harness was sampling by whole pages, and a CKAN catalogue is not
  randomly ordered.** Cali's 144 IDESC cartographic bundles — JPEG, RAR and WMS,
  none of them a table — sit contiguously, so landing on two of those pages put
  fifty unreadable datasets into a sample of 120. The harness reported 46%
  coverage for a portal whose exhaustive count is 67%, and 75% on an earlier
  seed: one defect, two wrong answers in opposite directions. It now takes a few
  datasets from each of many pages instead of every dataset from a few, at the
  same request budget.
- `sample_ckan` also floored its page count, which capped Cartagena at 25 of its
  38 datasets — the last 13 were unreachable whatever was asked for. Catalogues
  smaller than the requested sample are now walked whole.

### Security

- **The source distribution shipped a local tool directory.**
  `.code-review-graph/graph.db`, an 852 KB SQLite database git had never
  tracked, was 74% of the published package and carried absolute paths under
  the maintainer's home directory. `uv build` does not honour nested
  `.gitignore` files. The sdist is now an explicit allowlist, so the next tool
  that leaves a working directory in the tree cannot repeat it, and a test
  fails if that allowlist is ever turned back into a denylist. Caught before
  the first publish; no release ever carried it.
- No tool accepts a URL. The ESRI and file tools take a resource UUID and look
  the address up through the portal's own `resource_show`, so the reachable host
  set is bounded by what a Colombian government catalogue publishes. A test
  asserts the absence of any `url`-shaped parameter across all 24 tools.

## [0.3.0] — 2026-08-29

Three portals instead of two, and the coverage work the stress harness said was
worth doing. 28 tools, 281 hermetic tests, 18 opt-in live tests.

### Added — Cali

- **Eight `cali_*` tools** covering `datos.cali.gov.co` (657 datasets, 30
  publishing entities, 24 groups). It runs CKAN 2.10.4, the same release as
  Bogotá, with the same extensions, and refuses `datastore_search_sql` the same
  way — 403 where Bogotá answers 400. Different guard, identical consequence:
  no server-side GROUP BY, so no aggregation tool, on either.
- Adding it cost a `CkanPortal` descriptor and nothing else. That is what the
  parameterised client bought: the Dominican server's client has its host baked
  into its base URL, its permalinks and its error hints, so a second portal
  there would have meant a second copy of the file.

### Added — the national portal's other 3,860 assets

- **`search_datasets` takes `asset_type`.** It was hardcoded to `only=dataset`,
  which hid every other asset the catalogue publishes. The notable ones are
  **2,197 saved views** (`filter`) that answer `/resource/<4x4>.json` exactly as
  a dataset does — the historical Representative Market Exchange Rate is one of
  them. Charts and maps are queryable too; `href`, `story`, `file` and
  `calendar` are not, and results now carry a `queryable` flag saying which is
  which. A live test asserts a saved view really does return rows.

### Added — retries for the failures a second attempt fixes

- **`retry.py`**, wired into both clients. A stress run over ~600 datasets
  produced three `Server disconnected without sending a response`, each of
  which reached the model as a failed tool call and each of which would have
  succeeded immediately.
- Retried: connect/read/write errors, a connection dropped mid-response, pool
  timeouts, and 502/503/504.
- **Not** retried, deliberately: a `ReadTimeout`, because the server accepted
  the request and is still working — on Socrata that is `count(*)` over three
  million rows, and retrying turns a 20-second wait into 60 before failing
  anyway. Nor any other 4xx: a 404 on a resource the catalogue wrongly flagged
  will be a 404 every time.
- Backoff is 0.4s then 0.8s, three attempts. These are public portals, not a
  load target.

### Changed

- **The eight city tools are generated by `ckan_tools.register`** rather than
  written out per portal. Two implementation notes are recorded in that module
  because both cost a debugging cycle: it must not use
  `from __future__ import annotations` (FastMCP evaluates annotation strings
  against module globals, and these interpolate a closure variable), and
  descriptions must be passed to the decorator rather than assigned to
  `__doc__` afterwards — the first attempt registered eight tools with no
  description at all, which is invisible until you read `tools/list`. A test
  now fails if any tool ships without one.
- Tools read their client from a registry on each call instead of closing over
  it, so a test can swap `server._ckan_clients[key]` like any other attribute
  rather than rewriting a closure cell.
- `_close_clients` iterates the registry, so a fourth portal cannot silently
  leak an open connection.

### Fixed

- `get_site_stats` reported **10,000** datasets; there are **8,391**. The call
  counted every asset type and saturated — unfiltered it returns exactly
  10,000 while the per-type counts sum to 12,251, so 10,000 was a ceiling
  reported as a total.
- The DataStore column allowlist rejected valid names. `MES:` and `Nombre:`
  exist on the portal and a colon was not permitted, so a legitimate projection
  became an error. 294 real column names were collected from live resources and
  the allowlist rebuilt from that evidence. Semicolons and newlines stay
  refused — every semicolon case is an entire malformed CSV row the portal
  exposes as one header — and the message now says to omit `columns` instead.
- A 404 from `datastore_search` on a resource the catalogue flagged as
  DataStore-backed is rewritten to name the portal's metadata as the cause.
  Measured: 27 of 80 flagged resources have no table. The bare CKAN
  "Not Found Error" reads like the caller's mistake.

### Measured

`sweep/stress_test.py` now samples all three portals and reports per portal,
including how many flagged resources turn out to have no table behind them.

## [0.2.0] — 2026-08-29

The first release intended to be installed by anyone else. 0.1.0 was a scaffold
that would not have survived contact with PyPI.

### Fixed — these three would have broken the published package

- **`mcp` dependency now pinned `>=1.9.0,<2`.** It was `>=1.2.0`, unbounded.
  MCP Python SDK 2.0 renamed `FastMCP` to `MCPServer` and removed
  `mcp.server.fastmcp`, which is the module this server imports. Verified in a
  clean environment: `pip install mcp==2.1.1` then importing that module raises
  `ModuleNotFoundError`. The lockfile protected this checkout; it would not have
  protected anyone running `uvx colombian-open-data-mcp`.
- **`serverInfo.version` now reports the package version.** It reported the
  installed SDK's version (`1.27.1`) because `FastMCP` takes no `version`
  argument and the low-level server falls back to the SDK's. Every MCP client
  saw the wrong number in the `initialize` handshake.
- **`User-Agent` now derives from `__version__`.** It carried the literal
  string `0.1`, already out of step with `__version__ = "0.1.0"` before the
  first release.

`tests/test_version_sync.py` now fails the build if any of the five places the
version lives disagree, so this class of drift cannot recur silently.

### Added — Bogotá

- **Eight `bogota_*` tools** covering `datosabiertos.bogota.gov.co`, the city's
  open data catalogue: `bogota_search_datasets`, `bogota_get_dataset`,
  `bogota_list_organizations`, `bogota_list_groups`, `bogota_list_tags`,
  `bogota_get_site_stats`, `bogota_resource_preview`, `bogota_filter_resource`.
  Total tool count is now 20.
- **A parameterised CKAN client** (`ckan.py`). Bogotá runs CKAN 2.10.4, not
  Socrata, so this is a second platform client rather than a second domain in
  the configuration. It is ported from the Dominican MCP's client but takes a
  `CkanPortal` descriptor instead of hardcoding its host in the base URL, the
  permalinks and the error messages.

Two deliberate omissions, both measured rather than guessed:

- **No Bogotá aggregation tool.** That portal does not expose
  `datastore_search_sql` — the action is unregistered (HTTP 400, "Action name
  not known") and a WAF separately blocks the GET form. Offering a `GROUP BY`
  tool would advertise something the portal cannot do. A live test asserts the
  absence, so if Bogotá ever enables it the build says so.
- **No file download.** Most of Bogotá's catalogue is geospatial (SHP, GPKG,
  GEOJSON, DXF, KML, WMS/WFS), which a CSV/XLSX parser would not read anyway.
  Adding a download path would have meant roughly 750 more lines plus
  reintroducing an SSRF guard, and would still have left that gap almost as
  wide. See the coverage figures below, which are lower than an early estimate.

### Changed

- **Every tool now returns an error envelope instead of raising.** Eight of the
  twelve original tools let a `SocrataError` escape, which reached the model as
  an opaque protocol error carrying a traceback. All twenty now return
  `{"error": ..., "hint": ...}`, and a parameterised test asserts it for each
  one.
- **The four listing tools return named envelopes** rather than bare lists.
  `list_categories`, `list_tags`, `list_owners` and `autocomplete` previously
  returned `list[str]` / `list[dict]`, which is a shape with nowhere to put an
  error. This is a **breaking change** to their output; the values now live
  under `categories`, `tags`, `owners` and `matches`.
- **Every tool carries `ToolAnnotations`** marking it read-only and open-world,
  with a Spanish human-readable title. There were none before.
- **`build_agg_expr` and `build_order_by` reject a missing column** with a
  clear `SoqlError` instead of failing several branches later inside
  `quote_ident`.

### Measured, not assumed — a stress harness and what it found

`sweep/stress_test.py` draws a random sample across both catalogues and runs the
**real tool functions** against it, then writes a Markdown report. Two runs of
300 datasets (seeds 2026 and 777) established the figures this release quotes,
and turned up three defects that the hermetic suite could not have seen.

Results over 300 sampled datasets per portal:

| | `datos.gov.co` | Bogotá |
|---|---|---|
| Returned real rows | 299/300 (99.7%) | 39/300 (13%) |
| Flagged as queryable | 100% | 27% |

- **Bogotá's catalogue lies about `datastore_active`.** Of 80 resources flagged
  DataStore-backed, 27 answered HTTP 404 because no table exists. An earlier
  estimate of "43% of datasets are queryable" counted the flag rather than the
  outcome, and drew from systematic rather than random offsets; the honest
  end-to-end figure is about 13%. `datastore_search` now rewrites that 404 into
  an explanation naming the portal's metadata as the cause, instead of passing
  along a bare CKAN "Not Found Error" that reads like the caller's mistake.
- **The column allowlist rejected valid column names.** `MES:` and `Nombre:`
  exist on the portal and a colon was not permitted, so a legitimate projection
  became an error. 294 real column names were collected from live DataStore
  resources and the allowlist rebuilt from that evidence. Semicolons and
  newlines stay refused — the semicolon cases are entire malformed CSV rows the
  portal exposes as a single header — and the rejection message now says to
  omit `columns` to get every field instead.
- **`get_site_stats` reported 10,000 datasets. There are 8,391.** The old call
  counted every asset type and saturated: an unfiltered catalogue query returns
  exactly 10,000 while the per-type counts sum to 12,251, so 10,000 was a
  ceiling, not a total. It now counts `only=dataset`, which is also what
  `search_datasets` can actually reach.

Neither run produced a single raised exception across ~1,850 tool calls — the
error-envelope guarantee holds against the live catalogue, not just against
mocks.

Also worth recording for later: about 9% of Bogotá's resources are queryable
**services** (ESRI REST, WFS, WMS) rather than static files. They accept
`?query=`, support pagination and statistics, and need no download. That is the
open coverage work with the best return, and it is not in this release.

### Added — repository

- `ruff` and `mypy` configuration, and both wired into CI. Neither had ever run
  against this code: the first `ruff check` found four import-ordering errors
  and five files failing `format --check`.
- An 85% coverage floor, currently at 89%.
- CI now covers Python 3.13 and adds a macOS job.
- `pre-commit`, Dependabot and CodeQL.
- `SECURITY.md`, `CONTRIBUTING.md`, `CITATION.cff`, `CHANGELOG.md`,
  `docs/clients.md`, and a privacy note in English and Spanish.
- An `internal/` directory rule in `.gitignore` — as a directory, not a file
  list, because a file list fails the first time someone forgets to extend it.
- Test count went from 74 to 212 hermetic tests, plus 11 opt-in live tests
  (6 national, 5 Bogotá) that stay out of CI on purpose.

### Documentation

- The README no longer claims to be the first MCP server for Colombian open
  data in the official registry. `io.github.pipeworx-io/datos-co` was published
  on 2026-06-02, before this project. The claims are now the two that can be
  checked: first **locally installable** one, and the most complete by tool
  count.

## [0.1.0] — 2026-05-25

Initial scaffold. Never published to PyPI or the MCP Registry.

- 12 tools against `datos.gov.co` via Socrata: 8 discovery, 4 SoQL-native
  (preview, typed filter, typed aggregate, raw SoQL escape hatch).
- SoQL builder with identifier allowlist, literal escaping and a write-keyword
  denylist on the raw-query path.
- 74 hermetic tests, 6 live.
