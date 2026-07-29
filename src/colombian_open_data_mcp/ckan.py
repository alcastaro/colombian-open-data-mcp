"""CKAN client for Colombian city portals — currently Bogotá.

Ported from the Dominican MCP's ``ckan.py``, with one substantive change: that
client hardcodes ``datos.gob.do`` in its base URL, its permalinks and its error
hints, so it can only ever serve one portal. Here the client takes a
``CkanPortal`` describing the host, which is what lets a second Colombian city
be added later without touching this module.

**What Bogotá can and cannot do**, measured against the live API on 2026-08-29:

* ``package_search``, ``package_show``, ``organization_list``, ``group_list``,
  ``tag_list`` — all work.
* ``datastore_search`` — works, and accepts ``fields``, ``filters``, ``q``,
  ``sort``, ``limit`` and ``offset``. This is what makes typed server-side
  filtering possible without downloading anything.
* ``datastore_search_sql`` — **not available.** The action is not registered
  (``HTTP 400: Action name not known``) and, separately, a WAF blocks the GET
  form of that path with a 500. So there is no server-side GROUP BY on this
  portal, and no aggregation tool is offered for it. Promising one would be
  promising something the portal cannot do.

Coverage, measured end to end over 300 randomly sampled datasets (two runs of
150, seeds 2026 and 777, see ``sweep/stress_test.py``):

* ~27% of datasets carry a resource the catalogue *flags* as DataStore-backed.
* **~13% actually return rows.** The gap is the portal's own metadata being
  wrong: of 80 resources flagged ``datastore_active``, 27 answered HTTP 404
  because no table exists for them.

An earlier estimate of 43% came from systematic rather than random offsets and
counted the flag rather than the outcome. Trust the flag for a hint, never for
a promise — which is why a 404 here is rewritten into an explanation.

The rest of the catalogue is mostly geospatial (SHP, GPKG, GEOJSON, DXF, KML)
plus a meaningful slice of queryable *services* (ESRI REST, WFS, WMS — about 9%
of resources), which are APIs rather than files and remain unexploited.
"""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass
from typing import Any

import httpx

from . import USER_AGENT

DEFAULT_TIMEOUT = 30.0

# Bogotá's catalog is large and some package_show payloads carry long
# descriptions; the same trimming the Socrata side uses keeps a single tool
# call from swamping the model's context.
NOTES_TRUNC = 300

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CkanPortal:
    """A CKAN portal this client can talk to."""

    key: str
    host: str
    name: str
    city: str
    ckan_version: str

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    @property
    def api_url(self) -> str:
        return f"https://{self.host}/api/3/action"

    def dataset_url(self, name: str) -> str:
        return f"{self.base_url}/dataset/{name}"

    def organization_url(self, name: str) -> str:
        return f"{self.base_url}/organization/{name}"

    def group_url(self, name: str) -> str:
        return f"{self.base_url}/group/{name}"


BOGOTA = CkanPortal(
    key="bogota",
    host="datosabiertos.bogota.gov.co",
    name="Datos Abiertos Bogotá",
    city="Bogotá D.C.",
    ckan_version="2.10.4",
)


# CKAN accepts either a UUID or the URL slug ("name") wherever it says "id".
# Both go straight into a query string we build, so both are checked here for
# the same reason the Socrata side checks its 4x4 ids: an unvalidated value is
# a request-splitting and parameter-injection surface.
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{1,99}$")

# DataStore column names. CKAN quotes these itself, but the allowlist is cheap
# and keeps one habit across both clients rather than two different rules a
# reader has to hold in their head.
# Built from evidence, not guesswork: 294 real column names were collected from
# DataStore resources across the catalogue and the character set below is what
# they actually use. Colons appear in legitimate names ("MES:", "Nombre:") and
# were being rejected, which is how a valid projection turned into an error.
#
# Deliberately still excluded, and the cost of each is bounded:
#   ";"  appears only in names that are a whole malformed CSV row misparsed as
#        one header (e.g. "111006;Cuenta de ahorro;69600000;..."). Garbage, and
#        a statement separator.
#   "\n" appears in 2 of 294 names. Legitimate but rare, and a raw newline in a
#        query parameter is a habit not worth keeping.
# Losing these costs projection, not access: omitting `columns` returns every
# field including the unnameable ones.
_COLUMN = re.compile(r"^[\w .\-À-ſ()/%°#:]{1,120}$")

# The allowlist above has to admit a hyphen, because real Bogotá columns use
# one. That admits "--" as a side effect, which is a SQL comment opener, so the
# denylist closes it explicitly. Same defence-in-depth shape as soql.py: an
# allowlist that must be permissive is paired with a denylist of the exact
# sequences that matter.
_COLUMN_FORBIDDEN = ("--", "/*", "*/", ";", "\x00")


class CkanError(RuntimeError):
    """Raised when a CKAN API call fails or returns an unusable body."""


def is_valid_uuid(value: str) -> bool:
    """True if value is a CKAN resource/dataset UUID."""
    return bool(_UUID.match(value or ""))


def is_valid_dataset_id(value: str) -> bool:
    """True if value is a UUID or a CKAN URL slug.

    ``package_show`` accepts both, and search results hand back the slug, so
    rejecting slugs would make the tools awkward for no security gain.
    """
    value = value or ""
    return bool(_UUID.match(value) or _SLUG.match(value))


def is_valid_column(value: str) -> bool:
    """True if value is an acceptable DataStore column name.

    Bogotá's column names are Spanish and irregular — real examples include
    ``INCLUIDOS EN VIGILANCIA CENTINELA`` and ``Año`` — so the allowlist has to
    admit spaces, accents and a few punctuation marks that a stricter identifier
    rule would reject.
    """
    value = value or ""
    if any(bad in value for bad in _COLUMN_FORBIDDEN):
        return False
    return bool(_COLUMN.match(value))


def _truncate(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


try:
    import truststore as _truststore  # type: ignore[import-not-found]

    _SSL_CTX: ssl.SSLContext | None = _truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except Exception:
    _SSL_CTX = None


class CkanClient:
    """Async CKAN client bound to one portal.

    Holds a single ``httpx.AsyncClient`` so repeated tool calls reuse the
    connection, the same arrangement the Socrata client uses.
    """

    def __init__(self, portal: CkanPortal = BOGOTA) -> None:
        self.portal = portal
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            kwargs: dict[str, Any] = dict(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
            )
            if _SSL_CTX is not None:
                kwargs["verify"] = _SSL_CTX
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def _clean(params: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in params.items() if v is not None}

    async def action(self, name: str, params: dict[str, Any] | None = None) -> Any:
        """Call one CKAN action and return its ``result``.

        CKAN answers 200 with ``success: false`` for some errors instead of a
        4xx, so the body is checked as well as the status code.
        """
        client = await self._get_client()
        url = f"{self.portal.api_url}/{name}"
        try:
            r = await client.get(url, params=self._clean(params or {}))
        except httpx.TimeoutException as e:
            raise CkanError(f"timeout calling {name} (>{DEFAULT_TIMEOUT}s)") from e
        except httpx.HTTPError as e:
            raise CkanError(f"network error calling {name}: {e}") from e

        if r.status_code >= 400:
            raise CkanError(f"HTTP {r.status_code} from {name}: {(r.text or '(no body)')[:300]}")
        try:
            body = r.json()
        except ValueError as e:
            raise CkanError(f"non-JSON response from {name}: {e}") from e

        if not isinstance(body, dict) or not body.get("success"):
            detail = ""
            if isinstance(body, dict):
                detail = str(body.get("error") or "")[:300]
            raise CkanError(f"CKAN reported failure for {name}: {detail or '(no detail)'}")
        return body.get("result")

    # ─── Catalog ─────────────────────────────────────────────────────────────

    async def package_search(
        self,
        *,
        query: str | None = None,
        organization: str | None = None,
        group: str | None = None,
        tag: str | None = None,
        rows: int = 10,
        start: int = 0,
    ) -> dict[str, Any]:
        """Search the catalog. Facet filters go through ``fq``."""
        fq_parts = []
        if organization:
            if not is_valid_dataset_id(organization):
                raise CkanError(f"Not a valid organization name: {organization!r}")
            fq_parts.append(f"organization:{organization}")
        if group:
            if not is_valid_dataset_id(group):
                raise CkanError(f"Not a valid group name: {group!r}")
            fq_parts.append(f"groups:{group}")
        if tag:
            fq_parts.append(f'tags:"{tag}"')

        result = await self.action(
            "package_search",
            {
                "q": query or "*:*",
                "fq": " AND ".join(fq_parts) if fq_parts else None,
                "rows": min(max(int(rows), 1), 100),
                "start": max(int(start), 0),
            },
        )
        if not isinstance(result, dict):
            raise CkanError("package_search returned an unexpected body")
        return result

    async def package_show(self, dataset_id: str) -> dict[str, Any]:
        if not is_valid_dataset_id(dataset_id):
            raise CkanError(f"Not a valid dataset id or slug: {dataset_id!r}")
        result = await self.action("package_show", {"id": dataset_id})
        if not isinstance(result, dict):
            raise CkanError("package_show returned an unexpected body")
        return result

    async def organization_list(self, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.action("organization_list", {"all_fields": "true"})
        items = result if isinstance(result, list) else []
        return items[: max(int(limit), 1)]

    async def group_list(self, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.action("group_list", {"all_fields": "true"})
        items = result if isinstance(result, list) else []
        return items[: max(int(limit), 1)]

    async def tag_list(self, limit: int = 100) -> list[str]:
        result = await self.action("tag_list")
        items = result if isinstance(result, list) else []
        return [t for t in items if isinstance(t, str)][: max(int(limit), 1)]

    async def site_stats(self) -> dict[str, Any]:
        """Portal totals.

        ``package_search`` with ``rows=0`` gives the dataset count without
        transferring any dataset bodies, which matters on a 1,900-dataset
        catalog.
        """
        search = await self.package_search(rows=1)
        groups = await self.action("group_list")
        orgs = await self.action("organization_list")
        tags = await self.action("tag_list")
        return {
            "portal": self.portal.host,
            "portal_url": self.portal.base_url,
            "portal_name": self.portal.name,
            "platform": "ckan",
            "ckan_version": self.portal.ckan_version,
            "country": "CO",
            "country_name": "Colombia",
            "city": self.portal.city,
            "total_datasets": search.get("count"),
            "total_groups": len(groups) if isinstance(groups, list) else None,
            "total_organizations": len(orgs) if isinstance(orgs, list) else None,
            "total_tags": len(tags) if isinstance(tags, list) else None,
            "datastore_sql_available": False,
            "datastore_note": (
                "datastore_search_sql is not enabled on this portal, so there is no "
                "server-side SQL or GROUP BY. Use bogota_filter_resource for typed "
                "filtering. Measured over 300 random datasets, about 27% carry a "
                "resource flagged DataStore-backed and about 13% actually return "
                "rows — the catalogue's flag is unreliable, so treat a 404 as the "
                "portal's metadata being wrong rather than as your mistake."
            ),
        }

    # ─── DataStore ───────────────────────────────────────────────────────────

    async def datastore_search(
        self,
        resource_id: str,
        *,
        fields: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        q: str | None = None,
        sort: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Typed query against one DataStore-backed resource.

        ``filters`` is sent as a JSON object and matched exactly by CKAN — it
        is not string-interpolated into a query, so equality filtering here
        carries none of the injection surface that raw SQL would.
        """
        if not is_valid_uuid(resource_id):
            raise CkanError(f"Not a valid resource UUID: {resource_id!r}")

        params: dict[str, Any] = {
            "resource_id": resource_id,
            "limit": min(max(int(limit), 1), 1000),
            "offset": max(int(offset), 0),
        }
        if fields:
            bad = [c for c in fields if not is_valid_column(c)]
            if bad:
                raise CkanError(
                    f"Rejected column name(s): {bad!r}. Omit `columns` to receive "
                    "every field, including names this filter cannot express."
                )
            params["fields"] = ",".join(fields)
        if filters:
            bad = [c for c in filters if not is_valid_column(c)]
            if bad:
                raise CkanError(f"Rejected filter column(s): {bad!r}")
            import json as _json

            params["filters"] = _json.dumps(filters, ensure_ascii=False)
        if q:
            params["q"] = q
        if sort:
            # CKAN's sort syntax is "column asc, other desc". Each column is
            # checked; the direction is restricted to the two legal words.
            parts = []
            for chunk in sort.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                bits = chunk.rsplit(" ", 1)
                col = bits[0].strip()
                direction = bits[1].lower() if len(bits) == 2 else "asc"
                if direction not in ("asc", "desc"):
                    raise CkanError(f"Sort direction must be asc or desc, got {direction!r}")
                if not is_valid_column(col):
                    raise CkanError(f"Rejected sort column: {col!r}")
                parts.append(f"{col} {direction}")
            if parts:
                params["sort"] = ", ".join(parts)

        try:
            result = await self.action("datastore_search", params)
        except CkanError as e:
            # Measured on 47 resources the catalogue flagged datastore_active:
            # 20 of them answered 404 because the table does not exist. The
            # portal's own metadata is wrong, and the raw "Not Found Error" is
            # unreadable to a model that was told the resource was queryable.
            if "404" in str(e):
                raise CkanError(
                    f"The catalogue flags resource {resource_id} as DataStore-backed, "
                    "but the portal has no table for it (HTTP 404). This inconsistency "
                    "affects a substantial share of the catalogue; the resource's "
                    "download URL is the only way to reach this data."
                ) from e
            raise
        if not isinstance(result, dict):
            raise CkanError("datastore_search returned an unexpected body")
        return result


# ─── Formatters (CKAN bodies → compact dicts for LLMs) ────────────────────────


def format_resource(r: dict) -> dict:
    """One CKAN resource, trimmed.

    ``datastore_active`` is surfaced deliberately: it is the single field that
    tells the model whether ``bogota_filter_resource`` will work on this
    resource or whether the only thing available is the download URL.
    """
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "format": (r.get("format") or "").upper() or None,
        "url": r.get("url"),
        "size_bytes": r.get("size"),
        "last_modified": r.get("last_modified") or r.get("created"),
        "datastore_active": bool(r.get("datastore_active")),
        "queryable": bool(r.get("datastore_active")),
    }


def format_dataset(d: dict, portal: CkanPortal = BOGOTA) -> dict:
    """One CKAN dataset, trimmed to what a model needs to decide a next call."""
    resources = [format_resource(r) for r in (d.get("resources") or [])]
    return {
        "country": "CO",
        "portal": portal.host,
        "city": portal.city,
        "id": d.get("id"),
        "name": d.get("name"),
        "title": d.get("title"),
        "notes": _truncate(d.get("notes"), NOTES_TRUNC),
        "organization": (d.get("organization") or {}).get("title")
        or (d.get("organization") or {}).get("name"),
        "groups": [g.get("title") or g.get("name") for g in (d.get("groups") or [])],
        "tags": [t.get("name") for t in (d.get("tags") or []) if isinstance(t, dict)],
        "license": d.get("license_title") or d.get("license_id"),
        "created": d.get("metadata_created"),
        "modified": d.get("metadata_modified"),
        "url": portal.dataset_url(d["name"]) if d.get("name") else None,
        "num_resources": len(resources),
        "queryable_resources": sum(1 for r in resources if r["datastore_active"]),
        "resources": resources,
    }


def format_search_response(body: dict, portal: CkanPortal = BOGOTA) -> dict:
    results = body.get("results") or []
    return {
        "country": "CO",
        "portal": portal.host,
        "city": portal.city,
        "total": body.get("count"),
        "returned": len(results),
        "datasets": [format_dataset(d, portal) for d in results],
    }


def format_organization(o: dict, portal: CkanPortal = BOGOTA) -> dict:
    return {
        "name": o.get("name"),
        "title": o.get("title") or o.get("display_name"),
        "dataset_count": o.get("package_count"),
        "url": portal.organization_url(o["name"]) if o.get("name") else None,
    }


def format_group(g: dict, portal: CkanPortal = BOGOTA) -> dict:
    return {
        "name": g.get("name"),
        "title": g.get("title") or g.get("display_name"),
        "dataset_count": g.get("package_count"),
        "url": portal.group_url(g["name"]) if g.get("name") else None,
    }


def format_datastore_result(body: dict, resource_id: str, portal: CkanPortal = BOGOTA) -> dict:
    records = body.get("records") or []
    return {
        "country": "CO",
        "portal": portal.host,
        "resource_id": resource_id,
        "total_rows_matching": body.get("total"),
        "rows_returned": len(records),
        "fields": [
            {"name": f.get("id"), "type": f.get("type")}
            for f in (body.get("fields") or [])
            if isinstance(f, dict)
        ],
        "rows": records,
    }
