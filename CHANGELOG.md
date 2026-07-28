# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- **No file download.** Of 300 datasets sampled across six points of the
  catalogue, 128 (43%) have at least one DataStore-backed resource. The
  remaining 57% is largely geospatial (SHP, GPKG, GEOJSON, DXF, KML, WMS/WFS),
  which a CSV/XLSX parser would not read anyway. Adding a download path would
  have meant roughly 750 more lines plus reintroducing an SSRF guard, and would
  still have left that gap almost as wide.

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
