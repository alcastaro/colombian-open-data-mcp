"""colombian-open-data-mcp — MCP server for Colombia's open government data.

Covers two portals that run different platforms:

* ``www.datos.gov.co`` — the national portal, on **Socrata**. Queried with SoQL,
  including server-side aggregation.
* ``datosabiertos.bogota.gov.co`` — Bogotá, on **CKAN 2.10.4**.
* ``datos.cali.gov.co`` — Cali, on **CKAN 2.10.4**.

The two cities are queried through the CKAN DataStore, which filters but cannot
aggregate: neither portal exposes ``datastore_search_sql``.

They are exposed as separate tool families rather than one tool with a
``portal`` switch, because their identifiers and their capabilities genuinely
differ; see ``internal/reportes/`` for the measurements behind that choice.
"""

__version__ = "0.3.0"

USER_AGENT = (
    f"colombian-open-data-mcp/{__version__} "
    "(MCP Server; +https://github.com/alcastaro/colombian-open-data-mcp)"
)
