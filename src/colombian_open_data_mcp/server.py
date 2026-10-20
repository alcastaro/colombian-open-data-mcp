"""colombian-open-data-mcp — MCP server for Colombia's open government data.

Two portals, two platforms, two tool families:

* ``datos.gov.co`` — national, **Socrata**. Tools have no prefix. Socrata is
  itself the query engine, so filtering and aggregation run server-side via
  SoQL and only rolled-up rows come back.
* Bogotá, Cali, Valle del Cauca and Cartagena — **CKAN**. Eight ``city_*``
  tools serve all four through a ``city`` parameter. Filtering runs server-side
  through the CKAN DataStore; there is no city aggregation tool because none of
  these portals exposes ``datastore_search_sql`` (measured, see ckan.py).
* Two further tool families reach what the DataStore cannot: ``city_esri_*``
  queries the ESRI REST services many Bogotá datasets are published as, and
  ``city_read_resource_file`` downloads and parses a published CSV/XLSX/JSON.
  Both take a resource UUID rather than a URL, and both go through
  :mod:`.netguard`.

The Socrata family stays separate rather than folding into the ``city``
parameter, because the platforms use different identifier formats and offer
different capabilities: a shared tool would have to branch its validation and
would advertise an aggregation the city portals cannot perform. Between CKAN
portals none of that applies, which is why those four did collapse.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, ckan, ckan_tools, esri, police, socrata, soql, tabular
from .netguard import NetGuardError

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("colombian-open-data-mcp")

# `version` is a real constructor argument in SDK v2. Under v1 it was not, and
# the low-level server fell back to the installed SDK's version — so every
# client's `initialize` handshake reported "1.27.1" as ours, and the fix was to
# reach past the wrapper and assign `_mcp_server.version`. That reach-around is
# gone; a test still asserts the handshake reports this package's version,
# because the bug was invisible from inside the server.
mcp = MCPServer("colombian-open-data-mcp", version=__version__)

# Singletons; reused across all tool calls so connections stay warm.
_client = socrata.SocrataClient()


def _ro(title: str) -> ToolAnnotations:
    """Annotations for a read-only tool that talks to a public portal.

    Every tool in this server reads; none of them writes anything anywhere.
    Declaring that lets a host skip confirmation prompts it would otherwise
    raise for a tool whose effects it cannot infer.
    """
    return ToolAnnotations(title=title, read_only_hint=True, open_world_hint=True)


_NATIONAL_HINT = (
    "The datos.gov.co portal or Socrata's catalog API may be temporarily "
    "unavailable. Retry, or narrow the query."
)


def _err(exc: Exception, hint: str, **extra: Any) -> dict:
    """Uniform error envelope.

    Tools return this instead of raising. An exception escaping a tool reaches
    the model as an opaque protocol error with a traceback in it; a dict with a
    `hint` tells the model what to try next, which is the difference between a
    dead end and a recoverable turn.
    """
    if isinstance(exc, NetGuardError):
        # The guard fires before the request leaves, so the caller-supplied hint
        # ("try a broader query", "check the dataset id") would be advice about
        # the wrong problem. Nothing the model passes can change the outcome.
        hint = (
            "The address this request would have reached was refused by the "
            "server's network policy, or could not be resolved at all. That is a "
            "property of the address and of this machine's network, not of your "
            "request: no argument you change will help. A proxy that blocks the "
            "portal, or a host with no DNS, is the usual cause."
        )
    return {"error": str(exc), "hint": hint, **extra}


# ═══════════════════════════════════════════════════════════════════════════
#  datos.gov.co — national portal (Socrata)
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool(annotations=_ro("Buscar datasets (nacional)"))
async def search_datasets(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Free-text search across dataset titles and descriptions. "
                "Omit to list all datasets sorted by relevance."
            )
        ),
    ] = None,
    category: Annotated[
        str | None,
        Field(description="Domain category (e.g. 'Salud y Protección Social')."),
    ] = None,
    tag: Annotated[str | None, Field(description="Tag name (e.g. 'presupuesto').")] = None,
    asset_type: Annotated[
        Literal["dataset", "filter", "chart", "map", "story", "href", "file", "calendar", "any"],
        Field(
            description=(
                "Which kind of asset to search. 'dataset' (the default) covers the "
                "8,391 plain datasets. 'filter' reaches 2,197 saved views that are "
                "queryable through the same data API under their own 4x4 — the "
                "historical Representative Market Exchange Rate is one of them. "
                "'any' searches every type, including ones with no rows behind them."
            )
        ),
    ] = "dataset",
    limit: Annotated[int, Field(description="Results (1-100)", ge=1, le=100)] = 10,
    offset: Annotated[int, Field(description="Offset for pagination.", ge=0)] = 0,
) -> dict:
    """Search datos.gov.co, Colombia's national open data portal.

    Returns a compact list of matching assets with their 4x4 Socrata IDs (use
    these in get_dataset / filter_dataset / aggregate_dataset). Every result
    carries a `queryable` flag saying whether that 4x4 answers the data API.

    For territorial data use city_search_datasets instead.
    """
    try:
        raw = await _client.catalog_search(
            query=query,
            categories=category,
            tags=tag,
            asset_type=asset_type,
            limit=limit,
            offset=offset,
        )
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT)
    return socrata.format_catalog_response(raw)


@mcp.tool(annotations=_ro("Metadatos de dataset (nacional)"))
async def get_dataset(
    id: Annotated[
        str,
        Field(
            description=(
                "Socrata 4x4 dataset ID (format like 'abcd-1234'). "
                "Get from search_datasets results."
            )
        ),
    ],
) -> dict:
    """Return full metadata for a dataset: columns, owner, license, row count, URL.

    Use this to discover the field names you'll need for filter_dataset and
    aggregate_dataset.
    """
    if not socrata.is_valid_4x4(id):
        return {"error": f"Not a valid 4x4 id: {id!r}", "hint": "IDs look like 'abcd-1234'."}
    try:
        view = await _client.get_view(id)
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT, dataset_id=id)
    return socrata.format_view(view)


@mcp.tool(annotations=_ro("Datasets recientes (nacional)"))
async def list_recent_datasets(
    limit: Annotated[int, Field(description="Count (1-50)", ge=1, le=50)] = 10,
) -> dict:
    """Datasets most recently updated on datos.gov.co. Useful for monitoring."""
    try:
        raw = await _client.catalog_recent(limit=limit)
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT)
    return socrata.format_catalog_response(raw)


@mcp.tool(annotations=_ro("Categorías (nacional)"))
async def list_categories() -> dict:
    """Top-level categories on datos.gov.co (Socrata's 'group' analogue)."""
    try:
        items = await _client.domain_categories()
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT)
    return {"country": "CO", "portal": "datos.gov.co", "count": len(items), "categories": items}


@mcp.tool(annotations=_ro("Etiquetas (nacional)"))
async def list_tags() -> dict:
    """All tags published on datos.gov.co."""
    try:
        items = await _client.domain_tags()
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT)
    return {"country": "CO", "portal": "datos.gov.co", "count": len(items), "tags": items}


@mcp.tool(annotations=_ro("Entidades publicadoras (nacional)"))
async def list_owners(
    limit: Annotated[int, Field(description="Max owners (1-200)", ge=1, le=200)] = 50,
) -> dict:
    """Publishing entities on datos.gov.co, with each one's dataset count."""
    try:
        items = await _client.domain_owners(limit=limit)
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT)
    return {"country": "CO", "portal": "datos.gov.co", "count": len(items), "owners": items}


@mcp.tool(annotations=_ro("Autocompletar (nacional)"))
async def autocomplete(
    kind: Annotated[
        Literal["dataset", "tag", "category", "owner"],
        Field(description="Entity type to autocomplete."),
    ],
    query: Annotated[str, Field(description="Partial text to match.")],
    limit: Annotated[int, Field(description="Suggestions (1-30)", ge=1, le=30)] = 10,
) -> dict:
    """Autocomplete dataset / tag / category / owner names on datos.gov.co.

    Useful when the user gives a partial name and the model needs to resolve
    it to a real value before querying.
    """
    try:
        items = await _client.autocomplete(kind=kind, query=query, limit=limit)
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT, kind=kind, query=query)
    except ValueError as e:
        return {"error": str(e), "hint": "kind must be dataset, tag, category or owner."}
    return {"country": "CO", "kind": kind, "query": query, "count": len(items), "matches": items}


@mcp.tool(annotations=_ro("Estadísticas del portal (nacional)"))
async def get_site_stats() -> dict:
    """Portal-wide stats for datos.gov.co: total datasets, categories, tags."""
    try:
        return await _client.site_stats()
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT)


# ─── Socrata-native data tools (the differentiators vs CKAN-based MCPs) ──────


@mcp.tool(annotations=_ro("Vista previa de filas (nacional)"))
async def download_dataset_preview(
    id: Annotated[str, Field(description="Socrata 4x4 dataset ID (e.g. 'abcd-1234').")],
    rows: Annotated[
        int, Field(description="Rows to return (1-1000). Default 20.", ge=1, le=1000)
    ] = 20,
    offset: Annotated[int, Field(description="Rows to skip for pagination.", ge=0)] = 0,
) -> dict:
    """Download the first N rows of a dataset directly via the Socrata data API.

    Unlike CKAN-based portals, Socrata is itself the data store: every
    dataset is queryable via JSON without downloading the whole file. This
    tool returns rows immediately, no caching needed.
    """
    if not socrata.is_valid_4x4(id):
        return {"error": f"Not a valid 4x4 id: {id!r}", "hint": "IDs look like 'abcd-1234'."}
    try:
        result = await _client.resource_query(
            id, soql.compose_soql_params(limit=rows, offset=offset)
        )
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT, dataset_id=id)
    return {
        "country": "CO",
        "dataset_id": id,
        "rows_returned": len(result),
        "rows": result,
        "data_api_endpoint": f"{socrata.RESOURCE_API_BASE}/{id}.json",
    }


@mcp.tool(annotations=_ro("Filtrar dataset (nacional)"))
async def filter_dataset(
    id: Annotated[str, Field(description="Socrata 4x4 dataset ID.")],
    filters: Annotated[
        list[dict] | None,
        Field(
            description=(
                "Filter conditions, AND-combined. Each item is {col, op, val}. "
                "Valid ops: =, !=, <, <=, >, >=, in, not_in, contains, "
                "starts_with, ends_with, is_null, is_not_null. "
                'Example: [{"col":"departamento","op":"=","val":"Cundinamarca"}].'
            )
        ),
    ] = None,
    columns: Annotated[
        list[str] | None,
        Field(description="Field names to SELECT. None = all columns."),
    ] = None,
    order_by: Annotated[
        list[dict] | None,
        Field(
            description=(
                'List of {col, dir} where dir is "asc" or "desc". '
                'Example: [{"col":"fecha","dir":"desc"}].'
            )
        ),
    ] = None,
    limit: Annotated[int, Field(description="Max rows (1-1000)", ge=1, le=1000)] = 100,
    offset: Annotated[int, Field(description="Rows to skip.", ge=0)] = 0,
) -> dict:
    """Typed filter / select / order / limit query against a dataset via SoQL.

    Safer than writing raw SoQL — every column name is allow-list checked,
    every literal is properly escaped. Use when you know what columns and
    filters you want without needing SQL features.
    """
    if not socrata.is_valid_4x4(id):
        return {"error": f"Not a valid 4x4 id: {id!r}", "hint": "IDs look like 'abcd-1234'."}
    try:
        select = ", ".join(soql.quote_ident(c) for c in columns) if columns else None
        where = soql.build_where(filters)
        order = soql.build_order_by(order_by)
    except soql.SoqlError as e:
        return {"error": str(e), "hint": "Check column names against get_dataset."}

    params = soql.compose_soql_params(
        select=select, where=where, order=order, limit=limit, offset=offset
    )
    try:
        rows = await _client.resource_query(id, params)
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT, dataset_id=id, params=params)
    return {
        "country": "CO",
        "dataset_id": id,
        "rows_returned": len(rows),
        "params": params,
        "rows": rows,
    }


@mcp.tool(annotations=_ro("Agregar dataset (nacional)"))
async def aggregate_dataset(
    id: Annotated[str, Field(description="Socrata 4x4 dataset ID.")],
    aggregations: Annotated[
        list[dict],
        Field(
            description=(
                "List of {col, fn, alias}. Valid fns: count, count_distinct, "
                "sum, avg, mean, median, min, max, stddev. col=null or "
                "col='*' means count(*). "
                'Example: [{"col":null,"fn":"count","alias":"total"},'
                '{"col":"valor","fn":"sum","alias":"suma"}].'
            )
        ),
    ],
    group_by: Annotated[
        list[str] | None,
        Field(description='Columns to GROUP BY. Example: ["departamento"].'),
    ] = None,
    filters: Annotated[
        list[dict] | None,
        Field(description="Same syntax as filter_dataset.filters."),
    ] = None,
    order_by: Annotated[
        list[dict] | None,
        Field(description="Same syntax as filter_dataset.order_by."),
    ] = None,
    limit: Annotated[int, Field(description="Max groups (1-1000)", ge=1, le=1000)] = 100,
) -> dict:
    """Server-side GROUP BY + aggregation against a dataset via SoQL.

    Socrata runs the aggregation on its end and returns the rolled-up rows
    only — fast even on multi-million-row datasets, no client-side cache
    needed. There is no Bogotá equivalent: that portal does not expose
    datastore_search_sql, so it cannot group server-side.
    """
    if not socrata.is_valid_4x4(id):
        return {"error": f"Not a valid 4x4 id: {id!r}", "hint": "IDs look like 'abcd-1234'."}
    if not aggregations:
        return {"error": "aggregations cannot be empty", "hint": "Pass at least one {col, fn}."}
    try:
        agg_parts = [soql.build_agg_expr(a) for a in aggregations]
        group_parts = [soql.quote_ident(c) for c in group_by] if group_by else []
        select_clause = ", ".join([*group_parts, *agg_parts])
        where = soql.build_where(filters)
        order = soql.build_order_by(order_by)
    except soql.SoqlError as e:
        return {"error": str(e), "hint": "Check column names against get_dataset."}

    params = soql.compose_soql_params(
        select=select_clause,
        where=where,
        group=", ".join(group_parts) if group_parts else None,
        order=order,
        limit=limit,
    )
    try:
        rows = await _client.resource_query(id, params)
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT, dataset_id=id, params=params)
    return {
        "country": "CO",
        "dataset_id": id,
        "groups_returned": len(rows),
        "params": params,
        "rows": rows,
    }


@mcp.tool(annotations=_ro("SoQL crudo (nacional)"))
async def query_dataset_soql(
    id: Annotated[str, Field(description="Socrata 4x4 dataset ID.")],
    soql_query: Annotated[
        str,
        Field(
            description=(
                "Raw SoQL query string passed as $query. Use SELECT-style "
                "SoQL only — write keywords (INSERT/UPDATE/DELETE/DROP/etc.) "
                "are rejected. "
                'Example: "SELECT departamento, count(*) WHERE año=2024 '
                'GROUP BY departamento ORDER BY count DESC"'
            )
        ),
    ],
    limit: Annotated[
        int, Field(description="Max rows the wrapper enforces (1-1000)", ge=1, le=1000)
    ] = 200,
) -> dict:
    """Run an arbitrary SoQL query against a dataset (power-user escape hatch).

    Socrata's full SoQL syntax is available: window functions, joins on the
    same dataset, etc. The query is validated against a denylist of write
    keywords and multi-statement breaks. A hard row cap is always applied.
    """
    if not socrata.is_valid_4x4(id):
        return {"error": f"Not a valid 4x4 id: {id!r}", "hint": "IDs look like 'abcd-1234'."}
    try:
        cleaned = soql.validate_soql(soql_query)
    except soql.SoqlError as e:
        return {"error": str(e), "hint": "Only read-only SELECT-style SoQL is accepted."}

    # Socrata rejects $query alongside any other $ parameter, so the row cap
    # goes inside the query text rather than beside it.
    cleaned = soql.enforce_row_cap(cleaned, limit)
    params = {"$query": cleaned}
    try:
        rows = await _client.resource_query(id, params)
    except (socrata.SocrataError, NetGuardError) as e:
        return _err(e, _NATIONAL_HINT, dataset_id=id, params=params)
    return {
        "country": "CO",
        "dataset_id": id,
        "soql_executed": cleaned,
        "rows_returned": len(rows),
        "rows": rows,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Territorial portals on CKAN — Bogotá, Cali, Valle del Cauca, Cartagena
# ═══════════════════════════════════════════════════════════════════════════
#
# Eight `city_*` tools serve all four portals through a `city` parameter. See
# ckan_tools for why that collapse is right between CKAN portals and wrong
# between CKAN and Socrata, and for why that module must not use
# `from __future__ import annotations`.

# The registry is populated by register() rather than built from its return
# value, because the tools read it on every call — see ckan_tools.register.
_ckan_clients: dict[str, ckan.CkanClient] = {}
# One ArcGIS client serves every portal, because ESRI layers are not per-portal:
# a Bogotá dataset's layer lives on catastrobogota.gov.co or habitatbogota.gov.co,
# neither of which belongs to any single catalogue.
_esri_clients: dict[str, esri.EsriClient] = {}
ckan_tools.register(mcp, _ckan_clients, _esri_clients, ro=_ro, err=_err)


# ─── Entry point ────────────────────────────────────────────────────────────


def _tool_count() -> int | None:
    try:
        return len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Policía Nacional — estadística delictiva (EXPERIMENTAL, not a portal)
# ═══════════════════════════════════════════════════════════════════════════

_POLICE_WARNING = (
    "EXPERIMENTAL. This is the only source here that is not an open-data portal: "
    "it is a page on policia.gov.co with no API, no catalogue and no stable "
    "identifiers, whose URL already ends in '-old'. It can change shape or "
    "disappear without notice. Everything else in this server talks to Socrata or "
    "CKAN and is unaffected when it does."
)


def _police_err(e: Exception) -> dict:
    return _err(
        e,
        "The Policía's statistics page has no API and changes without notice. "
        "Call police_list_crime_files first: it reports what is published right "
        "now, and fails loudly rather than returning an empty list if the page "
        "has changed structure.",
    )


@mcp.tool(
    name="police_list_crime_files",
    annotations=_ro("Catálogo de estadística delictiva (Policía, experimental)"),
    description=(
        "List the crime spreadsheets the Policía Nacional publishes, grouped by "
        "crime, with the file id you pass to police_read_crime_file.\n\n"
        "Use this for the victim's **age group**, which the portal route does not "
        "carry. For homicide itself prefer `m8fd-ahd9` on datos.gov.co (Observatorio "
        "MDN, 2003-2026, with urban/rural zone and a feminicide marker); the two agree "
        "on 2023 at 13,555 cases and that one has a real date column and a licence.\n\n"
        "`year_hint` comes from the file name and is a hint, never a fact: 34 of the "
        "215 files carry no year at all, and one of those holds 2023. The year is "
        "only authoritative inside the file, which police_read_crime_file returns as "
        "`period_year`.\n\n" + _POLICE_WARNING
    ),
)
async def police_list_crime_files(
    crime: Annotated[
        str | None,
        Field(
            description=(
                "Filter by crime, accent- and case-insensitive substring "
                "(e.g. 'homicidio intencional', 'delitos sexuales')."
            )
        ),
    ] = None,
) -> dict:
    """What the Policía publishes right now, grouped by crime."""
    try:
        entries = await police.fetch_index()
    except (police.PoliceError, NetGuardError) as e:
        return _police_err(e)
    out = police.summarise_index(entries)
    if crime:
        needle = police._slug(crime)
        out["crimes"] = {k: v for k, v in out["crimes"].items() if needle in k}
        out["filtered_by"] = crime
    return out


@mcp.tool(
    name="police_read_crime_file",
    annotations=_ro("Leer un archivo de estadística delictiva (Policía, experimental)"),
    description=(
        "Read one crime spreadsheet: its rows, or a rollup when you pass "
        "`group_by`.\n\n"
        "Pass `file_id` exactly as police_list_crime_files reports it. The address is "
        "rebuilt from a fixed host and path, so a file id cannot point anywhere "
        "else.\n\n"
        "A file holds tens of thousands of rows, so prefer `group_by` — for example "
        "['anio','mes'] for a monthly series, or ['codigo_dane'] for a municipal "
        "one.\n\n"
        "The counts are **casos denunciados ante la Policía**, reports received "
        "rather than prevalence. The last month of any cut is under-registered. The "
        "names of the officers who signed the report are never returned.\n\n" + _POLICE_WARNING
    ),
)
async def police_read_crime_file(
    file_id: Annotated[
        str,
        Field(
            description=(
                "File name exactly as the catalogue reports it, e.g. "
                "'Homicidio Intencional2025.xlsx'."
            )
        ),
    ],
    rows: Annotated[int, Field(description="Rows when not grouping (1-500).", ge=1, le=500)] = 25,
    group_by: Annotated[
        list[str] | None,
        Field(
            description=(
                "Roll up by any of: anio, mes, departamento, municipio, "
                "codigo_dane, genero, grupo_etario."
            )
        ),
    ] = None,
) -> dict:
    """One crime report, parsed and cleaned."""
    if not police.is_valid_file_id(file_id):
        return {
            "error": f"Not a valid file id: {file_id!r}",
            "hint": "Ids are bare file names ending in .xlsx or .xls, from police_list_crime_files.",
        }
    try:
        return await police.read_crime_file(file_id, rows=rows, group_by=group_by)
    except (police.PoliceError, NetGuardError, tabular.TabularError) as e:
        return _police_err(e)


async def _close_clients() -> None:
    """Close every HTTP client. A leaked one keeps the process alive past EOF."""
    await _client.close()
    for client in _ckan_clients.values():
        await client.close()
    for esri_client in _esri_clients.values():
        await esri_client.close()


def main() -> None:
    portals = ", ".join([socrata.PORTAL_HOST, *(p.host for p in ckan.PORTALS.values())])
    logger.info("colombian-open-data-mcp %s starting (portals: %s)", __version__, portals)
    count = _tool_count()
    if count is not None:
        logger.info("Registered %d tools", count)
    try:
        mcp.run()
    except Exception:
        logger.exception("Fatal error in MCP server")
        raise
    finally:
        try:
            asyncio.run(_close_clients())
        except RuntimeError:
            pass
        logger.info("colombian-open-data-mcp shut down")


if __name__ == "__main__":
    main()
