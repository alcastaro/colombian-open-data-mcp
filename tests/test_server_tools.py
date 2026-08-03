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

import re

import pytest

from colombian_open_data_mcp import ckan, server, socrata

CITIES = ["bogota", "cali", "valle", "cartagena"]
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
    """Twelve national tools plus twelve city ones, covering four portals.

    Before v0.4 this was twelve plus eight per portal. Four portals that way
    would be forty-four tools saying the same thing four times; the `city`
    parameter covers twice the portals in half the surface.
    """
    tools = await _tools()
    national = {n for n in tools if not n.startswith("city_")}
    city_tools = {n for n in tools if n.startswith("city_")}
    assert len(national) == 12
    assert len(city_tools) == 12
    assert len(tools) == 24


@pytest.mark.parametrize("suffix", CITY_SUFFIXES)
async def test_every_city_tool_exists(suffix):
    assert f"city_{suffix}" in await _tools()


async def test_every_city_tool_accepts_every_portal():
    """The `city` enum and the portal registry must not drift apart.

    A value in the schema with no portal behind it is a tool the model will
    call and that can only fail; a portal missing from the schema is one it
    can never reach.
    """
    tools = await _tools()
    for name in (n for n in tools if n.startswith("city_")):
        enum = (
            tools[name].input_schema["$defs"]["CityKey"]["enum"]
            if "$defs" in tools[name].input_schema
            else tools[name].input_schema["properties"]["city"]["enum"]
        )
        assert sorted(enum) == sorted(ckan.PORTALS), name


async def test_every_tool_is_annotated_read_only():
    for name, tool in (await _tools()).items():
        ann = tool.annotations
        assert ann is not None, f"{name} has no annotations"
        assert ann.read_only_hint is True, f"{name} is not marked read-only"
        assert ann.open_world_hint is True, f"{name} is not marked open-world"
        assert ann.title, f"{name} has no human title"


async def test_every_tool_has_a_description():
    """The factory's first version assigned __doc__ after the decorator ran,
    which registers eight tools with no description at all."""
    missing = [n for n, t in (await _tools()).items() if not (t.description or "").strip()]
    assert missing == []


async def test_city_tool_descriptions_name_every_portal():
    """One tool now serves four catalogues, so its description has to say which.

    A model choosing a `city` value cannot infer that Bogotá is thirty times
    larger than Cartagena but returns rows a third as often. Both facts are in
    the text for that reason.
    """
    text = (await _tools())["city_search_datasets"].description
    for portal in ckan.PORTALS.values():
        assert portal.name in text, portal.key
        assert portal.city in text, portal.key


async def test_city_tools_point_at_their_own_siblings():
    """A parameter description naming a tool that does not exist sends the
    model on a round trip that cannot succeed."""
    schema = (await _tools())["city_search_datasets"].input_schema["properties"]
    assert "city_list_organizations" in schema["organization"]["description"]
    assert "city_list_groups" in schema["group"]["description"]


async def test_every_city_tool_takes_a_city_parameter():
    """The collapse is only sound if `city` is genuinely required everywhere.

    A city tool that defaulted to one portal would answer questions about
    Bogotá when asked about Cartagena, and nothing in the response would say so.
    """
    tools = await _tools()
    for name in (n for n in tools if n.startswith("city_")):
        schema = tools[name].input_schema
        assert "city" in (schema.get("properties") or {}), name
        assert "city" in (schema.get("required") or []), f"{name} does not require a city"


async def test_no_datastore_aggregation_tool_is_offered_for_the_city_portals():
    """None of the four exposes datastore_search_sql, so none may advertise a
    DataStore GROUP BY. A live test asserts the absence upstream.

    `city_esri_aggregate` is not a counterexample and is excluded deliberately:
    it aggregates on an ArcGIS service, which is a different system that really
    does compute the rollup remotely.
    """
    names = set(await _tools())
    assert "aggregate_dataset" in names
    assert [n for n in names if "aggregat" in n and n.startswith("city_")] == [
        "city_esri_aggregate"
    ]


async def test_national_search_can_reach_saved_views():
    """2,197 queryable saved views were invisible while `only` was hardcoded."""
    schema = (await _tools())["search_datasets"].input_schema["properties"]
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
@pytest.mark.parametrize(
    "suffix",
    [
        "resource_preview",
        "filter_resource",
        "esri_query",
        "esri_service_info",
        "read_resource_file",
    ],
)
async def test_bad_uuid_returns_envelope_without_calling_out(city, suffix):
    out = await fn(f"city_{suffix}")(city=city, resource_id="not-a-uuid")
    assert "error" in out
    assert "UUID" in out["error"]
    assert "city_get_dataset" in out["hint"]


@pytest.mark.parametrize("city", CITIES)
async def test_bad_dataset_slug_returns_envelope(city):
    out = await fn("city_get_dataset")(city=city, id="../../admin")
    assert "error" in out
    assert "city_search_datasets" in out["hint"]


@pytest.mark.parametrize(
    "suffix",
    [
        "search_datasets",
        "get_dataset",
        "list_tags",
        "resource_preview",
        "esri_query",
        "read_resource_file",
    ],
)
async def test_an_unknown_city_is_refused_before_any_request(suffix):
    """The enum makes this unreachable through a conforming client, and it is
    still checked. A tool that trusted the schema would raise a KeyError on a
    non-conforming one, and a KeyError reaches the model as an opaque protocol
    error rather than as something it can correct."""
    import inspect

    tool = fn(f"city_{suffix}")
    accepted = inspect.signature(tool).parameters
    kwargs = {k: v for k, v in {"resource_id": "x", "id": "x"}.items() if k in accepted}
    out = await tool(city="medellin", **kwargs)
    assert "error" in out and "medellin" in out["error"]
    assert "bogota" in out["hint"]


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

    out = await fn(f"city_{suffix}")(city=city, **kwargs)

    assert isinstance(out, dict), f"city_{suffix} returned {type(out).__name__}"
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
    out = await fn("city_search_datasets")(city=city, query="x", limit=5, offset=10)
    assert out["total"] == 1234
    assert out["portal"] == ckan.PORTALS[city].host
    assert fake.last["rows"] == 5
    assert fake.last["start"] == 10


@pytest.mark.parametrize("city", CITIES)
async def test_city_get_dataset_returns_that_portals_url(monkeypatch, city):
    portal = ckan.PORTALS[city]
    use_fake(monkeypatch, city)
    out = await fn("city_get_dataset")(city=city, id="un-dataset")
    assert out["url"] == f"https://{portal.host}/dataset/un-dataset"
    assert out["city"] == portal.city


@pytest.mark.parametrize("city", CITIES)
async def test_city_filter_echoes_applied_filters(monkeypatch, city):
    fake = use_fake(monkeypatch, city)
    out = await fn("city_filter_resource")(
        city=city,
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
    orgs = await fn("city_list_organizations")(city=city)
    groups = await fn("city_list_groups")(city=city)
    tags = await fn("city_list_tags")(city=city)
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


# ─── Beyond the DataStore: ESRI and file tools ───────────────────────────────
#
# These four tools are the only ones in the server that contact a host this
# source file does not name. What keeps that safe is not a rule about URLs — it
# is that the model never supplies one. It supplies a resource UUID, and the
# address is read out of the portal's own catalogue. The first test below is
# the one that proves that property, and it is the one to keep if this section
# ever has to shrink.


class _FakeResourceCkan(_FakeCkan):
    """A CKAN client whose catalogue points every resource at one address."""

    def __init__(self, portal, url, fmt="ESRI REST"):
        super().__init__(portal)
        self.url = url
        self.fmt = fmt
        self.shown: list[str] = []

    async def resource_show(self, resource_id):
        self.shown.append(resource_id)
        return {"id": resource_id, "url": self.url, "format": self.fmt}


LAYER_URL = "https://serviciosgis.example.gov.co/arcgis/rest/services/m/metro/MapServer/4"
FILE_URL = "https://datosabiertos.example.gov.co/dataset/a/resource/b/download/d.csv"
RESOURCE_UUID = "dc751251-95ef-48bb-9785-329a3a5e0bdf"
EXAMPLE = re.compile(r"https://.*example\.gov\.co/.*")


def use_resource_fake(monkeypatch, city, url, fmt="ESRI REST") -> _FakeResourceCkan:
    fake = _FakeResourceCkan(ckan.PORTALS[city], url, fmt)
    monkeypatch.setitem(server._ckan_clients, city, fake)
    return fake


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        ("city_esri_service_info", {}),
        ("city_esri_query", {}),
        ("city_esri_aggregate", {"aggregations": [{"fn": "count", "col": "OBJECTID"}]}),
        ("city_read_resource_file", {}),
    ],
)
async def test_the_address_comes_from_the_catalogue_not_from_the_caller(
    monkeypatch, httpx_mock, tool, kwargs
):
    """No tool in this server accepts a URL, and this is the test that says so.

    An argument taking a URL would let a model — or anything steering one —
    point this server at an arbitrary host. Taking a resource UUID instead
    means the set of reachable addresses is bounded by what a Colombian
    government catalogue publishes, before netguard's policy applies on top.
    """
    import inspect

    fn_ = fn(tool)
    assert "url" not in inspect.signature(fn_).parameters, f"{tool} must not take a URL"

    url = FILE_URL if tool == "city_read_resource_file" else LAYER_URL
    fmt = "CSV" if tool == "city_read_resource_file" else "ESRI REST"
    fake = use_resource_fake(monkeypatch, "bogota", url, fmt)
    httpx_mock.add_response(url=EXAMPLE, content=b"a,b\n1,2\n", is_reusable=True)

    await fn_(city="bogota", resource_id=RESOURCE_UUID, **kwargs)

    assert fake.shown == [RESOURCE_UUID], "the tool must look the address up, not invent it"


async def test_esri_service_info_returns_a_trimmed_description(monkeypatch, httpx_mock):
    use_resource_fake(monkeypatch, "bogota", LAYER_URL)
    httpx_mock.add_response(
        url=EXAMPLE,
        json={
            "name": "Estaciones",
            "type": "Feature Layer",
            "supportsStatistics": True,
            "drawingInfo": {"renderer": "enormous"},
            "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}],
        },
    )
    httpx_mock.add_response(url=EXAMPLE, json={"count": 11})
    out = await fn("city_esri_service_info")(city="bogota", resource_id=RESOURCE_UUID)
    assert out["name"] == "Estaciones"
    assert out["supports_statistics"] is True
    assert "drawingInfo" not in out


async def test_esri_query_returns_flat_rows(monkeypatch, httpx_mock):
    use_resource_fake(monkeypatch, "bogota", LAYER_URL)
    httpx_mock.add_response(
        url=EXAMPLE,
        json={"fields": [], "features": [{"attributes": {"NOMBRE": "ESTACIÓN 1"}}]},
    )
    out = await fn("city_esri_query")(city="bogota", resource_id=RESOURCE_UUID, limit=1)
    assert out["rows"] == [{"NOMBRE": "ESTACIÓN 1"}]
    assert out["city"] == "Bogotá D.C."


async def test_esri_aggregate_marks_that_the_rollup_ran_remotely(monkeypatch, httpx_mock):
    """The one territorial aggregation in this server that is not summed in the
    model's context. Saying so in the envelope is how the model can tell."""
    use_resource_fake(monkeypatch, "bogota", LAYER_URL)
    httpx_mock.add_response(
        url=EXAMPLE, json={"fields": [], "features": [{"attributes": {"TIPO": "A", "N": 11}}]}
    )
    out = await fn("city_esri_aggregate")(
        city="bogota",
        resource_id=RESOURCE_UUID,
        aggregations=[{"fn": "count", "col": "OBJECTID", "alias": "n"}],
        group_by=["TIPO"],
    )
    assert out["aggregated_on_server"] is True
    assert out["grouped_by"] == ["TIPO"]
    assert out["rows"] == [{"TIPO": "A", "N": 11}]


async def test_esri_aggregate_without_aggregations_explains_what_to_pass(monkeypatch):
    use_resource_fake(monkeypatch, "bogota", LAYER_URL)
    out = await fn("city_esri_aggregate")(city="bogota", resource_id=RESOURCE_UUID)
    assert "error" in out
    assert "statisticType" in out["hint"] or "count" in out["hint"]


async def test_an_esri_injection_attempt_never_leaves_the_process(monkeypatch, httpx_mock):
    use_resource_fake(monkeypatch, "bogota", LAYER_URL)
    out = await fn("city_esri_query")(
        city="bogota", resource_id=RESOURCE_UUID, where="1=1; DROP TABLE capas"
    )
    assert "error" in out
    assert httpx_mock.get_requests() == [], "the request must be refused before it is sent"


async def test_a_resource_with_no_registered_url_is_reported(monkeypatch):
    class _NoUrl(_FakeCkan):
        async def resource_show(self, resource_id):
            return {"id": resource_id, "url": "", "format": "CSV"}

    monkeypatch.setitem(server._ckan_clients, "bogota", _NoUrl(ckan.BOGOTA))
    out = await fn("city_read_resource_file")(city="bogota", resource_id=RESOURCE_UUID)
    assert "error" in out and "no URL" in out["error"]


async def test_a_shapefile_labelled_esri_rest_is_routed_to_the_file_reader(monkeypatch):
    """Measured on the live catalogue: `format` is a hint, not a promise."""
    use_resource_fake(
        monkeypatch, "bogota", "https://datosabiertos.example.gov.co/d/a/download/shape.zip"
    )
    out = await fn("city_esri_query")(city="bogota", resource_id=RESOURCE_UUID)
    assert "error" in out
    assert "city_read_resource_file" in out["error"]


async def test_reading_a_published_csv_returns_rows(monkeypatch, httpx_mock):
    use_resource_fake(monkeypatch, "bogota", FILE_URL, "CSV")
    httpx_mock.add_response(url=EXAMPLE, content="ID;LOCALIDAD\n1;USAQUÉN\n".encode("cp1252"))
    out = await fn("city_read_resource_file")(city="bogota", resource_id=RESOURCE_UUID, rows=5)
    assert out["rows"] == [{"ID": "1", "LOCALIDAD": "USAQUÉN"}]
    assert out["portal"] == ckan.BOGOTA.host
    assert out["resource_id"] == RESOURCE_UUID


async def test_an_unreadable_format_returns_an_envelope_not_an_exception(monkeypatch):
    use_resource_fake(monkeypatch, "cartagena", "https://x.example.gov.co/capa.zip", "SHP")
    out = await fn("city_read_resource_file")(city="cartagena", resource_id=RESOURCE_UUID)
    assert "error" in out and "SHP" in out["error"]


async def test_a_refused_address_is_reported_as_policy_not_as_an_outage(monkeypatch):
    """A model told 'the portal is down' will retry forever. Told the address
    was refused on policy, it knows nothing it passes will change the outcome.
    """
    from colombian_open_data_mcp import netguard

    monkeypatch.delenv(netguard.MODE_ENV, raising=False)

    async def resolves_private(host):
        return ["127.0.0.1"]

    monkeypatch.setattr(netguard, "resolve_host", resolves_private)
    use_resource_fake(monkeypatch, "bogota", FILE_URL, "CSV")

    out = await fn("city_read_resource_file")(city="bogota", resource_id=RESOURCE_UUID)

    assert "error" in out and "non-public address" in out["error"]
    assert "network policy" in out["hint"]


async def test_esri_service_info_reports_the_layer_feature_count(monkeypatch, httpx_mock):
    """A few hundred features can be listed; forty thousand should be
    aggregated. The model cannot tell which without being told."""
    use_resource_fake(monkeypatch, "bogota", LAYER_URL)
    httpx_mock.add_response(url=EXAMPLE, json={"name": "Capa", "fields": []})
    httpx_mock.add_response(url=EXAMPLE, json={"count": 41234})
    out = await fn("city_esri_service_info")(city="bogota", resource_id=RESOURCE_UUID)
    assert out["feature_count"] == 41234


async def test_a_layer_that_refuses_a_count_still_returns_its_schema(monkeypatch, httpx_mock):
    """Losing the whole description because one optional extra failed would be
    a worse answer than an incomplete one."""
    use_resource_fake(monkeypatch, "bogota", LAYER_URL)
    httpx_mock.add_response(url=EXAMPLE, json={"name": "Capa", "fields": []})
    httpx_mock.add_response(
        url=EXAMPLE, json={"error": {"code": 400, "message": "Failed to execute query."}}
    )
    out = await fn("city_esri_service_info")(city="bogota", resource_id=RESOURCE_UUID)
    assert out["name"] == "Capa"
    assert out["feature_count"] is None
