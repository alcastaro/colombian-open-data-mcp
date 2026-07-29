"""Tests for the MCP tool layer itself, not the clients underneath it.

Four things are checked here that unit tests on the clients cannot see:

1. **Every tool is registered with a description and read-only annotations.**
   A host uses the annotations to decide whether to prompt; the model uses the
   description to decide whether to call. An empty description is invisible
   until you inspect ``tools/list`` — which is exactly how the CKAN factory
   shipped eight nameless tools on its first attempt.
2. **No tool raises.** A portal outage has to arrive at the model as a dict
   with a hint, not as a protocol error carrying a traceback.
3. **The three tool families stay separate**, with neither city portal
   offering aggregation — because neither can do it.
4. **The two city families are identical in shape**, since they come from one
   factory. A divergence means the factory grew a portal-specific branch.

Tools are reached through the tool manager rather than as module attributes:
the city tools are closures created by ``ckan_tools.register`` and never bound
to a module-level name, which is also how a real client reaches them.
"""

from __future__ import annotations

import pytest

from colombian_open_data_mcp import ckan, server, socrata

CITIES = ["bogota", "cali"]
CITY_SUFFIXES = [
    "search_datasets",
    "get_dataset",
    "list_organizations",
    "list_groups",
    "list_tags",
    "get_site_stats",
    "resource_preview",
    "filter_resource",
]


def fn(name: str):
    """The callable behind a registered tool name."""
    return server.mcp._tool_manager.get_tool(name).fn


async def _tools():
    return {t.name: t for t in await server.mcp.list_tools()}


# ─── Registration ────────────────────────────────────────────────────────────


async def test_expected_tool_families_are_registered():
    tools = await _tools()
    national = {n for n in tools if not n.startswith(("bogota_", "cali_"))}
    assert len(national) == 12
    for city in CITIES:
        assert len({n for n in tools if n.startswith(f"{city}_")}) == 8
    assert len(tools) == 28


@pytest.mark.parametrize("city", CITIES)
@pytest.mark.parametrize("suffix", CITY_SUFFIXES)
async def test_every_city_tool_exists(city, suffix):
    assert f"{city}_{suffix}" in await _tools()


async def test_every_tool_is_annotated_read_only():
    for name, tool in (await _tools()).items():
        ann = tool.annotations
        assert ann is not None, f"{name} has no annotations"
        assert ann.readOnlyHint is True, f"{name} is not marked read-only"
        assert ann.openWorldHint is True, f"{name} is not marked open-world"
        assert ann.title, f"{name} has no human title"


async def test_every_tool_has_a_description():
    """The factory's first version assigned __doc__ after the decorator ran,
    which registers eight tools with no description at all."""
    missing = [n for n, t in (await _tools()).items() if not (t.description or "").strip()]
    assert missing == []


async def test_city_tool_descriptions_name_their_own_city():
    tools = await _tools()
    assert "Bogotá" in tools["bogota_search_datasets"].description
    assert "Cali" in tools["cali_search_datasets"].description
    assert "Cali" not in tools["bogota_search_datasets"].description


async def test_city_tools_point_at_their_own_siblings():
    """A parameter description naming the wrong portal's tool sends the model
    on a round trip that cannot succeed."""
    tools = await _tools()
    for city in CITIES:
        schema = tools[f"{city}_search_datasets"].inputSchema["properties"]
        assert f"{city}_list_organizations" in schema["organization"]["description"]
        assert f"{city}_list_groups" in schema["group"]["description"]


async def test_both_city_families_have_the_same_shape():
    """They come from one factory; divergence means a portal-specific branch."""
    tools = await _tools()
    for suffix in CITY_SUFFIXES:
        shapes = [
            sorted(tools[f"{c}_{suffix}"].inputSchema.get("properties") or {}) for c in CITIES
        ]
        assert shapes[0] == shapes[1], f"{suffix} differs between portals"


async def test_no_aggregation_tool_is_offered_for_any_city_portal():
    """Neither city portal exposes datastore_search_sql, so neither may
    advertise GROUP BY. A live test asserts the absence upstream."""
    names = set(await _tools())
    assert "aggregate_dataset" in names
    assert not [n for n in names if "aggregat" in n and n.startswith(("bogota_", "cali_"))]


async def test_national_search_can_reach_saved_views():
    """2,197 queryable saved views were invisible while `only` was hardcoded."""
    schema = (await _tools())["search_datasets"].inputSchema["properties"]
    assert "asset_type" in schema
    assert "filter" in schema["asset_type"]["enum"]
    assert "any" in schema["asset_type"]["enum"]


# ─── Identifier rejection happens before any network call ────────────────────


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        ("get_dataset", {"id": "not-a-4x4"}),
        ("download_dataset_preview", {"id": "../etc/passwd"}),
        ("filter_dataset", {"id": "abcd"}),
        ("aggregate_dataset", {"id": "abcd", "aggregations": [{"fn": "count"}]}),
        ("query_dataset_soql", {"id": "x", "soql_query": "SELECT 1"}),
    ],
)
async def test_bad_4x4_returns_envelope_without_calling_out(tool, kwargs):
    """No httpx_mock is registered, so any outbound request would fail loudly."""
    out = await fn(tool)(**kwargs)
    assert "error" in out
    assert "4x4" in out["error"]


@pytest.mark.parametrize("city", CITIES)
@pytest.mark.parametrize("suffix", ["resource_preview", "filter_resource"])
async def test_bad_uuid_returns_envelope_without_calling_out(city, suffix):
    out = await fn(f"{city}_{suffix}")(resource_id="not-a-uuid")
    assert "error" in out
    assert "UUID" in out["error"]
    assert f"{city}_get_dataset" in out["hint"], "the hint must name this portal's tool"


@pytest.mark.parametrize("city", CITIES)
async def test_bad_dataset_slug_returns_envelope(city):
    out = await fn(f"{city}_get_dataset")(id="../../admin")
    assert "error" in out
    assert f"{city}_search_datasets" in out["hint"]


# ─── Error envelope: portal failures never escape as exceptions ──────────────


class _Boom:
    """Stands in for a client whose every call fails the way an outage does."""

    def __init__(self, exc, portal=None):
        self._exc = exc
        if portal is not None:
            self.portal = portal

    def __getattr__(self, name):
        async def _raise(*a, **k):
            raise self._exc

        return _raise


NATIONAL_TOOLS = [
    ("search_datasets", {}),
    ("list_recent_datasets", {}),
    ("list_categories", {}),
    ("list_tags", {}),
    ("list_owners", {}),
    ("autocomplete", {"kind": "tag", "query": "salud"}),
    ("get_site_stats", {}),
    ("get_dataset", {"id": "abcd-1234"}),
    ("download_dataset_preview", {"id": "abcd-1234"}),
    ("filter_dataset", {"id": "abcd-1234"}),
    ("aggregate_dataset", {"id": "abcd-1234", "aggregations": [{"fn": "count"}]}),
    ("query_dataset_soql", {"id": "abcd-1234", "soql_query": "SELECT campo"}),
]


@pytest.mark.parametrize("tool,kwargs", NATIONAL_TOOLS)
async def test_national_tool_returns_envelope_when_portal_fails(monkeypatch, tool, kwargs):
    monkeypatch.setattr(server, "_client", _Boom(socrata.SocrataError("HTTP 503 from portal")))
    out = await fn(tool)(**kwargs)
    assert isinstance(out, dict), f"{tool} returned {type(out).__name__}"
    assert "error" in out and "503" in out["error"]
    assert "hint" in out and "datos.gov.co" in out["hint"]


CITY_TOOL_ARGS = {
    "search_datasets": {},
    "get_dataset": {"id": "hurtos-bogota"},
    "list_organizations": {},
    "list_groups": {},
    "list_tags": {},
    "get_site_stats": {},
    "resource_preview": {"resource_id": "dc751251-95ef-48bb-9785-329a3a5e0bdf"},
    "filter_resource": {"resource_id": "dc751251-95ef-48bb-9785-329a3a5e0bdf"},
}


@pytest.mark.parametrize("city", CITIES)
@pytest.mark.parametrize("suffix,kwargs", sorted(CITY_TOOL_ARGS.items()))
async def test_city_tool_returns_envelope_when_portal_fails(monkeypatch, city, suffix, kwargs):
    portal = ckan.PORTALS[city]
    boom = _Boom(ckan.CkanError("HTTP 500 blocked by WAF"), portal=portal)
    monkeypatch.setitem(server._ckan_clients, city, boom)

    out = await fn(f"{city}_{suffix}")(**kwargs)

    assert isinstance(out, dict), f"{city}_{suffix} returned {type(out).__name__}"
    assert "error" in out and "WAF" in out["error"]
    assert "hint" in out and portal.host in out["hint"], "the hint must name this portal"


async def test_autocomplete_rejects_unknown_kind(monkeypatch):
    """The client raises ValueError, not SocrataError, for a bad kind."""

    class _BadKind:
        async def autocomplete(self, **k):
            raise ValueError("kind must be one of: dataset, tag, category, owner")

    monkeypatch.setattr(server, "_client", _BadKind())
    out = await fn("autocomplete")(kind="dataset", query="x")
    assert "error" in out
    assert "kind" in out["error"]


# ─── Named envelopes on the listing tools ────────────────────────────────────
#
# These four used to return bare lists. A bare list cannot carry an error, so a
# portal outage had nowhere to go but an exception.


class _Lists:
    async def domain_categories(self):
        return ["Salud", "Educación"]

    async def domain_tags(self):
        return ["presupuesto", "salud"]

    async def domain_owners(self, limit=50):
        return [{"owner": "MinSalud", "count": 42}]

    async def autocomplete(self, kind, query, limit=10):
        return ["Salud y Protección Social"]


@pytest.mark.parametrize(
    "tool,kwargs,key",
    [
        ("list_categories", {}, "categories"),
        ("list_tags", {}, "tags"),
        ("list_owners", {}, "owners"),
        ("autocomplete", {"kind": "category", "query": "sal"}, "matches"),
    ],
)
async def test_listing_tools_return_named_envelopes(monkeypatch, tool, kwargs, key):
    monkeypatch.setattr(server, "_client", _Lists())
    out = await fn(tool)(**kwargs)
    assert isinstance(out, dict)
    assert out["country"] == "CO"
    assert isinstance(out[key], list)
    assert out["count"] == len(out[key])


# ─── Happy paths through the city tools ──────────────────────────────────────


class _FakeCkan:
    def __init__(self, portal):
        self.portal = portal
        self.last: dict = {}

    async def package_search(self, **k):
        self.last = k
        return {"count": 1234, "results": []}

    async def package_show(self, dataset_id):
        return {"id": "u", "name": "un-dataset", "title": "Un dataset", "resources": []}

    async def organization_list(self, limit=60):
        return [{"name": "sec-x", "title": "Secretaría X", "package_count": 12}]

    async def group_list(self, limit=50):
        return [{"name": "movilidad", "title": "Movilidad", "package_count": 30}]

    async def tag_list(self, limit=100):
        return ["hurto", "movilidad"]

    async def datastore_search(self, resource_id, **k):
        self.last = k
        return {
            "total": 464,
            "fields": [{"id": "Comuna", "type": "text"}],
            "records": [{"Comuna": "22"}],
        }


def use_fake(monkeypatch, city) -> _FakeCkan:
    """Put a fake client in the registry the tools read from."""
    fake = _FakeCkan(ckan.PORTALS[city])
    monkeypatch.setitem(server._ckan_clients, city, fake)
    return fake


@pytest.mark.parametrize("city", CITIES)
async def test_city_search_passes_pagination_through(monkeypatch, city):
    fake = use_fake(monkeypatch, city)
    out = await fn(f"{city}_search_datasets")(query="x", limit=5, offset=10)
    assert out["total"] == 1234
    assert out["portal"] == ckan.PORTALS[city].host
    assert fake.last["rows"] == 5
    assert fake.last["start"] == 10


@pytest.mark.parametrize("city", CITIES)
async def test_city_get_dataset_returns_that_portals_url(monkeypatch, city):
    portal = ckan.PORTALS[city]
    use_fake(monkeypatch, city)
    out = await fn(f"{city}_get_dataset")(id="un-dataset")
    assert out["url"] == f"https://{portal.host}/dataset/un-dataset"
    assert out["city"] == portal.city


@pytest.mark.parametrize("city", CITIES)
async def test_city_filter_echoes_applied_filters(monkeypatch, city):
    fake = use_fake(monkeypatch, city)
    out = await fn(f"{city}_filter_resource")(
        resource_id="dc751251-95ef-48bb-9785-329a3a5e0bdf",
        filters={"Comuna": 22},
        columns=["Comuna"],
        sort="Comuna asc",
        limit=5,
    )
    assert out["filters_applied"] == {"Comuna": 22}
    assert out["rows_returned"] == 1
    assert fake.last["fields"] == ["Comuna"]
    assert fake.last["limit"] == 5


@pytest.mark.parametrize("city", CITIES)
async def test_city_listing_tools_return_named_envelopes(monkeypatch, city):
    portal = ckan.PORTALS[city]
    use_fake(monkeypatch, city)
    orgs = await fn(f"{city}_list_organizations")()
    groups = await fn(f"{city}_list_groups")()
    tags = await fn(f"{city}_list_tags")()
    assert orgs["organizations"][0]["dataset_count"] == 12
    assert groups["groups"][0]["name"] == "movilidad"
    assert tags["count"] == 2
    for out in (orgs, groups, tags):
        assert out["portal"] == portal.host
        assert out["city"] == portal.city


# ─── Shutdown ────────────────────────────────────────────────────────────────


async def test_close_clients_closes_every_client(monkeypatch):
    """A leaked httpx client keeps the process alive after stdin closes.

    This iterates the registry rather than naming two clients, so adding a
    fourth portal cannot silently leave one open.
    """
    closed = []

    class _C:
        def __init__(self, tag):
            self.tag = tag

        async def close(self):
            closed.append(self.tag)

    monkeypatch.setattr(server, "_client", _C("socrata"))
    monkeypatch.setattr(server, "_ckan_clients", {"bogota": _C("bogota"), "cali": _C("cali")})
    await server._close_clients()
    assert sorted(closed) == ["bogota", "cali", "socrata"]
