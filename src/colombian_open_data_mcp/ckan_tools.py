"""The eight ``city_*`` tools, shared by every CKAN portal.

Until v0.4 each portal got its own family of eight prefixed tools —
``bogota_search_datasets``, ``cali_search_datasets`` and so on. With two
portals that was sixteen tools and defensible. With four it would be
thirty-two, and thirty-two near-identical schemas is a lot of context spent to
say the same thing four times. So the family collapsed: one set of eight tools
taking a ``city`` parameter, covering twice the portals in a quarter of the
surface. A fifth portal is now a descriptor in :mod:`.ckan` and nothing else.

**Why this does not contradict the decision against a ``portal`` parameter.**
That earlier argument was about Socrata versus CKAN, and it still stands: the
national portal uses 4x4 identifiers and offers server-side aggregation, the
city portals use UUIDs and slugs and cannot aggregate at all. A parameter
spanning those two would have to branch its validation and would advertise
capabilities half its values do not have. None of that applies *between* CKAN
portals — the four take the same identifiers, expose the same actions, and
refuse ``datastore_search_sql`` alike. The dividing line was always the
platform, not the city.

What is not shared is the description text, which is assembled per portal from
the descriptors so a model reading the schema sees the real catalogue sizes and
the real coverage figures rather than a generic sentence.

Two implementation constraints, both learned the hard way:

1. **This module deliberately omits ``from __future__ import annotations``.**
   With it, every annotation becomes a source string that FastMCP later
   evaluates against *module* globals. The annotations here reference
   :data:`ckan.CityKey` through the module object, which survives that, but the
   earlier per-portal factory interpolated a closure variable and produced
   ``NameError: name 'p' is not defined`` at import. The missing import stays
   out: this module registers tools inside a function, and that is exactly the
   situation the import breaks.
2. **Descriptions are passed to the decorator, not assigned afterwards.**
   ``mcp.tool()`` reads ``__doc__`` at decoration time, so setting
   ``func.__doc__`` on the line below registers a tool with no description at
   all — invisible until you inspect ``tools/list`` and find every description
   empty. A test asserts no tool ships without one.
"""

import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import ckan, esri, tabular
from .netguard import NetGuardError

logger = logging.getLogger(__name__)

TOOL_NAMES = (
    "city_search_datasets",
    "city_get_dataset",
    "city_list_organizations",
    "city_list_groups",
    "city_list_tags",
    "city_get_site_stats",
    "city_resource_preview",
    "city_filter_resource",
    "city_esri_service_info",
    "city_esri_query",
    "city_esri_aggregate",
    "city_read_resource_file",
)


def portal_table() -> str:
    """The four portals as a compact list for the tool descriptions.

    A model choosing a ``city`` value benefits from knowing that Bogotá is
    thirty times larger than Cartagena but returns rows a third as often. That
    is exactly the kind of thing it cannot infer and will otherwise learn by
    wasting a turn.
    """
    return "\n".join(
        f"- `{p.key}` — {p.name} ({p.city}), about {p.approx_datasets:,} datasets, "
        f"DataStore returns rows for {p.datastore_coverage}."
        for p in ckan.PORTALS.values()
    )


CITY_PARAM_DESC = (
    "Which territorial portal to query:\n"
    "- `bogota` — Bogotá D.C., the largest catalogue but the least reliable "
    "DataStore (about 27% of datasets return rows).\n"
    "- `cali` — Santiago de Cali (67%; a quarter of its catalogue is "
    "cartographic image bundles no tabular tool can read).\n"
    "- `valle` — Valle del Cauca, department-level, small and complete (100%).\n"
    "- `cartagena` — Cartagena de Indias, small and almost complete (97%)."
)

# Module level on purpose. A type alias defined inside ``register`` is a local
# variable, and neither mypy nor FastMCP's schema builder accepts a local in an
# annotation position — the first attempt did exactly that and mypy called it
# "Variable not allowed in type expression". Here it is a real alias that
# resolves against module globals, which is also the only form that survives
# FastMCP evaluating annotations lazily.
City = Annotated[ckan.CityKey, Field(description=CITY_PARAM_DESC)]


def register(
    mcp: FastMCP,
    clients: dict[str, ckan.CkanClient],
    esri_clients: dict[str, esri.EsriClient],
    *,
    ro: Callable[[str], ToolAnnotations],
    err: Callable[..., dict],
) -> dict[str, ckan.CkanClient]:
    """Register the eight ``city_*`` tools and return the client registry.

    ``ro`` and ``err`` are injected rather than imported so this module never
    imports ``server``, which imports it.

    ``clients`` is the live registry, read on every call rather than captured
    once. That indirection exists for one reason: a tool that closes over its
    client can only be redirected by rewriting a closure cell, which is exactly
    the kind of test that breaks on a Python upgrade. Reading the registry lets
    a test swap ``server._ckan_clients[key]`` like any other attribute.
    """
    for portal in ckan.PORTALS.values():
        clients[portal.key] = ckan.CkanClient(portal)

    listing = portal_table()
    esri_client = esri_clients.setdefault("esri", esri.EsriClient())

    def resolve(city: str) -> ckan.CkanPortal:
        return ckan.PORTALS[city]

    def hint_for(city: str) -> str:
        host = ckan.PORTALS[city].host if city in ckan.PORTALS else "the portal"
        return (
            f"The {host} portal may be temporarily unavailable, or a WAF rejected "
            "the request. Retry, or narrow the query."
        )

    def bad_city(city: str) -> dict:
        return {
            "error": f"Unknown city {city!r}",
            "hint": f"Use one of: {', '.join(ckan.PORTALS)}.",
        }

    def bad_slug(value: str) -> dict:
        return {
            "error": f"Not a valid dataset id or slug: {value!r}",
            "hint": "Pass the 'id' (UUID) or 'name' (slug) from city_search_datasets.",
        }

    def bad_uuid(value: str) -> dict:
        return {
            "error": f"Not a valid resource UUID: {value!r}",
            "hint": "Take it from the 'resources[].id' field of city_get_dataset.",
        }

    def envelope(portal: ckan.CkanPortal, **extra: Any) -> dict:
        return {"country": "CO", "portal": portal.host, "city": portal.city, **extra}

    # ── Catalogue ────────────────────────────────────────────────────────────

    @mcp.tool(
        name="city_search_datasets",
        annotations=ro("Buscar datasets (ciudad/departamento)"),
        description=(
            "Search the open data catalogue of one Colombian territorial portal.\n\n"
            f"{listing}\n\n"
            "Each returned dataset lists its resources, and each resource carries a "
            "`queryable` flag. Treat that flag as a hint, not a promise: these portals "
            "mark some resources as DataStore-backed that have no table behind them, "
            "and those answer with an error saying exactly that. When a dataset is not "
            "queryable, `city_esri_query` and `city_read_resource_file` often reach it "
            "anyway.\n\n"
            "For national-level data use search_datasets (no prefix) instead."
        ),
    )
    async def city_search_datasets(
        city: City,
        query: Annotated[
            str | None,
            Field(description="Free-text search. Omit to list the whole catalogue."),
        ] = None,
        organization: Annotated[
            str | None,
            Field(description="Organization slug, from city_list_organizations."),
        ] = None,
        group: Annotated[
            str | None, Field(description="Group slug, from city_list_groups.")
        ] = None,
        tag: Annotated[str | None, Field(description="Tag name.")] = None,
        limit: Annotated[int, Field(description="Results (1-100)", ge=1, le=100)] = 10,
        offset: Annotated[int, Field(description="Offset for pagination.", ge=0)] = 0,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        portal = resolve(city)
        try:
            body = await clients[city].package_search(
                query=query,
                organization=organization,
                group=group,
                tag=tag,
                rows=limit,
                start=offset,
            )
        except ckan.CkanError as e:
            return err(e, hint_for(city))
        return ckan.format_search_response(body, portal)

    @mcp.tool(
        name="city_get_dataset",
        annotations=ro("Metadatos de dataset (ciudad/departamento)"),
        description=(
            "Full metadata for one territorial dataset, including every resource.\n\n"
            "Use this to find the resource UUID needed by city_filter_resource, "
            "city_resource_preview, city_esri_query or city_read_resource_file. Each "
            "resource's `format` field tells you which of those will work: a "
            "`queryable: true` resource goes to the DataStore tools, an `ESRI REST` "
            "resource to the ESRI tools, and a CSV/XLSX/JSON resource to the file "
            "reader."
        ),
    )
    async def city_get_dataset(
        city: City,
        id: Annotated[
            str,
            Field(
                description=(
                    "Dataset UUID or URL slug, from city_search_datasets "
                    "(the 'id' or 'name' field)."
                )
            ),
        ] = "",
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        if not ckan.is_valid_dataset_id(id):
            return bad_slug(id)
        try:
            d = await clients[city].package_show(id)
        except ckan.CkanError as e:
            return err(e, hint_for(city), dataset_id=id)
        return ckan.format_dataset(d, resolve(city))

    @mcp.tool(
        name="city_list_organizations",
        annotations=ro("Entidades publicadoras (ciudad/departamento)"),
        description=(
            "Publishing entities of one territorial government, with dataset counts. "
            "Their slugs are what city_search_datasets accepts as `organization`."
        ),
    )
    async def city_list_organizations(
        city: City,
        limit: Annotated[int, Field(description="Max organizations (1-200)", ge=1, le=200)] = 60,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        portal = resolve(city)
        try:
            items = await clients[city].organization_list(limit=limit)
        except ckan.CkanError as e:
            return err(e, hint_for(city))
        formatted = [ckan.format_organization(o, portal) for o in items]
        return envelope(portal, count=len(formatted), organizations=formatted)

    @mcp.tool(
        name="city_list_groups",
        annotations=ro("Grupos temáticos (ciudad/departamento)"),
        description=(
            "Thematic groups on one territorial portal — CKAN's analogue of a "
            "category. Their slugs are what city_search_datasets accepts as `group`."
        ),
    )
    async def city_list_groups(
        city: City,
        limit: Annotated[int, Field(description="Max groups (1-200)", ge=1, le=200)] = 50,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        portal = resolve(city)
        try:
            items = await clients[city].group_list(limit=limit)
        except ckan.CkanError as e:
            return err(e, hint_for(city))
        formatted = [ckan.format_group(g, portal) for g in items]
        return envelope(portal, count=len(formatted), groups=formatted)

    @mcp.tool(
        name="city_list_tags",
        annotations=ro("Etiquetas (ciudad/departamento)"),
        description="Tags published on one territorial portal.",
    )
    async def city_list_tags(
        city: City,
        limit: Annotated[int, Field(description="Max tags (1-1000)", ge=1, le=1000)] = 100,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        portal = resolve(city)
        try:
            items = await clients[city].tag_list(limit=limit)
        except ckan.CkanError as e:
            return err(e, hint_for(city))
        return envelope(portal, count=len(items), tags=items)

    @mcp.tool(
        name="city_get_site_stats",
        annotations=ro("Estadísticas del portal (ciudad/departamento)"),
        description=(
            "Portal-wide stats for one territory, including what its DataStore can "
            "and cannot do.\n\n"
            "The `datastore_sql_available: false` field is not incidental: it is why "
            "there is no city aggregation tool. None of these four portals can GROUP "
            "BY server-side, so an aggregation has to be computed over rows returned "
            "by city_filter_resource — or, for an ESRI-backed layer, by "
            "city_esri_aggregate, which is the one place a territorial rollup does "
            "run on the server."
        ),
    )
    async def city_get_site_stats(city: City) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        try:
            return await clients[city].site_stats()
        except ckan.CkanError as e:
            return err(e, hint_for(city))

    # ── DataStore ────────────────────────────────────────────────────────────

    @mcp.tool(
        name="city_resource_preview",
        annotations=ro("Vista previa de filas (ciudad/departamento)"),
        description=(
            "First N rows of a DataStore-backed territorial resource, with its column "
            "types.\n\n"
            "Call this before city_filter_resource to learn the exact column names — "
            "they are Spanish, irregularly cased, and often contain spaces.\n\n"
            "If this answers that no table exists, the resource is one the catalogue "
            "flags wrongly; try city_read_resource_file instead, which reads the "
            "published file directly."
        ),
    )
    async def city_resource_preview(
        city: City,
        resource_id: Annotated[
            str,
            Field(
                description=(
                    "Resource UUID from city_get_dataset. The resource should have "
                    "`queryable: true`."
                )
            ),
        ] = "",
        rows: Annotated[int, Field(description="Rows to return (1-1000).", ge=1, le=1000)] = 20,
        offset: Annotated[int, Field(description="Rows to skip.", ge=0)] = 0,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        if not ckan.is_valid_uuid(resource_id):
            return bad_uuid(resource_id)
        try:
            body = await clients[city].datastore_search(resource_id, limit=rows, offset=offset)
        except ckan.CkanError as e:
            return err(e, hint_for(city), resource_id=resource_id)
        return ckan.format_datastore_result(body, resource_id, resolve(city))

    @mcp.tool(
        name="city_filter_resource",
        annotations=ro("Filtrar recurso (ciudad/departamento)"),
        description=(
            "Typed server-side filter against a DataStore-backed territorial "
            "resource.\n\n"
            "This is the CKAN counterpart of filter_dataset, and the differences are "
            "real: `filters` does exact matching only — CKAN receives them as a JSON "
            "object, not as SQL — and there is no aggregation, because none of these "
            "portals exposes datastore_search_sql. A GROUP BY has to be computed by "
            "the caller over the rows this returns."
        ),
    )
    async def city_filter_resource(
        city: City,
        resource_id: Annotated[str, Field(description="Resource UUID from city_get_dataset.")] = "",
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
        if city not in ckan.PORTALS:
            return bad_city(city)
        if not ckan.is_valid_uuid(resource_id):
            return bad_uuid(resource_id)
        try:
            body = await clients[city].datastore_search(
                resource_id,
                fields=columns,
                filters=filters,
                q=q,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        except ckan.CkanError as e:
            return err(e, hint_for(city), resource_id=resource_id)
        result = ckan.format_datastore_result(body, resource_id, resolve(city))
        result["filters_applied"] = filters or {}
        return result

    # ── Beyond the DataStore ─────────────────────────────────────────────────
    #
    # Everything below reaches a host the catalogue names rather than one this
    # source file names. Two things keep that safe. The model never supplies a
    # URL — it supplies a resource UUID, and the address is looked up in the
    # portal's own catalogue by `resource_address` below. And every request goes
    # out through a client carrying netguard's request hook, so the address and
    # each redirect hop are checked against the SSRF policy before a socket
    # opens. See netguard.py for what that policy refuses and why.

    async def resource_address(city: str, resource_id: str) -> tuple[str, str | None]:
        """The URL and declared format of one resource, from the catalogue."""
        res = await clients[city].resource_show(resource_id)
        url = (res.get("url") or "").strip()
        if not url:
            raise ckan.CkanError(f"Resource {resource_id} has no URL registered in the catalogue.")
        return url, res.get("format")

    def guard_error(e: NetGuardError, **extra: Any) -> dict:
        return {
            "error": str(e),
            "hint": (
                "The address this resource points at was refused by the server's "
                "network policy. This is a property of the address, not of your "
                "request; nothing you can pass will change it."
            ),
            **extra,
        }

    @mcp.tool(
        name="city_esri_service_info",
        annotations=ro("Metadatos de servicio ESRI (ciudad/departamento)"),
        description=(
            "Describe the ArcGIS REST layer behind a resource: its fields, its "
            "geometry type, and whether it supports server-side statistics.\n\n"
            "A large share of Bogotá's catalogue that the DataStore cannot reach is "
            "published this way — 334 of its 1,917 datasets carry an `ESRI REST` "
            "resource. Call this first to learn the field names, then "
            "city_esri_query for rows or city_esri_aggregate for a rollup.\n\n"
            "If the resource address is a service root rather than one layer, the "
            "answer lists the available layers and you pass one back as `layer`."
        ),
    )
    async def city_esri_service_info(
        city: City,
        resource_id: Annotated[
            str,
            Field(
                description=(
                    "Resource UUID from city_get_dataset. Look for a resource whose "
                    "`format` is 'ESRI REST'."
                )
            ),
        ] = "",
        layer: Annotated[
            int | None,
            Field(description="Layer number, only when the address is a service root."),
        ] = None,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        if not ckan.is_valid_uuid(resource_id):
            return bad_uuid(resource_id)
        try:
            url, _ = await resource_address(city, resource_id)
            target = esri.layer_url(url, layer) if layer is not None else url.rstrip("/")
            body = await esri_client.service_info(target)
        except NetGuardError as e:
            return guard_error(e, resource_id=resource_id)
        except (ckan.CkanError, esri.EsriError) as e:
            return err(e, hint_for(city), resource_id=resource_id)
        info = esri.format_service_info(body, target)
        # How many features the layer holds decides the next call: a few hundred
        # can simply be listed, forty thousand should be aggregated. It costs one
        # extra request and is worth it. A service root has no count, and some
        # layers refuse the query — neither is a failure of this call, so the
        # field is simply absent rather than the whole answer being lost.
        if esri.is_layer_url(target):
            try:
                info["feature_count"] = await esri_client.count(target)
            except (esri.EsriError, NetGuardError):
                info["feature_count"] = None
        return info

    @mcp.tool(
        name="city_esri_query",
        annotations=ro("Consultar capa ESRI (ciudad/departamento)"),
        description=(
            "Rows from an ArcGIS REST layer, filtered and paginated on the server.\n\n"
            "This reaches data no city DataStore holds. `where` is a plain SQL "
            "condition the ArcGIS service evaluates — `LOCALIDAD = 'SUBA'`, "
            "`AREA > 1000`, `NOMBRE LIKE '%PARQUE%'` — and column names come from "
            "city_esri_service_info.\n\n"
            "Geometry is never returned. A polygon layer would answer with thousands "
            "of coordinates per row and no additional information: the attributes are "
            "the data."
        ),
    )
    async def city_esri_query(
        city: City,
        resource_id: Annotated[
            str, Field(description="Resource UUID of an ESRI REST resource.")
        ] = "",
        where: Annotated[
            str | None,
            Field(
                description=(
                    "SQL condition, e.g. \"LOCALIDAD = 'USME' AND AREA > 500\". Omit for "
                    "all rows. Double an apostrophe inside a literal: 'BOGOTA D''C'."
                )
            ),
        ] = None,
        columns: Annotated[
            list[str] | None,
            Field(description="Field names to return. None = every field."),
        ] = None,
        layer: Annotated[
            int | None, Field(description="Layer number, if the address is a service root.")
        ] = None,
        limit: Annotated[int, Field(description="Max rows (1-1000).", ge=1, le=1000)] = 50,
        offset: Annotated[int, Field(description="Rows to skip.", ge=0)] = 0,
        order_by: Annotated[
            str | None, Field(description='Sort spec, e.g. "AREA desc" or "NOMBRE asc".')
        ] = None,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        if not ckan.is_valid_uuid(resource_id):
            return bad_uuid(resource_id)
        try:
            url, _ = await resource_address(city, resource_id)
            target = esri.layer_url(url, layer)
            body = await esri_client.query(
                target,
                where=where,
                out_fields=columns,
                limit=limit,
                offset=offset,
                order_by=order_by,
            )
        except NetGuardError as e:
            return guard_error(e, resource_id=resource_id)
        except (ckan.CkanError, esri.EsriError) as e:
            return err(e, hint_for(city), resource_id=resource_id)
        result = esri.format_features(body, target)
        result["city"] = ckan.PORTALS[city].city
        return result

    @mcp.tool(
        name="city_esri_aggregate",
        annotations=ro("Agregar capa ESRI (ciudad/departamento)"),
        description=(
            "Server-side GROUP BY over an ArcGIS REST layer.\n\n"
            "This is the only territorial aggregation in this server that runs "
            "remotely. None of the four CKAN portals exposes datastore_search_sql, so "
            "a DataStore rollup has to be summed over rows in your own context; an "
            "ESRI layer computes it and returns only the grouped result. Counting "
            "parks by locality across a 40,000-row layer costs one call and returns "
            "twenty rows.\n\n"
            "Check `supports_statistics` in city_esri_service_info first — a few "
            "layers do not offer it."
        ),
    )
    async def city_esri_aggregate(
        city: City,
        resource_id: Annotated[
            str, Field(description="Resource UUID of an ESRI REST resource.")
        ] = "",
        aggregations: Annotated[
            list[dict] | None,
            Field(
                description=(
                    "List of {'fn': ..., 'col': ..., 'alias': ...}. `fn` is one of "
                    "count, sum, min, max, avg, stddev, var. ArcGIS has no count(*), "
                    "so `col` is always required — use the object-id field for a plain "
                    'row count. Example: [{"fn": "count", "col": "OBJECTID", '
                    '"alias": "n"}].'
                )
            ),
        ] = None,
        group_by: Annotated[
            list[str] | None,
            Field(description='Fields to group by, e.g. ["LOCALIDAD"]. None = one total row.'),
        ] = None,
        where: Annotated[
            str | None, Field(description="SQL condition applied before grouping.")
        ] = None,
        layer: Annotated[
            int | None, Field(description="Layer number, if the address is a service root.")
        ] = None,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        if not ckan.is_valid_uuid(resource_id):
            return bad_uuid(resource_id)
        if not aggregations:
            return {
                "error": "At least one aggregation is required.",
                "hint": (
                    'Pass [{"fn": "count", "col": "<object id field>", "alias": "n"}]. '
                    "city_esri_service_info lists the field names."
                ),
            }
        try:
            url, _ = await resource_address(city, resource_id)
            target = esri.layer_url(url, layer)
            body = await esri_client.aggregate(
                target, aggregations=aggregations, group_by=group_by, where=where
            )
        except NetGuardError as e:
            return guard_error(e, resource_id=resource_id)
        except (ckan.CkanError, esri.EsriError) as e:
            return err(e, hint_for(city), resource_id=resource_id)
        result = esri.format_features(body, target)
        result["city"] = ckan.PORTALS[city].city
        result["grouped_by"] = group_by or []
        result["aggregated_on_server"] = True
        return result

    @mcp.tool(
        name="city_read_resource_file",
        annotations=ro("Leer archivo publicado (ciudad/departamento)"),
        description=(
            "Download a published CSV, XLSX or JSON resource and return its first "
            "rows.\n\n"
            "This is the last resort and it is the one that closes Bogotá's gap: many "
            "of its datasets exist only as a file at a download URL, with no DataStore "
            "table and no ESRI service. Reach for it when city_resource_preview "
            "answers that no table exists.\n\n"
            "Nothing is stored. The file is streamed under a byte cap, the first rows "
            "are parsed, and the bytes are discarded — so a large file answers with a "
            "sample and says so, rather than with everything. Geospatial archives "
            "(SHP, GPKG, KML), PDFs and images are refused with an explanation; they "
            "are not tables."
        ),
    )
    async def city_read_resource_file(
        city: City,
        resource_id: Annotated[
            str,
            Field(
                description=(
                    "Resource UUID from city_get_dataset. Its `format` should be CSV, "
                    "XLSX, XLS, JSON, GEOJSON, TSV or TXT."
                )
            ),
        ] = "",
        rows: Annotated[int, Field(description="Rows to return (1-500).", ge=1, le=500)] = 25,
    ) -> dict:
        if city not in ckan.PORTALS:
            return bad_city(city)
        if not ckan.is_valid_uuid(resource_id):
            return bad_uuid(resource_id)
        try:
            url, fmt = await resource_address(city, resource_id)
            result = await tabular.read_resource_file(url, fmt, rows=rows)
        except NetGuardError as e:
            return guard_error(e, resource_id=resource_id)
        except (ckan.CkanError, tabular.TabularError) as e:
            return err(e, hint_for(city), resource_id=resource_id)
        portal = resolve(city)
        return {
            "country": "CO",
            "portal": portal.host,
            "city": portal.city,
            "resource_id": resource_id,
            **result,
        }

    logger.debug("registered %d city tools over %d portals", len(TOOL_NAMES), len(ckan.PORTALS))
    return clients
