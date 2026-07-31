"""Opt-in live tests against the two real portals.

Skipped by default. Enable with: RUN_LIVE_TESTS=1 pytest tests/test_live.py

Never run in CI. Both portals are third-party infrastructure we do not
control; the Bogotá one additionally sits behind a WAF. A build that goes red
because someone else's rate limiter had a bad minute is a build nobody trusts.
"""

from __future__ import annotations

import pytest

from colombian_open_data_mcp import ckan, socrata

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
async def _client():
    """Fresh client per test to avoid event-loop binding issues."""
    c = socrata.SocrataClient()
    yield c
    await c.close()


async def test_live_catalog_search_returns_hits(_client):
    raw = await _client.catalog_search(query="presupuesto", limit=3)
    formatted = socrata.format_catalog_response(raw)
    assert formatted["country"] == "CO"
    assert formatted["total"] > 0
    assert len(formatted["datasets"]) > 0
    for d in formatted["datasets"]:
        assert socrata.is_valid_4x4(d["id"])


async def test_live_site_stats(_client):
    s = await _client.site_stats()
    assert s["country"] == "CO"
    assert s["platform"] == "socrata"
    assert (s["total_datasets"] or 0) > 1000


async def test_live_list_categories(_client):
    cats = await _client.domain_categories()
    assert isinstance(cats, list)
    assert len(cats) > 0


async def test_live_list_tags(_client):
    tags = await _client.domain_tags()
    assert isinstance(tags, list)
    assert len(tags) > 10


async def test_live_recent_datasets(_client):
    raw = await _client.catalog_recent(limit=3)
    formatted = socrata.format_catalog_response(raw)
    assert formatted["returned"] >= 1


async def test_live_get_view_for_first_search_hit(_client):
    raw = await _client.catalog_search(query="presupuesto", limit=1)
    first = raw.get("results", [])[0]
    four = first["resource"]["id"]
    view = await _client.get_view(four)
    formatted = socrata.format_view(view)
    assert formatted["id"] == four
    assert isinstance(formatted["columns"], list)


# ─── City portals on CKAN — Bogotá and Cali ──────────────────────────────────
#
# Parametrised over the portal registry rather than written per city, so adding
# a fourth portal is covered the moment its descriptor exists. These stay out of
# CI on purpose: both sit behind a WAF, and one already refused a probe from a
# laptop during development. A shared runner IP fares worse, and a build that
# reddens on someone else's rate limiter is a build people learn to ignore.

CKAN_KEYS = sorted(ckan.PORTALS)


@pytest.fixture(params=CKAN_KEYS, ids=CKAN_KEYS)
async def city(request):
    c = ckan.CkanClient(ckan.PORTALS[request.param])
    yield c
    await c.close()


async def test_live_city_catalogue_is_reachable(city):
    stats = await city.site_stats()
    assert stats["platform"] == "ckan"
    assert stats["city"] == city.portal.city
    # Valle publishes 50 datasets and Cartagena 38, so the old floor of 100
    # would have failed on two real portals. The point of the assertion is that
    # a catalogue came back at all, not that it is large.
    assert (stats["total_datasets"] or 0) >= 20
    assert stats["datastore_sql_available"] is False
    assert "city_filter_resource" in stats["datastore_note"]
    assert city.portal.key in stats["datastore_note"]


async def test_live_city_search_returns_datasets(city):
    body = await city.package_search(query="salud", rows=3)
    out = ckan.format_search_response(body, city.portal)
    assert out["total"] > 0
    for d in out["datasets"]:
        assert d["url"].startswith(f"https://{city.portal.host}/dataset/")
        assert isinstance(d["resources"], list)


async def test_live_city_organizations_and_groups(city):
    orgs = await city.organization_list(limit=5)
    groups = await city.group_list(limit=5)
    assert len(orgs) > 0 and len(groups) > 0
    assert ckan.format_organization(orgs[0], city.portal)["name"]
    assert ckan.format_group(groups[0], city.portal)["name"]


async def test_live_city_datastore_returns_typed_rows(city):
    """Walk the catalogue for a resource with a table, then read it.

    Written as a walk rather than a hardcoded resource id because ids change
    between republications, and a test that dies for that reason teaches the
    reader nothing. It also has to tolerate the portal flagging resources that
    have no table — measured at roughly a third of the flagged ones — so a 404
    means keep looking, not fail.
    """
    read = None
    for start in (0, 100, 300, 600):
        body = await city.package_search(rows=25, start=start)
        for pkg in body.get("results", []):
            for res in pkg.get("resources", []):
                if not (res.get("datastore_active") and ckan.is_valid_uuid(res.get("id", ""))):
                    continue
                try:
                    read = await city.datastore_search(res["id"], limit=3)
                except ckan.CkanError as e:
                    assert "404" in str(e) or "catalogue flags" in str(e)
                    continue
                break
            if read:
                break
        if read:
            break

    assert read is not None, "no readable DataStore resource found in the sampled catalogue"
    out = ckan.format_datastore_result(read, "x" * 8, city.portal)
    assert out["rows_returned"] >= 1
    assert len(out["fields"]) > 0


async def test_live_city_has_no_sql_endpoint(city):
    """The absence this whole design rests on — asserted, not assumed.

    Bogotá answers 400 (action unregistered), Cali 403 (a guard in front of it),
    Valle and Cartagena 400. Different mechanisms, identical consequence: no
    server-side GROUP BY on any of the four, therefore no DataStore aggregation
    tool. If any portal ever enables it, this test fails and that is the signal
    to add one.
    """
    with pytest.raises(ckan.CkanError):
        await city.action("datastore_search_sql", {"sql": "SELECT 1"})


async def test_live_city_reports_the_ckan_release_the_descriptor_claims(city):
    """Cartagena runs CKAN 2.11.3 while the other three run 2.10.4.

    Nothing in this client branches on the version — the actions used are
    identical across those releases — which is exactly why the claim is worth
    checking rather than trusting. A portal upgrading underneath us should show
    up as a failing test, not as a tool that quietly stopped working.
    """
    reported = await city.action("status_show")
    assert reported["ckan_version"].startswith(city.portal.ckan_version), (
        f"{city.portal.key} reports {reported['ckan_version']}, "
        f"descriptor claims {city.portal.ckan_version}"
    )


async def test_live_bogota_esri_layer_answers_and_aggregates():
    """The avenue that took Bogotá from 27% to the low sixties, end to end.

    Server-side GROUP BY on a territorial layer is the one thing no CKAN city
    portal can do, so this asserts the rollup arrives computed rather than as
    rows to be summed.
    """
    from colombian_open_data_mcp import esri

    client = esri.EsriClient()
    try:
        ckan_client = ckan.CkanClient(ckan.BOGOTA)
        body = await ckan_client.package_search(query='res_format:"ESRI REST"', rows=10)
        layer = next(
            (
                r["url"]
                for d in body["results"]
                for r in d["resources"]
                if "ESRI" in (r.get("format") or "").upper()
                and esri.is_layer_url(r.get("url") or "")
            ),
            None,
        )
        await ckan_client.close()
        assert layer, "Bogotá's catalogue no longer publishes a queryable ESRI layer"

        info = await client.service_info(layer)
        formatted = esri.format_service_info(info, layer)
        assert formatted["fields"], "the layer reports no fields"

        rows = await client.query(layer, limit=3)
        assert esri.format_features(rows, layer)["rows_returned"] >= 1
    finally:
        await client.close()


async def test_live_bogota_publishes_files_this_server_can_read():
    """The third avenue, which took Bogotá from the low sixties to about 86%."""
    from colombian_open_data_mcp import tabular

    client = ckan.CkanClient(ckan.BOGOTA)
    try:
        body = await client.package_search(query='res_format:"CSV"', rows=5)
        resource = next(
            (
                r
                for d in body["results"]
                for r in d["resources"]
                if (r.get("format") or "").upper() == "CSV" and r.get("url")
            ),
            None,
        )
        assert resource, "Bogotá's catalogue no longer publishes a CSV"
        out = await tabular.read_resource_file(resource["url"], "CSV", rows=3)
        assert out["rows_returned"] >= 1
        assert out["columns"]
    finally:
        await client.close()


async def test_live_national_saved_views_are_queryable(_client):
    """2,197 saved views were invisible while `only` was hardcoded to dataset.

    They answer /resource/<4x4>.json exactly as a dataset does, which is the
    whole reason asset_type exists.
    """
    raw = await _client.catalog_search(asset_type="filter", limit=3)
    results = raw.get("results") or []
    assert results, "the portal publishes saved views; none came back"
    four = results[0]["resource"]["id"]
    assert socrata.is_valid_4x4(four)
    rows = await _client.resource_query(four, {"$limit": "1"})
    assert isinstance(rows, list)


async def test_live_national_asset_type_any_outnumbers_datasets(_client):
    """Sanity on the coverage claim: 'any' really does reach more than datasets."""
    only_ds = await _client.catalog_search(asset_type="dataset", limit=1)
    anything = await _client.catalog_search(asset_type="any", limit=1)
    assert (anything.get("resultSetSize") or 0) > (only_ds.get("resultSetSize") or 0)
