"""colombian-open-data-mcp — FastMCP server for datos.gov.co (Socrata).

Standard tools (paridad with the RD MCP) plus Socrata-native tools that
take advantage of SoQL: filter, aggregate, raw query, CSV preview.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import socrata, soql

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("colombian-open-data-mcp")

mcp = FastMCP("colombian-open-data-mcp")

# Singleton client; reused across all tool calls.
_client = socrata.SocrataClient()


# ─── Discovery ────────────────────────────────────────────────────────────────


@mcp.tool()
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
    tag: Annotated[
        str | None, Field(description="Tag name (e.g. 'presupuesto').")
    ] = None,
    limit: Annotated[int, Field(description="Results (1-100)", ge=1, le=100)] = 10,
    offset: Annotated[int, Field(description="Offset for pagination.", ge=0)] = 0,
) -> dict:
    """Search datasets on datos.gov.co (Colombia's open data portal).

    Returns a compact list of matching datasets with their 4x4 Socrata IDs
    (use these IDs in get_dataset / filter_dataset / aggregate_dataset).
    """
    raw = await _client.catalog_search(
        query=query, categories=category, tags=tag, limit=limit, offset=offset
    )
    return socrata.format_catalog_response(raw)


@mcp.tool()
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
    view = await _client.get_view(id)
    return socrata.format_view(view)


@mcp.tool()
async def list_recent_datasets(
    limit: Annotated[int, Field(description="Count (1-50)", ge=1, le=50)] = 10,
) -> dict:
    """Datasets most recently updated on datos.gov.co. Useful for monitoring."""
    raw = await _client.catalog_recent(limit=limit)
    return socrata.format_catalog_response(raw)


@mcp.tool()
async def list_categories() -> list[str]:
    """Top-level categories on the portal (Socrata's 'group' analogue)."""
    return await _client.domain_categories()


@mcp.tool()
async def list_tags() -> list[str]:
    """All tags published on the portal."""
    return await _client.domain_tags()


@mcp.tool()
async def list_owners(
    limit: Annotated[int, Field(description="Max owners (1-200)", ge=1, le=200)] = 50,
) -> list[dict]:
    """Dataset owners (Socrata's 'organization' analogue), with dataset count each."""
    return await _client.domain_owners(limit=limit)


@mcp.tool()
async def autocomplete(
    kind: Annotated[
        Literal["dataset", "tag", "category", "owner"],
        Field(description="Entity type to autocomplete."),
    ],
    query: Annotated[str, Field(description="Partial text to match.")],
    limit: Annotated[int, Field(description="Suggestions (1-30)", ge=1, le=30)] = 10,
) -> list[str]:
    """Autocomplete dataset / tag / category / owner names.

    Useful when the user gives a partial name and the model needs to resolve
    it to a real value before querying.
    """
    return await _client.autocomplete(kind=kind, query=query, limit=limit)


@mcp.tool()
async def get_site_stats() -> dict:
    """Portal-wide stats: total datasets, categories, tags, platform info."""
    return await _client.site_stats()


# ─── Socrata-native data tools (the differentiators vs CKAN-based MCPs) ───────


@mcp.tool()
async def download_dataset_preview(
    id: Annotated[
        str, Field(description="Socrata 4x4 dataset ID (e.g. 'abcd-1234').")
    ],
    rows: Annotated[
        int, Field(description="Rows to return (1-1000). Default 20.", ge=1, le=1000)
    ] = 20,
    offset: Annotated[
        int, Field(description="Rows to skip for pagination.", ge=0)
    ] = 0,
) -> dict:
    """Download the first N rows of a dataset directly via the Socrata data API.

    Unlike CKAN-based portals, Socrata is itself the data store: every
    dataset is queryable via JSON without downloading the whole file. This
    tool returns rows immediately, no caching needed.
    """
    if not socrata.is_valid_4x4(id):
        return {"error": f"Not a valid 4x4 id: {id!r}"}
    try:
        result = await _client.resource_query(
            id, soql.compose_soql_params(limit=rows, offset=offset)
        )
    except socrata.SocrataError as e:
        return {"error": str(e)}
    return {
        "country": "CO",
        "dataset_id": id,
        "rows_returned": len(result),
        "rows": result,
        "data_api_endpoint": f"{socrata.RESOURCE_API_BASE}/{id}.json",
    }


@mcp.tool()
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
    limit: Annotated[
        int, Field(description="Max rows (1-1000)", ge=1, le=1000)
    ] = 100,
    offset: Annotated[int, Field(description="Rows to skip.", ge=0)] = 0,
) -> dict:
    """Typed filter / select / order / limit query against a dataset via SoQL.

    Safer than writing raw SoQL — every column name is allow-list checked,
    every literal is properly escaped. Use when you know what columns and
    filters you want without needing SQL features.
    """
    if not socrata.is_valid_4x4(id):
        return {"error": f"Not a valid 4x4 id: {id!r}"}
    try:
        select = (
            ", ".join(soql.quote_ident(c) for c in columns)
            if columns
            else None
        )
        where = soql.build_where(filters)
        order = soql.build_order_by(order_by)
    except soql.SoqlError as e:
        return {"error": str(e)}

    params = soql.compose_soql_params(
        select=select, where=where, order=order, limit=limit, offset=offset
    )
    try:
        rows = await _client.resource_query(id, params)
    except socrata.SocrataError as e:
        return {"error": str(e), "params": params}
    return {
        "country": "CO",
        "dataset_id": id,
        "rows_returned": len(rows),
        "params": params,
        "rows": rows,
    }


@mcp.tool()
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
    limit: Annotated[
        int, Field(description="Max groups (1-1000)", ge=1, le=1000)
    ] = 100,
) -> dict:
    """Server-side GROUP BY + aggregation against a dataset via SoQL.

    Socrata runs the aggregation on its end and returns the rolled-up rows
    only — fast even on multi-million-row datasets, no client-side cache
    needed (unlike CKAN-based portals without DataStore).
    """
    if not socrata.is_valid_4x4(id):
        return {"error": f"Not a valid 4x4 id: {id!r}"}
    if not aggregations:
        return {"error": "aggregations cannot be empty"}
    try:
        agg_parts = [soql.build_agg_expr(a) for a in aggregations]
        group_parts = (
            [soql.quote_ident(c) for c in group_by] if group_by else []
        )
        select_clause = ", ".join([*group_parts, *agg_parts])
        where = soql.build_where(filters)
        order = soql.build_order_by(order_by)
    except soql.SoqlError as e:
        return {"error": str(e)}

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
        return {"error": str(e), "params": params}
    return {
        "country": "CO",
        "dataset_id": id,
        "groups_returned": len(rows),
        "params": params,
        "rows": rows,
    }


@mcp.tool()
async def query_dataset_soql(
    id: Annotated[str, Field(description="Socrata 4x4 dataset ID.")],
    soql_query: Annotated[
        str,
        Field(
            description=(
                "Raw SoQL query string passed as $query. Use SELECT-style "
                "SoQL only — write keywords (INSERT/UPDATE/DELETE/DROP/etc.) "
                "are rejected. "
                "Example: \"SELECT departamento, count(*) WHERE año=2024 "
                "GROUP BY departamento ORDER BY count DESC\""
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
        return {"error": f"Not a valid 4x4 id: {id!r}"}
    try:
        cleaned = soql.validate_soql(soql_query)
    except soql.SoqlError as e:
        return {"error": str(e)}

    params = {"$query": cleaned, "$limit": str(limit)}
    try:
        rows = await _client.resource_query(id, params)
    except socrata.SocrataError as e:
        return {"error": str(e), "params": params}
    return {
        "country": "CO",
        "dataset_id": id,
        "soql_executed": cleaned,
        "rows_returned": len(rows),
        "rows": rows,
    }


# ─── Entry point ──────────────────────────────────────────────────────────────


def _tool_count() -> int | None:
    try:
        return len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
    except Exception:
        return None


def main() -> None:
    logger.info(
        "colombian-open-data-mcp starting (portal: %s)", socrata.PORTAL_URL
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
            asyncio.run(_client.close())
        except RuntimeError:
            pass
        logger.info("colombian-open-data-mcp shut down")


if __name__ == "__main__":
    main()
