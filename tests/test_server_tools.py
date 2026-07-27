"""Tests for the MCP tool layer itself, not the clients underneath it.

Three things are checked here that unit tests on the clients cannot see:

1. **Every tool is registered with read-only annotations.** A host uses those to
   decide whether to prompt before calling.
2. **No tool raises.** A portal outage has to arrive at the model as a dict with
   a hint, not as a protocol error carrying a traceback. Eight of the twelve
   original tools leaked their exception; this is the regression guard.
3. **The two tool families stay separate**, with Bogotá offering no aggregation
   — because that portal cannot do it.
"""

from __future__ import annotations

import pytest

from colombian_open_data_mcp import ckan, server, socrata

# ─── Registration ────────────────────────────────────────────────────────────


async def _tools():
    return {t.name: t for t in await server.mcp.list_tools()}


async def test_expected_tool_families_are_registered():
    tools = await _tools()
    national = {n for n in tools if not n.startswith("bogota_")}
    bogota = {n for n in tools if n.startswith("bogota_")}
    assert len(national) == 12
    assert len(bogota) == 8


async def test_every_tool_is_annotated_read_only():
    for name, tool in (await _tools()).items():
        ann = tool.annotations
        assert ann is not None, f"{name} has no annotations"
        assert ann.readOnlyHint is True, f"{name} is not marked read-only"
        assert ann.openWorldHint is True, f"{name} is not marked open-world"
        assert ann.title, f"{name} has no human title"


async def test_no_aggregation_tool_is_offered_for_bogota():
    """Bogotá has no datastore_search_sql, so it must not advertise GROUP BY.

    Verified against the live portal: the action is unregistered (HTTP 400
    "Action name not known") and a WAF blocks the GET form. Offering an
    aggregation tool would be promising something the portal cannot do.
    """
    names = set(await _tools())
    assert "aggregate_dataset" in names
    assert not any("aggregat" in n for n in names if n.startswith("bogota_"))


async def test_socrata_tools_do_not_leak_into_bogota_naming():
    names = set(await _tools())
    assert "bogota_query_soql" not in names
    assert "bogota_filter_resource" in names


# ─── Identifier rejection happens before any network call ────────────────────


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        (server.get_dataset, {"id": "not-a-4x4"}),
        (server.download_dataset_preview, {"id": "../etc/passwd"}),
        (server.filter_dataset, {"id": "abcd"}),
        (server.aggregate_dataset, {"id": "abcd", "aggregations": [{"fn": "count"}]}),
        (server.query_dataset_soql, {"id": "x", "soql_query": "SELECT 1"}),
    ],
)
async def test_bad_4x4_returns_envelope_without_calling_out(tool, kwargs):
    """No httpx_mock is registered, so any outbound request would fail the test."""
    out = await tool(**kwargs)
    assert "error" in out
    assert "4x4" in out["error"]


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        (server.bogota_resource_preview, {"resource_id": "hurtos-bogota"}),
        (server.bogota_filter_resource, {"resource_id": "not-a-uuid"}),
    ],
)
async def test_bad_uuid_returns_envelope_without_calling_out(tool, kwargs):
    out = await tool(**kwargs)
    assert "error" in out
    assert "UUID" in out["error"]


async def test_bad_dataset_slug_returns_envelope():
    out = await server.bogota_get_dataset(id="../../admin")
    assert "error" in out
    assert "hint" in out


# ─── Error envelope: portal failures never escape as exceptions ──────────────


class _Boom:
    """Stands in for a client whose every call fails the way a real outage does."""

    def __init__(self, exc):
        self._exc = exc

    def __getattr__(self, name):
        async def _raise(*a, **k):
            raise self._exc

        return _raise


NATIONAL_TOOLS = [
    (server.search_datasets, {}),
    (server.list_recent_datasets, {}),
    (server.list_categories, {}),
    (server.list_tags, {}),
    (server.list_owners, {}),
    (server.autocomplete, {"kind": "tag", "query": "salud"}),
    (server.get_site_stats, {}),
    (server.get_dataset, {"id": "abcd-1234"}),
    (server.download_dataset_preview, {"id": "abcd-1234"}),
    (server.filter_dataset, {"id": "abcd-1234"}),
    (server.aggregate_dataset, {"id": "abcd-1234", "aggregations": [{"fn": "count"}]}),
    (server.query_dataset_soql, {"id": "abcd-1234", "soql_query": "SELECT campo"}),
]


@pytest.mark.parametrize("tool,kwargs", NATIONAL_TOOLS)
async def test_national_tool_returns_envelope_when_portal_fails(monkeypatch, tool, kwargs):
    monkeypatch.setattr(
        server, "_client", _Boom(socrata.SocrataError("HTTP 503 from datos.gov.co"))
    )
    out = await tool(**kwargs)
    assert isinstance(out, dict), f"{tool.__name__} returned {type(out).__name__}"
    assert "error" in out and "503" in out["error"]
    assert "hint" in out and "datos.gov.co" in out["hint"]


BOGOTA_TOOLS = [
    (server.bogota_search_datasets, {}),
    (server.bogota_get_dataset, {"id": "hurtos-bogota"}),
    (server.bogota_list_organizations, {}),
    (server.bogota_list_groups, {}),
    (server.bogota_list_tags, {}),
    (server.bogota_get_site_stats, {}),
    (server.bogota_resource_preview, {"resource_id": "dc751251-95ef-48bb-9785-329a3a5e0bdf"}),
    (server.bogota_filter_resource, {"resource_id": "dc751251-95ef-48bb-9785-329a3a5e0bdf"}),
]


@pytest.mark.parametrize("tool,kwargs", BOGOTA_TOOLS)
async def test_bogota_tool_returns_envelope_when_portal_fails(monkeypatch, tool, kwargs):
    boom = _Boom(ckan.CkanError("HTTP 500 blocked by WAF"))
    boom.portal = ckan.BOGOTA  # tools read .portal for formatting
    monkeypatch.setattr(server, "_bogota", boom)
    out = await tool(**kwargs)
    assert isinstance(out, dict), f"{tool.__name__} returned {type(out).__name__}"
    assert "error" in out and "WAF" in out["error"]
    assert "hint" in out and "bogota" in out["hint"]


async def test_autocomplete_rejects_unknown_kind(monkeypatch):
    """The client raises ValueError, not SocrataError, for a bad kind."""

    class _BadKind:
        async def autocomplete(self, **k):
            raise ValueError("kind must be one of: dataset, tag, category, owner")

    monkeypatch.setattr(server, "_client", _BadKind())
    out = await server.autocomplete(kind="dataset", query="x")
    assert "error" in out
    assert "kind" in out["error"]


# ─── Named envelopes on the listing tools ────────────────────────────────────
#
# These four used to return bare lists. A bare list cannot carry an error, so
# a portal outage had nowhere to go but an exception. Wrapping them is what
# made the guarantee above possible.


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
        (server.list_categories, {}, "categories"),
        (server.list_tags, {}, "tags"),
        (server.list_owners, {}, "owners"),
        (server.autocomplete, {"kind": "category", "query": "sal"}, "matches"),
    ],
)
async def test_listing_tools_return_named_envelopes(monkeypatch, tool, kwargs, key):
    monkeypatch.setattr(server, "_client", _Lists())
    out = await tool(**kwargs)
    assert isinstance(out, dict)
    assert out["country"] == "CO"
    assert isinstance(out[key], list)
    assert out["count"] == len(out[key])


# ─── Happy paths through the Bogotá tools ────────────────────────────────────


class _FakeBogota:
    portal = ckan.BOGOTA

    async def package_search(self, **k):
        self.last = k
        return {"count": 1917, "results": []}

    async def package_show(self, dataset_id):
        return {"id": "u", "name": "hurtos-bogota", "title": "Hurtos", "resources": []}

    async def organization_list(self, limit=60):
        return [{"name": "sec-seguridad", "title": "Seguridad", "package_count": 12}]

    async def group_list(self, limit=50):
        return [{"name": "movilidad", "title": "Movilidad", "package_count": 30}]

    async def tag_list(self, limit=100):
        return ["hurto", "movilidad"]

    async def datastore_search(self, resource_id, **k):
        self.last = k
        return {
            "total": 464,
            "fields": [{"id": "Localidad", "type": "text"}],
            "records": [{"Localidad": "Bosa"}],
        }


async def test_bogota_search_passes_pagination_through(monkeypatch):
    fake = _FakeBogota()
    monkeypatch.setattr(server, "_bogota", fake)
    out = await server.bogota_search_datasets(query="hurtos", limit=5, offset=10)
    assert out["total"] == 1917
    assert fake.last["rows"] == 5
    assert fake.last["start"] == 10


async def test_bogota_get_dataset_returns_portal_url(monkeypatch):
    monkeypatch.setattr(server, "_bogota", _FakeBogota())
    out = await server.bogota_get_dataset(id="hurtos-bogota")
    assert out["url"] == "https://datosabiertos.bogota.gov.co/dataset/hurtos-bogota"
    assert out["city"] == "Bogotá D.C."


async def test_bogota_filter_echoes_applied_filters(monkeypatch):
    fake = _FakeBogota()
    monkeypatch.setattr(server, "_bogota", fake)
    out = await server.bogota_filter_resource(
        resource_id="dc751251-95ef-48bb-9785-329a3a5e0bdf",
        filters={"Localidad": "Bosa"},
        columns=["Localidad"],
        sort="Localidad asc",
        limit=5,
    )
    assert out["filters_applied"] == {"Localidad": "Bosa"}
    assert out["rows_returned"] == 1
    assert fake.last["fields"] == ["Localidad"]
    assert fake.last["limit"] == 5


async def test_bogota_listing_tools_return_named_envelopes(monkeypatch):
    monkeypatch.setattr(server, "_bogota", _FakeBogota())
    orgs = await server.bogota_list_organizations()
    groups = await server.bogota_list_groups()
    tags = await server.bogota_list_tags()
    assert orgs["organizations"][0]["dataset_count"] == 12
    assert groups["groups"][0]["name"] == "movilidad"
    assert tags["count"] == 2
    for out in (orgs, groups, tags):
        assert out["portal"] == "datosabiertos.bogota.gov.co"


# ─── Shutdown ────────────────────────────────────────────────────────────────


async def test_close_clients_closes_both(monkeypatch):
    """A leaked httpx client keeps the process alive after stdin closes."""
    closed = []

    class _C:
        def __init__(self, tag):
            self.tag = tag

        async def close(self):
            closed.append(self.tag)

    monkeypatch.setattr(server, "_client", _C("socrata"))
    monkeypatch.setattr(server, "_bogota", _C("ckan"))
    await server._close_clients()
    assert sorted(closed) == ["ckan", "socrata"]
