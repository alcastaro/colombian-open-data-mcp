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


# ─── Bogotá (CKAN) ───────────────────────────────────────────────────────────
#
# These stay out of CI on purpose. The portal sits behind a WAF that already
# rejected one probe during development, and a shared GitHub runner IP is far
# more likely to trip it than a laptop. A red build caused by someone else's
# rate limiter teaches nothing.


@pytest.fixture
async def bogota():
    c = ckan.CkanClient(ckan.BOGOTA)
    yield c
    await c.close()


async def test_live_bogota_catalog_size(bogota):
    stats = await bogota.site_stats()
    assert stats["platform"] == "ckan"
    assert stats["city"] == "Bogotá D.C."
    assert (stats["total_datasets"] or 0) > 1000
    assert stats["datastore_sql_available"] is False


async def test_live_bogota_search_returns_datasets(bogota):
    body = await bogota.package_search(query="movilidad", rows=3)
    out = ckan.format_search_response(body, bogota.portal)
    assert out["total"] > 0
    assert len(out["datasets"]) > 0
    for d in out["datasets"]:
        assert d["url"].startswith("https://datosabiertos.bogota.gov.co/dataset/")
        assert isinstance(d["resources"], list)


async def test_live_bogota_organizations_and_groups(bogota):
    orgs = await bogota.organization_list(limit=5)
    groups = await bogota.group_list(limit=5)
    assert len(orgs) > 0 and len(groups) > 0
    assert ckan.format_organization(orgs[0], bogota.portal)["name"]
    assert ckan.format_group(groups[0], bogota.portal)["name"]


async def test_live_bogota_datastore_returns_typed_rows(bogota):
    """Find a DataStore-backed resource by walking the catalog, then read it.

    Written as a walk rather than a hardcoded resource id because resource ids
    on this portal do change between republications, and a test that dies for
    that reason teaches the reader nothing.
    """
    rid = None
    for start in (0, 200, 600):
        body = await bogota.package_search(rows=25, start=start)
        for pkg in body.get("results", []):
            for res in pkg.get("resources", []):
                if res.get("datastore_active") and ckan.is_valid_uuid(res.get("id", "")):
                    rid = res["id"]
                    break
            if rid:
                break
        if rid:
            break
    assert rid, "no DataStore-backed resource found in the sampled catalog"

    result = await bogota.datastore_search(rid, limit=3)
    out = ckan.format_datastore_result(result, rid, bogota.portal)
    assert out["rows_returned"] >= 1
    assert len(out["fields"]) > 0


async def test_live_bogota_has_no_sql_endpoint(bogota):
    """The absence this whole design rests on — asserted, not assumed.

    If Bogotá ever enables datastore_search_sql this test fails, which is the
    signal to add a bogota aggregation tool.
    """
    with pytest.raises(ckan.CkanError):
        await bogota.action("datastore_search_sql", {"sql": "SELECT 1"})
