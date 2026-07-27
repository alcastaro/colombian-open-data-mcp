"""colombian-open-data-mcp — FastMCP server for Colombia's open government data.

Two portals, two platforms, two tool families:

* ``datos.gov.co`` — national, **Socrata**. Tools have no prefix. Socrata is
  itself the query engine, so filtering and aggregation run server-side via
  SoQL and only rolled-up rows come back.
* ``datosabiertos.bogota.gov.co`` — Bogotá, **CKAN 2.10**. Tools are prefixed
  ``bogota_``. Filtering runs server-side through the CKAN DataStore; there is
  no aggregation tool because this portal does not expose
  ``datastore_search_sql`` (measured, see ckan.py).

The families are kept separate rather than folded into a ``portal`` parameter
because the two platforms use different identifier formats and offer different
capabilities. A shared tool would have to branch its validation and would
advertise an aggregation Bogotá cannot perform.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, ckan, socrata, soql

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("colombian-open-data-mcp")

mcp = FastMCP("colombian-open-data-mcp")

# FastMCP takes no `version` argument, so the low-level server falls back to
# the installed mcp SDK's version — which means every client's `initialize`
# handshake reported the SDK version (e.g. "1.27.1") as ours. Setting it here
# is the only place the real package version reaches the wire.
mcp._mcp_server.version = __version__

# Singletons; reused across all tool calls so connections stay warm.
_client = socrata.SocrataClient()
_bogota = ckan.CkanClient(ckan.BOGOTA)


def _ro(title: str) -> ToolAnnotations:
    """Annotations for a read-only tool that talks to a public portal.

    Every tool in this server reads; none of them writes anything anywhere.
    Declaring that lets a host skip confirmation prompts it would otherwise
    raise for a tool whose effects it cannot infer.
    """
    return ToolAnnotations(title=title, readOnlyHint=True, openWorldHint=True)


_NATIONAL_HINT = (
    "The datos.gov.co portal or Socrata's catalog API may be temporarily "
    "unavailable. Retry, or narrow the query."
)
_BOGOTA_HINT = (
    "The datosabiertos.bogota.gov.co portal may be temporarily unavailable, or "
    "a WAF rejected the request. Retry, or narrow the query."
)


def _err(exc: Exception, hint: str, **extra: Any) -> dict:
    """Uniform error envelope.

    Tools return this instead of raising. An exception escaping a tool reaches
    the model as an opaque protocol error with a traceback in it; a dict with a
    `hint` tells the model what to try next, which is the difference between a
    dead end and a recoverable turn.
    """
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
    limit: Annotated[int, Field(description="Results (1-100)", ge=1, le=100)] = 10,
    offset: Annotated[int, Field(description="Offset for pagination.", ge=0)] = 0,
) -> dict:
    """Search datasets on datos.gov.co (Colombia's national open data portal).

    Returns a compact list of matching datasets with their 4x4 Socrata IDs
    (use these IDs in get_dataset / filter_dataset / aggregate_dataset).
    For Bogotá city data use bogota_search_datasets instead.
    """
    try:
        raw = await _client.catalog_search(
            query=query, categories=category, tags=tag, limit=limit, offset=offset
        )
    except socrata.SocrataError as e:
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
    except socrata.SocrataError as e:
        return _err(e, _NATIONAL_HINT, dataset_id=id)
    return socrata.format_view(view)


@mcp.tool(annotations=_ro("Datasets recientes (nacional)"))
async def list_recent_datasets(
    limit: Annotated[int, Field(description="Count (1-50)", ge=1, le=50)] = 10,
) -> dict:
    """Datasets most recently updated on datos.gov.co. Useful for monitoring."""
    try:
        raw = await _client.catalog_recent(limit=limit)
    except socrata.SocrataError as e:
        return _err(e, _NATIONAL_HINT)
    return socrata.format_catalog_response(raw)


@mcp.tool(annotations=_ro("Categorías (nacional)"))
async def list_categories() -> dict:
    """Top-level categories on datos.gov.co (Socrata's 'group' analogue)."""
    try:
        items = await _client.domain_categories()
    except socrata.SocrataError as e:
        return _err(e, _NATIONAL_HINT)
    return {"country": "CO", "portal": "datos.gov.co", "count": len(items), "categories": items}


@mcp.tool(annotations=_ro("Etiquetas (nacional)"))
async def list_tags() -> dict:
    """All tags published on datos.gov.co."""
    try:
        items = await _client.domain_tags()
    except socrata.SocrataError as e:
        return _err(e, _NATIONAL_HINT)
    return {"country": "CO", "portal": "datos.gov.co", "count": len(items), "tags": items}


@mcp.tool(annotations=_ro("Entidades publicadoras (nacional)"))
async def list_owners(
    limit: Annotated[int, Field(description="Max owners (1-200)", ge=1, le=200)] = 50,
) -> dict:
    """Publishing entities on datos.gov.co, with each one's dataset count."""
    try:
        items = await _client.domain_owners(limit=limit)
    except socrata.SocrataError as e:
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
    except socrata.SocrataError as e:
        return _err(e, _NATIONAL_HINT, kind=kind, query=query)
    except ValueError as e:
        return {"error": str(e), "hint": "kind must be dataset, tag, category or owner."}
    return {"country": "CO", "kind": kind, "query": query, "count": len(items), "matches": items}


@mcp.tool(annotations=_ro("Estadísticas del portal (nacional)"))
async def get_site_stats() -> dict:
    """Portal-wide stats for datos.gov.co: total datasets, categories, tags."""
    try:
        return await _client.site_stats()
    except socrata.SocrataError as e:
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
    except socrata.SocrataError as e:
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
    except socrata.SocrataError as e:
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
    except socrata.SocrataError as e:
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

    params = {"$query": cleaned, "$limit": str(limit)}
    try:
        rows = await _client.resource_query(id, params)
    except socrata.SocrataError as e:
        return _err(e, _NATIONAL_HINT, dataset_id=id, params=params)
    return {
        "country": "CO",
        "dataset_id": id,
        "soql_executed": cleaned,
        "rows_returned": len(rows),
        "rows": rows,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  datosabiertos.bogota.gov.co — Bogotá city portal (CKAN 2.10)
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool(annotations=_ro("Buscar datasets (Bogotá)"))
async def bogota_search_datasets(
    query: Annotated[
        str | None,
        Field(description="Free-text search. Omit to list the whole catalog."),
    ] = None,
    organization: Annotated[
        str | None,
        Field(description="Organization slug, from bogota_list_organizations."),
    ] = None,
    group: Annotated[str | None, Field(description="Group slug, from bogota_list_groups.")] = None,
    tag: Annotated[str | None, Field(description="Tag name.")] = None,
    limit: Annotated[int, Field(description="Results (1-100)", ge=1, le=100)] = 10,
    offset: Annotated[int, Field(description="Offset for pagination.", ge=0)] = 0,
) -> dict:
    """Search Bogotá's city open data catalog (about 1,900 datasets).

    Each returned dataset lists its resources, and each resource carries a
    `queryable` flag. Only resources with `queryable: true` can be read row by
    row with bogota_filter_resource — the rest are downloadable files, many of
    them geospatial (SHP, GPKG, KML).
    """
    try:
        body = await _bogota.package_search(
            query=query,
            organization=organization,
            group=group,
            tag=tag,
            rows=limit,
            start=offset,
        )
    except ckan.CkanError as e:
        return _err(e, _BOGOTA_HINT)
    return ckan.format_search_response(body, _bogota.portal)


@mcp.tool(annotations=_ro("Metadatos de dataset (Bogotá)"))
async def bogota_get_dataset(
    id: Annotated[
        str,
        Field(
            description=(
                "Dataset UUID or URL slug, from bogota_search_datasets (the 'id' or 'name' field)."
            )
        ),
    ],
) -> dict:
    """Full metadata for one Bogotá dataset, including every resource.

    Use this to find the resource UUID you need for bogota_filter_resource or
    bogota_resource_preview.
    """
    if not ckan.is_valid_dataset_id(id):
        return {
            "error": f"Not a valid dataset id or slug: {id!r}",
            "hint": "Pass the 'id' (UUID) or 'name' (slug) from bogota_search_datasets.",
        }
    try:
        d = await _bogota.package_show(id)
    except ckan.CkanError as e:
        return _err(e, _BOGOTA_HINT, dataset_id=id)
    return ckan.format_dataset(d, _bogota.portal)


@mcp.tool(annotations=_ro("Entidades publicadoras (Bogotá)"))
async def bogota_list_organizations(
    limit: Annotated[int, Field(description="Max organizations (1-200)", ge=1, le=200)] = 60,
) -> dict:
    """Publishing entities of Bogotá's city government, with dataset counts."""
    try:
        items = await _bogota.organization_list(limit=limit)
    except ckan.CkanError as e:
        return _err(e, _BOGOTA_HINT)
    formatted = [ckan.format_organization(o, _bogota.portal) for o in items]
    return {
        "country": "CO",
        "portal": _bogota.portal.host,
        "city": _bogota.portal.city,
        "count": len(formatted),
        "organizations": formatted,
    }


@mcp.tool(annotations=_ro("Grupos temáticos (Bogotá)"))
async def bogota_list_groups(
    limit: Annotated[int, Field(description="Max groups (1-200)", ge=1, le=200)] = 50,
) -> dict:
    """Thematic groups on Bogotá's portal (CKAN's category analogue)."""
    try:
        items = await _bogota.group_list(limit=limit)
    except ckan.CkanError as e:
        return _err(e, _BOGOTA_HINT)
    formatted = [ckan.format_group(g, _bogota.portal) for g in items]
    return {
        "country": "CO",
        "portal": _bogota.portal.host,
        "city": _bogota.portal.city,
        "count": len(formatted),
        "groups": formatted,
    }


@mcp.tool(annotations=_ro("Etiquetas (Bogotá)"))
async def bogota_list_tags(
    limit: Annotated[int, Field(description="Max tags (1-1000)", ge=1, le=1000)] = 100,
) -> dict:
    """Tags published on Bogotá's portal (the catalog carries about 3,200)."""
    try:
        items = await _bogota.tag_list(limit=limit)
    except ckan.CkanError as e:
        return _err(e, _BOGOTA_HINT)
    return {
        "country": "CO",
        "portal": _bogota.portal.host,
        "count": len(items),
        "tags": items,
    }


@mcp.tool(annotations=_ro("Estadísticas del portal (Bogotá)"))
async def bogota_get_site_stats() -> dict:
    """Portal-wide stats for Bogotá, including what the DataStore can and cannot do.

    The `datastore_sql_available: false` field is not incidental: it is why
    there is no bogota_aggregate_* tool.
    """
    try:
        return await _bogota.site_stats()
    except ckan.CkanError as e:
        return _err(e, _BOGOTA_HINT)


@mcp.tool(annotations=_ro("Vista previa de filas (Bogotá)"))
async def bogota_resource_preview(
    resource_id: Annotated[
        str,
        Field(
            description=(
                "Resource UUID from bogota_get_dataset. The resource must have `queryable: true`."
            )
        ),
    ],
    rows: Annotated[int, Field(description="Rows to return (1-1000).", ge=1, le=1000)] = 20,
    offset: Annotated[int, Field(description="Rows to skip.", ge=0)] = 0,
) -> dict:
    """First N rows of a DataStore-backed Bogotá resource, with its column types.

    Call this before bogota_filter_resource to learn the exact column names —
    Bogotá's are Spanish, irregularly cased, and often contain spaces.
    """
    if not ckan.is_valid_uuid(resource_id):
        return {
            "error": f"Not a valid resource UUID: {resource_id!r}",
            "hint": "Take it from the 'resources[].id' field of bogota_get_dataset.",
        }
    try:
        body = await _bogota.datastore_search(resource_id, limit=rows, offset=offset)
    except ckan.CkanError as e:
        return _err(
            e,
            _BOGOTA_HINT + " If the resource is not DataStore-backed, only its "
            "download URL is available.",
            resource_id=resource_id,
        )
    return ckan.format_datastore_result(body, resource_id, _bogota.portal)


@mcp.tool(annotations=_ro("Filtrar recurso (Bogotá)"))
async def bogota_filter_resource(
    resource_id: Annotated[str, Field(description="Resource UUID from bogota_get_dataset.")],
    filters: Annotated[
        dict | None,
        Field(
            description=(
                "Exact-match filters as {column: value}, AND-combined. "
                'Example: {"Localidad": "Bosa"}. CKAN matches these exactly; '
                "there are no ranges or comparisons — use `q` for substring search."
            )
        ),
    ] = None,
    columns: Annotated[
        list[str] | None,
        Field(description="Column names to return. None = all columns."),
    ] = None,
    q: Annotated[
        str | None,
        Field(description="Full-text search across the whole row."),
    ] = None,
    sort: Annotated[
        str | None,
        Field(description='Sort spec, e.g. "Ano desc" or "Localidad asc, Ano desc".'),
    ] = None,
    limit: Annotated[int, Field(description="Max rows (1-1000)", ge=1, le=1000)] = 100,
    offset: Annotated[int, Field(description="Rows to skip.", ge=0)] = 0,
) -> dict:
    """Typed server-side filter against a DataStore-backed Bogotá resource.

    This is the CKAN counterpart of filter_dataset. The differences are real
    and worth knowing: `filters` does exact matching only (CKAN sends them as
    a JSON object, not as SQL), and there is no aggregation — this portal does
    not expose datastore_search_sql, so GROUP BY has to be done by the caller
    over the returned rows.
    """
    if not ckan.is_valid_uuid(resource_id):
        return {
            "error": f"Not a valid resource UUID: {resource_id!r}",
            "hint": "Take it from the 'resources[].id' field of bogota_get_dataset.",
        }
    try:
        body = await _bogota.datastore_search(
            resource_id,
            fields=columns,
            filters=filters,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    except ckan.CkanError as e:
        return _err(e, _BOGOTA_HINT, resource_id=resource_id)
    result = ckan.format_datastore_result(body, resource_id, _bogota.portal)
    result["filters_applied"] = filters or {}
    return result


# ─── Entry point ────────────────────────────────────────────────────────────


def _tool_count() -> int | None:
    try:
        return len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
    except Exception:
        return None


async def _close_clients() -> None:
    await _client.close()
    await _bogota.close()


def main() -> None:
    logger.info(
        "colombian-open-data-mcp %s starting (portals: %s, %s)",
        __version__,
        socrata.PORTAL_HOST,
        ckan.BOGOTA.host,
    )
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
