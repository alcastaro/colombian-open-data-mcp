"""Opt-in live tests against the real datos.gov.co Socrata API.

Skipped by default. Enable with: RUN_LIVE_TESTS=1 pytest tests/test_live.py
"""

from __future__ import annotations

import pytest

from colombian_open_data_mcp import socrata

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
