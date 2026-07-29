"""Tool factory for the CKAN city portals — Bogotá and Cali.

Both run CKAN 2.10.4 with the same extensions and refuse
``datastore_search_sql`` the same way, so their eight tools are generated from
one definition per portal rather than written out twice. That is not an
abstraction reached for early: it is the second and third instance, the shapes
are identical, and hand-copying them would mean a fix landing in one family and
not the other — which is how the Dominican client ended up with its host baked
into three separate places.

What is **not** shared is the prose. Each portal's tool and parameter
descriptions name its own catalogue, its own sibling tools and its own quirks,
because that text is what a model reads when it decides which portal to ask.

Two implementation constraints, both learned the hard way:

1. **This module deliberately omits ``from __future__ import annotations``.**
   With it, every annotation becomes a source string that FastMCP later
   evaluates against *module* globals — and these annotations interpolate the
   portal prefix, which is a closure variable. The result is
   ``NameError: name 'p' is not defined`` at import. The missing import is
   load-bearing; do not add it.
2. **Descriptions are passed to the decorator, not assigned afterwards.**
   ``mcp.tool()`` reads ``__doc__`` at decoration time, so setting
   ``func.__doc__`` on the line below registers a tool with no description at
   all — which is invisible until you inspect ``tools/list`` and find every
   description empty.
"""

import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import ckan

logger = logging.getLogger(__name__)


def register(
    mcp: FastMCP,
    portal: ckan.CkanPortal,
    clients: dict[str, ckan.CkanClient],
    *,
    ro: Callable[[str], ToolAnnotations],
    err: Callable[..., dict],
) -> ckan.CkanClient:
    """Register the eight tools for one CKAN portal and return its client.

    ``ro`` and ``err`` are injected rather than imported so this module never
    imports ``server``, which imports it.

    ``clients`` is the live registry, read on every call rather than captured
    once. That indirection exists for one reason: a tool that closes over its
    client can only be redirected by rewriting a closure cell, which is exactly
    the kind of test that breaks on a Python upgrade. Reading the registry lets
    a test swap ``server._ckan_clients[key]`` like any other attribute.
    """
    client = ckan.CkanClient(portal)
    clients[portal.key] = client
    key = portal.key
    p = portal.prefix
    city = portal.city
    host = portal.host

    hint = (
        f"The {host} portal may be temporarily unavailable, or a WAF rejected "
        "the request. Retry, or narrow the query."
    )

    def bad_slug(value: str) -> dict:
        return {
            "error": f"Not a valid dataset id or slug: {value!r}",
            "hint": f"Pass the 'id' (UUID) or 'name' (slug) from {p}_search_datasets.",
        }

    def bad_uuid(value: str) -> dict:
        return {
            "error": f"Not a valid resource UUID: {value!r}",
            "hint": f"Take it from the 'resources[].id' field of {p}_get_dataset.",
        }

    def envelope(**extra: Any) -> dict:
        return {"country": "CO", "portal": host, "city": city, **extra}

    # ── Catalogue ────────────────────────────────────────────────────────────

    @mcp.tool(
        name=f"{p}_search_datasets",
        annotations=ro(f"Buscar datasets ({city})"),
        description=(
            f"Search {city}'s open data catalogue (about {portal.approx_datasets:,} "
            "datasets, CKAN).\n\n"
            "Each returned dataset lists its resources, and each resource carries a "
            "`queryable` flag. Treat that flag as a hint, not a promise: the portal "
            "marks some resources as DataStore-backed that have no table behind them, "
            "and those answer with an error saying exactly that.\n\n"
            "For national-level data use search_datasets (no prefix) instead."
        ),
    )
    async def search_datasets_(
        query: Annotated[
            str | None,
            Field(description="Free-text search. Omit to list the whole catalogue."),
        ] = None,
        organization: Annotated[
            str | None,
            Field(description=f"Organization slug, from {p}_list_organizations."),
        ] = None,
        group: Annotated[
            str | None, Field(description=f"Group slug, from {p}_list_groups.")
        ] = None,
        tag: Annotated[str | None, Field(description="Tag name.")] = None,
        limit: Annotated[int, Field(description="Results (1-100)", ge=1, le=100)] = 10,
        offset: Annotated[int, Field(description="Offset for pagination.", ge=0)] = 0,
    ) -> dict:
        try:
            body = await clients[key].package_search(
                query=query,
                organization=organization,
                group=group,
                tag=tag,
                rows=limit,
                start=offset,
            )
        except ckan.CkanError as e:
            return err(e, hint)
        return ckan.format_search_response(body, portal)

    @mcp.tool(
        name=f"{p}_get_dataset",
        annotations=ro(f"Metadatos de dataset ({city})"),
        description=(
            f"Full metadata for one {city} dataset, including every resource.\n\n"
            f"Use this to find the resource UUID needed by {p}_filter_resource or "
            f"{p}_resource_preview."
        ),
    )
    async def get_dataset_(
        id: Annotated[
            str,
            Field(
                description=(
                    f"Dataset UUID or URL slug, from {p}_search_datasets "
                    "(the 'id' or 'name' field)."
                )
            ),
        ],
    ) -> dict:
        if not ckan.is_valid_dataset_id(id):
            return bad_slug(id)
        try:
            d = await clients[key].package_show(id)
        except ckan.CkanError as e:
            return err(e, hint, dataset_id=id)
        return ckan.format_dataset(d, portal)

    @mcp.tool(
        name=f"{p}_list_organizations",
        annotations=ro(f"Entidades publicadoras ({city})"),
        description=(
            f"Publishing entities of the {city} city government, with dataset counts. "
            f"Their slugs are what {p}_search_datasets accepts as `organization`."
        ),
    )
    async def list_organizations_(
        limit: Annotated[int, Field(description="Max organizations (1-200)", ge=1, le=200)] = 60,
    ) -> dict:
        try:
            items = await clients[key].organization_list(limit=limit)
        except ckan.CkanError as e:
            return err(e, hint)
        formatted = [ckan.format_organization(o, portal) for o in items]
        return envelope(count=len(formatted), organizations=formatted)

    @mcp.tool(
        name=f"{p}_list_groups",
        annotations=ro(f"Grupos temáticos ({city})"),
        description=(
            f"Thematic groups on the {city} portal — CKAN's analogue of a category. "
            f"Their slugs are what {p}_search_datasets accepts as `group`."
        ),
    )
    async def list_groups_(
        limit: Annotated[int, Field(description="Max groups (1-200)", ge=1, le=200)] = 50,
    ) -> dict:
        try:
            items = await clients[key].group_list(limit=limit)
        except ckan.CkanError as e:
            return err(e, hint)
        formatted = [ckan.format_group(g, portal) for g in items]
        return envelope(count=len(formatted), groups=formatted)

    @mcp.tool(
        name=f"{p}_list_tags",
        annotations=ro(f"Etiquetas ({city})"),
        description=f"Tags published on the {city} portal.",
    )
    async def list_tags_(
        limit: Annotated[int, Field(description="Max tags (1-1000)", ge=1, le=1000)] = 100,
    ) -> dict:
        try:
            items = await clients[key].tag_list(limit=limit)
        except ckan.CkanError as e:
            return err(e, hint)
        return envelope(count=len(items), tags=items)

    @mcp.tool(
        name=f"{p}_get_site_stats",
        annotations=ro(f"Estadísticas del portal ({city})"),
        description=(
            f"Portal-wide stats for {city}, including what its DataStore can and "
            "cannot do.\n\n"
            "The `datastore_sql_available: false` field is not incidental: it is why "
            f"there is no {p}_aggregate_* tool. This portal cannot GROUP BY "
            "server-side, so an aggregation has to be computed over rows returned by "
            f"{p}_filter_resource."
        ),
    )
    async def get_site_stats_() -> dict:
        try:
            return await clients[key].site_stats()
        except ckan.CkanError as e:
            return err(e, hint)

    # ── DataStore ────────────────────────────────────────────────────────────

    @mcp.tool(
        name=f"{p}_resource_preview",
        annotations=ro(f"Vista previa de filas ({city})"),
        description=(
            f"First N rows of a DataStore-backed {city} resource, with its column "
            "types.\n\n"
            f"Call this before {p}_filter_resource to learn the exact column names — "
            "they are Spanish, irregularly cased, and often contain spaces."
        ),
    )
    async def resource_preview_(
        resource_id: Annotated[
            str,
            Field(
                description=(
                    f"Resource UUID from {p}_get_dataset. The resource must have `queryable: true`."
                )
            ),
        ],
        rows: Annotated[int, Field(description="Rows to return (1-1000).", ge=1, le=1000)] = 20,
        offset: Annotated[int, Field(description="Rows to skip.", ge=0)] = 0,
    ) -> dict:
        if not ckan.is_valid_uuid(resource_id):
            return bad_uuid(resource_id)
        try:
            body = await clients[key].datastore_search(resource_id, limit=rows, offset=offset)
        except ckan.CkanError as e:
            return err(e, hint, resource_id=resource_id)
        return ckan.format_datastore_result(body, resource_id, portal)

    @mcp.tool(
        name=f"{p}_filter_resource",
        annotations=ro(f"Filtrar recurso ({city})"),
        description=(
            f"Typed server-side filter against a DataStore-backed {city} resource.\n\n"
            "This is the CKAN counterpart of filter_dataset, and the differences are "
            "real: `filters` does exact matching only — CKAN receives them as a JSON "
            "object, not as SQL — and there is no aggregation, because this portal "
            "does not expose datastore_search_sql. A GROUP BY has to be computed by "
            "the caller over the rows this returns."
        ),
    )
    async def filter_resource_(
        resource_id: Annotated[str, Field(description=f"Resource UUID from {p}_get_dataset.")],
        filters: Annotated[
            dict | None,
            Field(
                description=(
                    "Exact-match filters as {column: value}, AND-combined. "
                    'Example: {"Comuna": 22}. CKAN matches these exactly; there are '
                    "no ranges or comparisons — use `q` for substring search."
                )
            ),
        ] = None,
        columns: Annotated[
            list[str] | None,
            Field(description="Column names to return. None = all columns."),
        ] = None,
        q: Annotated[
            str | None, Field(description="Full-text search across the whole row.")
        ] = None,
        sort: Annotated[
            str | None,
            Field(description='Sort spec, e.g. "Comuna desc" or "Barrio asc, Comuna desc".'),
        ] = None,
        limit: Annotated[int, Field(description="Max rows (1-1000)", ge=1, le=1000)] = 100,
        offset: Annotated[int, Field(description="Rows to skip.", ge=0)] = 0,
    ) -> dict:
        if not ckan.is_valid_uuid(resource_id):
            return bad_uuid(resource_id)
        try:
            body = await clients[key].datastore_search(
                resource_id,
                fields=columns,
                filters=filters,
                q=q,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        except ckan.CkanError as e:
            return err(e, hint, resource_id=resource_id)
        result = ckan.format_datastore_result(body, resource_id, portal)
        result["filters_applied"] = filters or {}
        return result

    logger.debug("registered 8 tools for %s (%s)", portal.key, host)
    return client
