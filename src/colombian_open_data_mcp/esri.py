"""Client for the ArcGIS REST services Colombian portals publish as resources.

**Why this exists.** Bogotá's CKAN DataStore returns rows for only about 27% of
its catalogue. That is not because the other 73% is unavailable — a large slice
of it is published as ArcGIS REST layers, which are *services*, not files. A
measured 334 of Bogotá's 1,917 datasets carry at least one ``ESRI REST``
resource, and every one of fifteen sampled answered ``?f=json`` on the first
try. Reaching them needs no download, no cache and no parser: an ArcGIS layer
takes a ``where`` clause, projects columns, paginates, and — the part no CKAN
city portal can do — **computes GROUP BY aggregations on the server**.

So this module is the one place in the whole server where a territorial rollup
runs remotely instead of being summed over rows in the model's context.

Three things about ArcGIS that shape everything below:

1. **Errors arrive as HTTP 200.** A malformed ``where`` clause returns status
   200 with ``{"error": {"code": 400, ...}}`` in the body. Any client that
   trusts the status code will report success and hand the model a dict with no
   rows in it. :func:`_unwrap` checks the body, always.
2. **The catalogue's ``format`` field lies.** Among the first Bogotá resources
   labelled ``ESRI REST`` is a URL ending ``/download/shape.zip`` — a zipped
   shapefile, not a service. :func:`is_service_url` refuses those with an
   explanation rather than firing a query at a zip file.
3. **A service root is not a layer.** ``.../MapServer`` describes a service and
   lists its layers; only ``.../MapServer/4`` can be queried. Both forms appear
   in the catalogue, so :func:`service_info` reports the layers of a root and
   the tools tell the caller to pick one.

Security. The URL never comes from the model — the tools take a resource UUID,
look the address up in the portal's own catalogue, and pass it here. On top of
that, this client installs :func:`netguard.guard_request_hook`, so the initial
request and every redirect hop are checked against the SSRF policy. And the
``where`` clause, which is SQL bound for someone else's database, goes through
:func:`validate_where` with the same denylist discipline the SoQL side uses.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import USER_AGENT
from .netguard import NetGuardError, guard_request_hook
from .retry import get_with_retries

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 40.0

#: ArcGIS caps a single page itself (``maxRecordCount``, typically 1000-2000).
#: This is the ceiling this server will ask for regardless.
MAX_RECORDS = 1000

#: A layer endpoint: ``…/rest/services/<folder>/<service>/(Map|Feature)Server/<n>``.
_LAYER_URL = re.compile(
    r"^https?://[^/]+/.*?/rest/services/.+/(?:MapServer|FeatureServer)/(\d+)/?$",
    re.IGNORECASE,
)
#: A service root, which lists layers but cannot itself be queried.
_SERVICE_URL = re.compile(
    r"^https?://[^/]+/.*?/rest/services/.+/(?:MapServer|FeatureServer)/?$",
    re.IGNORECASE,
)

#: Aggregation functions ArcGIS accepts in ``outStatistics``.
STATISTIC_TYPES = ("count", "sum", "min", "max", "avg", "stddev", "var")

#: Field names. Same reasoning as the CKAN column rule: the security boundary is
#: not an enumeration of acceptable characters — these go into query-string
#: parameters that httpx percent-encodes — but a short closed set of sequences
#: that would break out of an identifier and into the statement around it.
_FIELD_MAX = 128
_FORBIDDEN_SUBSTR = ("--", "/*", "*/", ";", "'", '"')

#: Statement keywords refused inside a ``where`` clause. A ``where`` is SQL
#: reaching a database this server does not own; ArcGIS layers are published
#: read-only, but relying on the publisher having configured that correctly is
#: not a security posture. The same denylist the raw-SoQL validator uses, plus
#: the ArcGIS-specific escapes.
_WHERE_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|"
    r"exec|execute|union|merge|declare|xp_\w+|sp_\w+|waitfor|shutdown)\b",
    re.IGNORECASE,
)
_WHERE_MAX = 2000


class EsriError(RuntimeError):
    """Raised when an ArcGIS request fails, or returns an error body."""


def is_service_url(url: str) -> bool:
    """Whether ``url`` is an ArcGIS REST layer or service root.

    Deliberately strict. A resource the catalogue labels ``ESRI REST`` may be
    anything at all — a zipped shapefile was the first counterexample found —
    and querying a zip file produces an error a model cannot act on. Refusing
    here produces one it can: *this resource is not a service, read the file
    instead.*
    """
    url = (url or "").strip()
    return bool(_LAYER_URL.match(url) or _SERVICE_URL.match(url))


def is_layer_url(url: str) -> bool:
    """Whether ``url`` names a specific layer, as opposed to a service root."""
    return bool(_LAYER_URL.match((url or "").strip()))


def layer_url(url: str, layer: int | None = None) -> str:
    """The queryable layer address for ``url``.

    Raises when a service root is given without a layer index, because there is
    no defensible default: picking layer 0 would silently answer a question
    about a different dataset than the one asked about.
    """
    url = (url or "").strip().rstrip("/")
    if not is_service_url(url):
        raise EsriError(
            f"Not an ArcGIS REST service address: {url!r}. The catalogue labels some "
            "resources 'ESRI REST' that are ordinary files (a zipped shapefile, for "
            "one). Use city_read_resource_file for those."
        )
    if is_layer_url(url):
        if layer is not None and str(layer) != url.rsplit("/", 1)[1]:
            raise EsriError(
                f"The resource URL already names layer {url.rsplit('/', 1)[1]}; "
                f"passing layer={layer} would contradict it. Omit the layer argument."
            )
        return url
    if layer is None:
        raise EsriError(
            "This resource is a service root, not a single layer. Call "
            "city_esri_service_info first to list its layers, then pass the chosen "
            "layer number."
        )
    if not isinstance(layer, int) or layer < 0 or layer > 9999:
        raise EsriError(f"Layer index must be a small non-negative integer, got {layer!r}")
    return f"{url}/{layer}"


def query_url(url: str) -> str:
    """The ``/query`` endpoint of a layer.

    This exists because forgetting it produced the most instructive bug in the
    module. A layer root answers ``GET`` with its own description and *ignores
    every query parameter*: HTTP 200, a well-formed JSON body, a ``fields`` list
    that looks exactly like the one a query would return — and no ``features``.
    The tool reported zero rows for a layer holding eleven, and nothing in the
    response said anything was wrong. It is the same failure mode as ArcGIS
    returning errors under HTTP 200, one level up, and the reason a test asserts
    the requested path ends in ``/query``.
    """
    url = (url or "").rstrip("/")
    return url if url.lower().endswith("/query") else f"{url}/query"


def validate_field(name: str) -> str:
    """Return ``name`` if it is usable as an ArcGIS field, else raise."""
    name = (name or "").strip()
    if not name:
        raise EsriError("Empty field name")
    if len(name) > _FIELD_MAX:
        raise EsriError(f"Field name too long ({len(name)} chars): {name[:40]!r}…")
    if any(bad in name for bad in _FORBIDDEN_SUBSTR):
        raise EsriError(
            f"Rejected field name {name!r}: it contains a quote, a comment marker or "
            "a statement separator."
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise EsriError(f"Rejected field name {name!r}: it contains a control character.")
    return name


def validate_where(where: str) -> str:
    """Return a ``where`` clause safe to send, else raise.

    ArcGIS evaluates this as SQL. The rules are the same three the raw-SoQL
    validator applies: no statement separators, no comment markers that could
    hide the rest of the clause, and no keyword that would write, execute or
    graft a second query onto the first.

    What is *allowed* is the whole comparison language a caller actually needs —
    ``=``, ``<>``, ``<``, ``>``, ``LIKE``, ``IN``, ``BETWEEN``, ``AND``, ``OR``,
    ``IS NULL`` and quoted literals — because refusing those would leave the
    tool unable to filter, which is the only reason it exists.
    """
    s = (where or "").strip()
    if not s:
        return "1=1"
    if len(s) > _WHERE_MAX:
        raise EsriError(f"WHERE clause too long ({len(s)} chars, limit {_WHERE_MAX}).")
    for bad in ("--", "/*", "*/", ";"):
        if bad in s:
            raise EsriError(
                f"Rejected WHERE clause: it contains {bad!r}, a comment marker or "
                "statement separator. Write a single plain condition."
            )
    if _WHERE_FORBIDDEN.search(s):
        found = _WHERE_FORBIDDEN.search(s)
        raise EsriError(
            f"Rejected WHERE clause: the keyword {found.group(0)!r} is not allowed. "  # type: ignore[union-attr]
            "This tool only reads; filtering keywords such as AND, OR, LIKE, IN and "
            "BETWEEN are accepted."
        )
    if s.count("'") % 2:
        raise EsriError(
            "Rejected WHERE clause: an odd number of single quotes, so a string "
            "literal is left open. Double a literal apostrophe: 'BOGOTA D''C'."
        )
    return s


def build_statistics(aggregations: list[dict]) -> str:
    """The ``outStatistics`` JSON ArcGIS expects, from a list of specs.

    Each spec is ``{"fn": ..., "col": ..., "alias": ...}``. ``count`` may omit
    ``col``, in which case the layer's own object-id field is counted — but the
    caller does not know that field's name, so the tools substitute it.
    """
    if not aggregations:
        raise EsriError("At least one aggregation is required.")
    if len(aggregations) > 10:
        raise EsriError("At most 10 aggregations per call.")
    out = []
    for i, agg in enumerate(aggregations):
        fn = str(agg.get("fn") or "count").strip().lower()
        if fn not in STATISTIC_TYPES:
            raise EsriError(f"Unknown aggregation {fn!r}; use one of {', '.join(STATISTIC_TYPES)}.")
        col = agg.get("col")
        if not col:
            raise EsriError(f"Aggregation {i} needs a `col`; ArcGIS has no count(*) form.")
        alias = str(agg.get("alias") or f"{fn}_{i}").strip()
        out.append(
            {
                "statisticType": fn,
                "onStatisticField": validate_field(str(col)),
                "outStatisticFieldName": validate_field(alias),
            }
        )
    return json.dumps(out)


def _unwrap(body: Any, url: str) -> dict:
    """The response body, or a raised error.

    ArcGIS answers HTTP 200 for query errors and puts the real status inside
    ``error``. Checking only the HTTP code would report success on a body with
    no data in it, which is the single most likely way this module could lie.
    """
    if not isinstance(body, dict):
        raise EsriError(f"Unexpected non-object response from {url}")
    if "error" in body:
        e = body["error"] or {}
        code = e.get("code", "?")
        message = e.get("message") or "(no message)"
        details = "; ".join(str(d) for d in (e.get("details") or []))
        raise EsriError(f"ArcGIS error {code}: {message}{(' — ' + details) if details else ''}")
    return body


try:
    import truststore as _truststore  # type: ignore[import-not-found]

    _SSL_CTX: ssl.SSLContext | None = _truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except Exception:
    _SSL_CTX = None


class EsriClient:
    """Async client for ArcGIS REST layers on hosts named by a portal catalogue.

    One client serves every host, because the addresses are not known in
    advance: Bogotá's layers sit on ``serviciosgis.catastrobogota.gov.co``,
    ``portalgis.habitatbogota.gov.co`` and half a dozen others, none of which is
    under ``bogota.gov.co``. That is exactly why the SSRF hook is installed on
    the client rather than checked once at a call site.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            kwargs: dict[str, Any] = dict(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                event_hooks={"request": [guard_request_hook]},
            )
            if _SSL_CTX is not None:
                kwargs["verify"] = _SSL_CTX
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _get(self, url: str, params: dict[str, Any]) -> dict:
        client = await self._get_client()
        params = {**params, "f": "json"}
        try:
            r = await get_with_retries(client, url, params)
        except NetGuardError:
            raise
        except httpx.TimeoutException as e:
            raise EsriError(f"timeout querying {url} (>{DEFAULT_TIMEOUT}s)") from e
        except httpx.HTTPError as e:
            raise EsriError(f"network error querying {url}: {e}") from e

        if r.status_code >= 400:
            raise EsriError(f"HTTP {r.status_code} from {url}: {(r.text or '')[:200]}")
        try:
            body = r.json()
        except ValueError as e:
            # An ArcGIS host answering HTML is almost always a portal login page
            # or a WAF, and saying so is more useful than a JSON parse error.
            raise EsriError(
                f"Non-JSON response from {url} — the service may require "
                f"authentication or be behind a gateway: {e}"
            ) from e
        return _unwrap(body, url)

    async def service_info(self, url: str) -> dict:
        """Metadata for a layer or a service root: fields, capabilities, layers."""
        if not is_service_url(url):
            raise EsriError(
                f"Not an ArcGIS REST service address: {url!r}. "
                "Use city_read_resource_file if this resource is a plain file."
            )
        return await self._get(url.rstrip("/"), {})

    async def count(self, url: str, where: str | None = None) -> int:
        body = await self._get(
            query_url(url), {"where": validate_where(where or ""), "returnCountOnly": "true"}
        )
        try:
            return int(body.get("count", 0))
        except (TypeError, ValueError) as e:
            raise EsriError(f"Unreadable count from {url}") from e

    async def query(
        self,
        url: str,
        *,
        where: str | None = None,
        out_fields: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        order_by: str | None = None,
    ) -> dict:
        """Rows from one layer. Geometry is never requested.

        Dropping geometry is not a limitation, it is the point: a polygon layer
        answering with its coordinates would fill the model's context with
        thousands of vertices nobody asked about. The attributes are the data.
        """
        fields = "*"
        if out_fields:
            fields = ",".join(validate_field(f) for f in out_fields)
        params: dict[str, Any] = {
            "where": validate_where(where or ""),
            "outFields": fields,
            "returnGeometry": "false",
            "resultRecordCount": min(max(int(limit), 1), MAX_RECORDS),
            "resultOffset": max(int(offset), 0),
        }
        if order_by:
            params["orderByFields"] = ", ".join(
                validate_field(part.strip().rsplit(" ", 1)[0])
                + (
                    " DESC"
                    if part.strip().lower().endswith(" desc")
                    else " ASC"
                    if part.strip().lower().endswith(" asc")
                    else ""
                )
                for part in order_by.split(",")
                if part.strip()
            )
        return await self._get(query_url(url), params)

    async def aggregate(
        self,
        url: str,
        *,
        aggregations: list[dict],
        group_by: list[str] | None = None,
        where: str | None = None,
    ) -> dict:
        """Server-side GROUP BY. The one territorial rollup that never ships rows."""
        params: dict[str, Any] = {
            "where": validate_where(where or ""),
            "outStatistics": build_statistics(aggregations),
            "returnGeometry": "false",
        }
        if group_by:
            params["groupByFieldsForStatistics"] = ",".join(validate_field(g) for g in group_by)
        return await self._get(query_url(url), params)


# ─── Formatters (ArcGIS bodies → compact dicts for LLMs) ──────────────────────


def format_service_info(body: dict, url: str) -> dict:
    """A layer or service description, trimmed to what decides the next call.

    ArcGIS metadata is enormous — ``drawingInfo`` alone carries the full
    cartographic symbology, which no model needs and every model would pay for
    in context. What survives is the identity, the field list, and the two
    capability flags that determine whether the aggregation tool will work.
    """
    fields = [
        {
            "name": f.get("name"),
            "type": str(f.get("type") or "").replace("esriFieldType", ""),
            "alias": f.get("alias") if f.get("alias") != f.get("name") else None,
        }
        for f in (body.get("fields") or [])
        if isinstance(f, dict)
    ]
    layers = [
        {"id": layer.get("id"), "name": layer.get("name")}
        for layer in (body.get("layers") or [])
        if isinstance(layer, dict)
    ]
    advanced = body.get("advancedQueryCapabilities") or {}
    out: dict[str, Any] = {
        "country": "CO",
        "service_url": url,
        "name": body.get("name"),
        "type": body.get("type"),
        "description": (body.get("description") or "").strip()[:300] or None,
        "geometry_type": str(body.get("geometryType") or "").replace("esriGeometry", "") or None,
        "max_records_per_page": body.get("maxRecordCount"),
        "supports_statistics": bool(body.get("supportsStatistics")),
        "supports_pagination": bool(advanced.get("supportsPagination")),
        "fields": fields,
        "n_fields": len(fields),
    }
    if layers:
        out["layers"] = layers
        out["note"] = (
            "This address is a service root, not a queryable layer. Pass one of "
            "the layer ids above as `layer` to query it."
        )
    return out


def format_features(body: dict, url: str) -> dict:
    """Feature attributes as plain rows.

    ``features[].attributes`` is unwrapped into a flat list because the ArcGIS
    envelope adds a level of nesting that carries no information once geometry
    has been dropped.
    """
    features = body.get("features") or []
    rows = [f.get("attributes", {}) for f in features if isinstance(f, dict)]
    return {
        "country": "CO",
        "service_url": url,
        "rows_returned": len(rows),
        "exceeded_transfer_limit": bool(body.get("exceededTransferLimit")),
        "fields": [
            {"name": f.get("name"), "type": str(f.get("type") or "").replace("esriFieldType", "")}
            for f in (body.get("fields") or [])
            if isinstance(f, dict)
        ],
        "rows": rows,
    }


def service_host(url: str) -> str | None:
    return urlsplit(url or "").hostname
