"""colombian-open-data-mcp — MCP server for Colombia's open government data.

Covers five portals on two platforms, reached three ways:

* ``www.datos.gov.co`` — the national portal, on **Socrata**. Queried with
  SoQL, including server-side aggregation.
* ``datosabiertos.bogota.gov.co``, ``datos.cali.gov.co``,
  ``datosabiertos.valledelcauca.gov.co`` and
  ``datosabiertos.cartagena.gov.co`` — the territorial portals, on **CKAN**.
  Queried through the DataStore where a dataset is in it, through the ArcGIS
  REST service many are published as where it is not, and by reading the
  published file as the last resort.

The national tools and the ``city_*`` tools are separate families with a
``city`` parameter on the second, rather than one family with a ``portal``
switch: Socrata and CKAN identifiers and capabilities genuinely differ, and no
CKAN portal here exposes ``datastore_search_sql``, so the server advertises
aggregation only where the platform performs it. The measurements behind those
choices are summarised in the README's coverage table.
"""

__version__ = "0.5.0"

USER_AGENT = (
    f"colombian-open-data-mcp/{__version__} "
    "(MCP Server; +https://github.com/alcastaro/colombian-open-data-mcp)"
)
