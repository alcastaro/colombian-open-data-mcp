# Contributing

Thanks for your interest. This is a small solo project; contributions are
welcome but the scope is intentionally narrow.

## Bug reports

Open an issue with:

- Version (`pip show colombian-open-data-mcp`)
- Python version and OS
- Minimal reproduction steps
- What you expected vs. what happened
- Which portal was involved (`datos.gov.co`, Bogotá, Cali, Valle del Cauca or
  Cartagena) — the platforms are different
  platforms and most bugs belong to one of them

## Pull requests

1. **Fork and branch** from `main`.
2. **Install dev deps:** `uv sync --group dev --extra dev`
3. **Quality gates must pass before opening the PR:**
   ```bash
   uv run ruff check src/ tests/ sweep/
   uv run ruff format --check src/ tests/ sweep/
   uv run mypy src/colombian_open_data_mcp/ --no-error-summary
   uv run pytest
   ```
   The suite enforces an 85% coverage floor. Adding code without tests will
   fail the build even if every existing test still passes.
4. **Tests:** add hermetic tests (no live network, `pytest-httpx`) for any new
   behaviour. Live tests go in `tests/test_live.py` behind `@pytest.mark.live`
   and never run in CI — see below for why.
5. **Scope:** this server targets Colombia's portals specifically. Tools for
   other countries belong in `opendata-latam-mcp`, not here.
6. **Security issues:** report privately via
   [GitHub Security Advisories](https://github.com/alcastaro/colombian-open-data-mcp/security/advisories/new)
   rather than opening a public issue. See [SECURITY.md](SECURITY.md).

## Why live tests stay out of CI

All five portals are third-party infrastructure we do not control, and Bogotá's
sits behind a WAF that has already refused a probe from a laptop during
development. A shared CI runner IP is more likely to be refused, not less. A
build that goes red because someone else's rate limiter had a bad minute is a
build the team learns to ignore, which is worse than having no signal at all.

Run them deliberately instead:

```bash
RUN_LIVE_TESTS=1 uv run pytest tests/test_live.py -v
```

## Code style

- Comment **why**, not **what**. Names describe what the code does; comments
  exist for the decision a reader would otherwise have to reconstruct.
- No premature abstraction. Three similar lines beat a helper used once.
- **Every SoQL identifier** goes through `soql.quote_ident()` and **every
  literal** through `soql.quote_literal()`. No f-string interpolation of model
  input into a query, ever.
- **Every CKAN column name** goes through `ckan.is_valid_column()`, and every
  identifier through `ckan.is_valid_uuid()` / `ckan.is_valid_dataset_id()`
  before a request is built.
- **Tools return dicts, never raise.** A portal failure has to reach the model
  as `{"error": ..., "hint": ...}`; an exception escaping a tool arrives as an
  opaque protocol error the model cannot act on. `tests/test_server_tools.py`
  enforces this for every registered tool.
- **Every ArcGIS `where` clause** goes through `esri.validate_where()` and every
  field name through `esri.validate_field()`. ArcGIS evaluates the clause as SQL
  against a database we do not own.
- **No tool may take a URL.** The ESRI and file tools accept a resource UUID and
  look the address up through the portal's own catalogue, which is what bounds
  the set of hosts this server can reach. Every outbound request to a
  catalogue-chosen address goes through `netguard.guard_request_hook`. A test
  asserts the absence of any `url`-shaped parameter, and it is not negotiable.
- **Nothing is cached and nothing touches disk.** Storing these datasets would
  make this server a *responsable del tratamiento* under Ley 1581 de 2012 for
  any of them containing identifiable people. That is a decision to take
  explicitly, not to acquire as a side effect of a performance optimisation.
- **Bumping the version means five files.** `test_version_sync.py` will tell
  you which one you missed.
