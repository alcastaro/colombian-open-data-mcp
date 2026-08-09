"""Socrata client + formatters for datos.gov.co.

The Socrata Open Data Platform (SODA) exposes three relevant endpoints:

  1. Catalog discovery on api.us.socrata.com (cross-portal search):
       https://api.us.socrata.com/api/catalog/v1?domains=www.datos.gov.co
     Returns lists of datasets that live on the queried domain.

  2. Per-domain catalog + view metadata on the portal itself:
       https://www.datos.gov.co/api/catalog/v1
       https://www.datos.gov.co/api/views/<4x4-id>.json
       https://www.datos.gov.co/api/views/metadata/v1
       https://www.datos.gov.co/api/catalog/v1/domain_categories
       https://www.datos.gov.co/api/catalog/v1/domain_tags

  3. Per-resource data with SoQL:
       https://www.datos.gov.co/resource/<4x4-id>.json?$select=...&$where=...

This module wraps all three with the same httpx.AsyncClient instance for
connection reuse. Optional Socrata App Token via env var SOCRATA_APP_TOKEN
removes the unauthenticated rate limit.
"""

from __future__ import annotations

import logging
import os
import re
import ssl
from typing import Any

import httpx

from . import USER_AGENT
from .retry import get_with_retries

PORTAL_HOST = "www.datos.gov.co"
PORTAL_URL = f"https://{PORTAL_HOST}"
CATALOG_API_BASE = "https://api.us.socrata.com/api/catalog/v1"
DOMAIN_API_BASE = f"https://{PORTAL_HOST}/api"
RESOURCE_API_BASE = f"https://{PORTAL_HOST}/resource"

DEFAULT_TIMEOUT = 20.0

#: Operator override for the request timeout, in seconds.
TIMEOUT_ENV = "CO_MCP_TIMEOUT"

#: Upper bound on the override. A request that has not answered in five minutes
#: is not going to, and an unbounded value would let one call hang a stdio
#: session indefinitely with the client unable to tell why.
MAX_TIMEOUT = 300.0


def request_timeout() -> float:
    """Seconds to wait for a portal response.

    Twenty seconds is right for the catalogue and for most datasets, and wrong
    for the few that are genuinely large: a ``median()`` over SECOP II's six
    million contracts is a legitimate question that takes the portal longer
    than that to answer, and the default turns it into a permanent failure.

    Read on every call rather than cached, so a session can be retuned without
    a restart. An unparseable or out-of-range value falls back to the default
    rather than raising — a bad environment variable should not stop the server
    from starting.
    """
    raw = os.environ.get(TIMEOUT_ENV)
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    if value <= 0 or value > MAX_TIMEOUT:
        return DEFAULT_TIMEOUT
    return value


# Output trimming so single calls never blow up the LLM context.
DESC_TRUNC = 300
NOTES_TRUNC = 300

# Socrata asset types on this domain, with the counts measured on 2026-08-29.
# `dataset` is the obvious one; `filter` is the surprising one — a saved view
# with its own 4x4 that answers the data API exactly like a dataset does.
ASSET_TYPES = {
    "dataset",  # 8,391
    "filter",  # 2,197 — saved views, queryable
    "chart",  # 681
    "href",  # 473 — external links, not queryable
    "story",  # 365
    "map",  # 133
    "file",  # 9
    "calendar",  # 2
}

# Asset types whose 4x4 answers /resource/<id>.json with rows. The others carry
# metadata worth finding but have no table behind them, and saying so up front
# beats letting a model discover it through an error.
QUERYABLE_ASSET_TYPES = {"dataset", "filter", "chart", "map"}

# Socrata 4x4 IDs look like "abcd-1234" — always 4 chars + dash + 4 chars.
_FOURBYFOUR = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")

logger = logging.getLogger(__name__)


# Some LatAm portals (notably datos.gob.mx) ship incomplete TLS chains. The
# Colombian portal has a clean chain in our testing, but we still inject the
# system trust store for resilience — `curl` works because it uses it.
try:
    import truststore as _truststore  # type: ignore[import-not-found]

    _SSL_CTX: ssl.SSLContext | None = _truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except Exception:
    _SSL_CTX = None


def is_valid_4x4(value: str) -> bool:
    """Return True if value is a Socrata 4x4 resource id."""
    return bool(_FOURBYFOUR.match(value or ""))


def _truncate(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


class SocrataError(RuntimeError):
    """Raised when the Socrata API returns a non-2xx or an unexpected body."""


class SocrataClient:
    """Async Socrata client bound to a single domain (www.datos.gov.co).

    Holds one httpx.AsyncClient for connection reuse. App token (optional) is
    read from the SOCRATA_APP_TOKEN env var on construction so tests can
    monkeypatch the env without touching the singleton.
    """

    def __init__(self, app_token: str | None = None) -> None:
        self._client: httpx.AsyncClient | None = None
        self._app_token = app_token or os.environ.get("SOCRATA_APP_TOKEN")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            if self._app_token:
                headers["X-App-Token"] = self._app_token
            kwargs: dict[str, Any] = dict(
                headers=headers,
                timeout=request_timeout(),
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

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        client = await self._get_client()
        try:
            r = await get_with_retries(client, url, self._clean(params or {}))
        except httpx.TimeoutException as e:
            raise SocrataError(
                f"timeout calling {url} (>{request_timeout()}s). "
                f"Set {TIMEOUT_ENV} to allow longer for very large datasets."
            ) from e
        except httpx.HTTPError as e:
            raise SocrataError(f"network error calling {url}: {e}") from e

        if r.status_code >= 400:
            body_excerpt = r.text[:300] if r.text else "(no body)"
            raise SocrataError(f"HTTP {r.status_code} from {url}: {body_excerpt}")
        try:
            return r.json()
        except ValueError as e:
            raise SocrataError(f"non-JSON response from {url}: {e}") from e

    # ─── Catalog (cross-dataset discovery) ────────────────────────────────

    async def catalog_search(
        self,
        *,
        query: str | None = None,
        categories: str | None = None,
        tags: str | None = None,
        asset_type: str = "dataset",
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the datos.gov.co catalog.

        Hits api.us.socrata.com, which understands domain-scoped queries.
        Returns the raw catalog body (use format_catalog_response to summarise).

        ``asset_type`` used to be hardcoded to ``dataset``, which hid 3,860
        assets. Saved views (``filter``) in particular are fully queryable
        through the data API under their own 4x4 id — the historical
        Representative Market Exchange Rate is one of them — so excluding them
        cost real coverage for no reason. Pass ``any`` to search everything.
        """
        params: dict[str, Any] = {
            "domains": PORTAL_HOST,
            "search_context": PORTAL_HOST,
            "limit": min(max(int(limit), 1), 100),
            "offset": max(int(offset), 0),
        }
        if asset_type and asset_type != "any":
            if asset_type not in ASSET_TYPES:
                raise SocrataError(
                    f"asset_type must be one of {sorted(ASSET_TYPES)} or 'any', got {asset_type!r}"
                )
            params["only"] = asset_type
        if query:
            params["q"] = query
        if categories:
            params["categories"] = categories
        if tags:
            params["tags"] = tags
        return await self._get_json(CATALOG_API_BASE, params)

    async def catalog_recent(self, limit: int = 10) -> dict[str, Any]:
        """List most-recently-updated datasets on the portal."""
        return await self._get_json(
            CATALOG_API_BASE,
            {
                "domains": PORTAL_HOST,
                "search_context": PORTAL_HOST,
                "only": "dataset",
                "limit": min(max(int(limit), 1), 50),
                "order": "updatedAt",
            },
        )

    async def domain_categories(self) -> list[str]:
        """Top-level categories on the portal (Socrata's group equivalent)."""
        data = await self._get_json(f"{DOMAIN_API_BASE}/catalog/v1/domain_categories")
        results = data.get("results", []) if isinstance(data, dict) else []
        return [r.get("domain_category", r) if isinstance(r, dict) else r for r in results]

    async def domain_tags(self) -> list[str]:
        """All tags on the portal."""
        data = await self._get_json(f"{DOMAIN_API_BASE}/catalog/v1/domain_tags")
        results = data.get("results", []) if isinstance(data, dict) else []
        return [r.get("domain_tag", r) if isinstance(r, dict) else r for r in results]

    async def domain_owners(self, limit: int = 50) -> list[dict[str, Any]]:
        """Dataset owners (Socrata's 'organization' analogue)."""
        data = await self._get_json(
            f"{DOMAIN_API_BASE}/catalog/v1/owners",
            {"limit": min(max(int(limit), 1), 200)},
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        return [
            {
                "owner": r.get("owner") if isinstance(r, dict) else r,
                "count": r.get("count") if isinstance(r, dict) else None,
            }
            for r in results
        ]

    async def autocomplete(self, kind: str, query: str, limit: int = 10) -> list[str]:
        """Free-text autocomplete against the catalog.

        Socrata's catalog API exposes /autocomplete which returns matched
        dataset titles. For tags/categories/owners we use the dedicated
        domain endpoints and prefix-filter client-side.
        """
        if kind == "dataset":
            data = await self._get_json(
                f"{CATALOG_API_BASE}/autocomplete",
                {
                    "domains": PORTAL_HOST,
                    "search_context": PORTAL_HOST,
                    "q": query,
                },
            )
            opts = data.get("results", []) if isinstance(data, dict) else []
            titles = [o.get("title") if isinstance(o, dict) else o for o in opts]
            return [str(t) for t in titles if t][: max(int(limit), 1)]
        if kind == "tag":
            tags = await self.domain_tags()
            q = (query or "").lower()
            return [t for t in tags if t and q in t.lower()][: max(int(limit), 1)]
        if kind == "category":
            cats = await self.domain_categories()
            q = (query or "").lower()
            return [c for c in cats if c and q in c.lower()][: max(int(limit), 1)]
        if kind == "owner":
            owners = await self.domain_owners(limit=200)
            q = (query or "").lower()
            return [
                str(o["owner"]) for o in owners if o.get("owner") and q in str(o["owner"]).lower()
            ][: max(int(limit), 1)]
        raise ValueError("kind must be one of: dataset, tag, category, owner")

    # ─── View / dataset metadata ──────────────────────────────────────────

    async def get_view(self, four_by_four: str) -> dict[str, Any]:
        """Full dataset metadata via the views API."""
        if not is_valid_4x4(four_by_four):
            raise SocrataError(f"Not a valid 4x4 id: {four_by_four!r}")
        return await self._get_json(f"{DOMAIN_API_BASE}/views/{four_by_four}.json")

    async def get_view_metadata(self, four_by_four: str) -> dict[str, Any]:
        """Lighter metadata via the metadata v1 endpoint."""
        if not is_valid_4x4(four_by_four):
            raise SocrataError(f"Not a valid 4x4 id: {four_by_four!r}")
        return await self._get_json(f"{DOMAIN_API_BASE}/views/metadata/v1/{four_by_four}")

    # ─── Per-resource data with SoQL ──────────────────────────────────────

    async def resource_query(
        self,
        four_by_four: str,
        soql_params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a SoQL query against a dataset, return rows as list of dicts."""
        if not is_valid_4x4(four_by_four):
            raise SocrataError(f"Not a valid 4x4 id: {four_by_four!r}")
        url = f"{RESOURCE_API_BASE}/{four_by_four}.json"
        result = await self._get_json(url, soql_params or {})
        if not isinstance(result, list):
            raise SocrataError(f"Expected list from {url}, got {type(result).__name__}")
        return result

    async def site_stats(self) -> dict[str, Any]:
        """Top-level portal stats.

        This used to query the catalogue with no ``only`` filter and report the
        answer as "total datasets". Two things were wrong with that. The count
        spans every asset type, not datasets; and it saturates — an unfiltered
        query returns exactly 10,000 while the per-type counts sum to 12,251, so
        10,000 is a ceiling, not a total.

        Counting ``only=dataset`` gives the real figure and matches what
        ``search_datasets`` can actually reach.
        """
        cat = await self._get_json(
            CATALOG_API_BASE,
            {
                "domains": PORTAL_HOST,
                "search_context": PORTAL_HOST,
                "only": "dataset",
                "limit": 0,
            },
        )
        cats = await self.domain_categories()
        tags = await self.domain_tags()
        return {
            "portal": "datos.gov.co",
            "portal_url": PORTAL_URL,
            "platform": "socrata",
            "country": "CO",
            "country_name": "Colombia",
            "total_datasets": cat.get("resultSetSize"),
            "total_categories": len(cats) if isinstance(cats, list) else None,
            "total_tags": len(tags) if isinstance(tags, list) else None,
            "asset_types_searchable": sorted(ASSET_TYPES),
            "asset_types_queryable": sorted(QUERYABLE_ASSET_TYPES),
            "note": (
                "total_datasets counts only assets of type 'dataset'. The portal "
                "also publishes saved views ('filter'), charts and maps that are "
                "queryable through the same data API — pass asset_type to "
                "search_datasets to reach them."
            ),
        }


# ─── Formatters (catalog body -> compact dicts for LLMs) ──────────────────────


def _permalink(four_by_four: str | None) -> str | None:
    if not four_by_four:
        return None
    return f"{PORTAL_URL}/d/{four_by_four}"


def format_catalog_dataset(item: dict) -> dict:
    """Format one Socrata catalog hit into a compact LLM-friendly dict."""
    resource = item.get("resource") or {}
    classification = item.get("classification") or {}
    metadata = item.get("metadata") or {}
    owner = item.get("owner") or {}

    columns_field_name = resource.get("columns_field_name") or []
    columns_datatype = resource.get("columns_datatype") or []
    columns_description = resource.get("columns_description") or []

    return {
        "id": resource.get("id"),
        "name": resource.get("name"),
        "description": _truncate(resource.get("description"), NOTES_TRUNC),
        "type": resource.get("type"),
        "queryable": resource.get("type") in QUERYABLE_ASSET_TYPES,
        "row_count": resource.get("count_estimate") or resource.get("rows_count"),
        "columns": [
            {
                "field_name": fn,
                "type": dt,
                "description": _truncate(desc, 80) if desc else None,
            }
            for fn, dt, desc in zip(
                columns_field_name,
                columns_datatype,
                columns_description + [None] * len(columns_field_name),
            )
        ],
        "category": classification.get("domain_category"),
        "tags": classification.get("domain_tags") or classification.get("tags") or [],
        "owner": owner.get("display_name"),
        "created_at": resource.get("createdAt"),
        "updated_at": resource.get("updatedAt"),
        "metadata_updated_at": resource.get("metadata_updated_at"),
        "license": metadata.get("license"),
        "url": _permalink(resource.get("id")) or item.get("permalink"),
        "data_api_endpoint": f"{RESOURCE_API_BASE}/{resource.get('id')}.json"
        if resource.get("id")
        else None,
    }


def format_catalog_response(data: dict) -> dict:
    """Format the catalog v1 body into the shape we expose via MCP."""
    results = data.get("results") or []
    return {
        "country": "CO",
        "portal": "datos.gov.co",
        "total": data.get("resultSetSize"),
        "returned": len(results),
        "datasets": [format_catalog_dataset(item) for item in results],
    }


def _row_count(view: dict) -> int | None:
    """How many rows the dataset holds, or None when the portal won't say.

    Socrata's view metadata has no top-level row count — ``rowsCount`` is
    simply absent on datos.gov.co — but every profiled column carries a
    ``cachedContents.count`` that matches ``count(*)`` exactly. Taking the
    largest across columns tolerates the columns that carry no profile at all.

    This matters more than it looks: without it a model has no way to tell a
    460-row table from a six-million-row one, and that is exactly the choice
    between previewing a dataset and aggregating it server-side.
    """
    counts: list[int] = []
    for c in view.get("columns") or []:
        raw = (c.get("cachedContents") or {}).get("count")
        if raw is None:
            continue
        try:
            counts.append(int(raw))
        except (TypeError, ValueError):
            continue
    if counts:
        return max(counts)
    top = view.get("rowsCount")
    if top is None:
        return None
    try:
        return int(top)
    except (TypeError, ValueError):
        return None


def format_view(view: dict) -> dict:
    """Format the full view metadata into a compact dict."""
    columns = view.get("columns") or []
    return {
        "country": "CO",
        "id": view.get("id"),
        "name": view.get("name"),
        "description": _truncate(view.get("description"), NOTES_TRUNC),
        "category": view.get("category"),
        "tags": view.get("tags") or [],
        "owner": (view.get("owner") or {}).get("displayName"),
        "created_at": view.get("createdAt"),
        "updated_at": view.get("rowsUpdatedAt") or view.get("updatedAt"),
        "row_count": _row_count(view),
        "license": (view.get("license") or {}).get("name"),
        "license_url": (view.get("license") or {}).get("termsLink"),
        "attribution": view.get("attribution"),
        "url": _permalink(view.get("id")),
        "data_api_endpoint": f"{RESOURCE_API_BASE}/{view.get('id')}.json"
        if view.get("id")
        else None,
        "columns": [
            {
                "field_name": c.get("fieldName"),
                "name": c.get("name"),
                "type": c.get("dataTypeName"),
                "description": _truncate(c.get("description"), 120),
                "position": c.get("position"),
            }
            for c in columns
        ],
    }
