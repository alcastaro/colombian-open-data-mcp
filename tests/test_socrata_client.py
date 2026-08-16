"""Hermetic tests for the Socrata client's transport layer. No network.

``test_socrata.py`` covers the pure formatters. This file covers everything
between them and the wire: parameter building, the app token, the 4x4 gate,
and the three ways a portal can hand back something unusable (a 4xx, a
timeout, a body that is not JSON).
"""

from __future__ import annotations

import re

import httpx
import pytest

from colombian_open_data_mcp import socrata

CATALOG = socrata.CATALOG_API_BASE
ANY_CATALOG = re.compile(r"https://api\.us\.socrata\.com/api/catalog/v1.*")
ANY_DOMAIN = re.compile(r"https://www\.datos\.gov\.co/api/.*")
ANY_RESOURCE = re.compile(r"https://www\.datos\.gov\.co/resource/.*")


@pytest.fixture
async def client():
    c = socrata.SocrataClient()
    yield c
    await c.close()


# ─── Headers and the app token ───────────────────────────────────────────────


async def test_user_agent_is_sent(client, httpx_mock):
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await client.catalog_search(query="x")
    assert httpx_mock.get_requests()[-1].headers["user-agent"] == socrata.USER_AGENT


async def test_no_app_token_header_when_unset(monkeypatch, httpx_mock):
    monkeypatch.delenv("SOCRATA_APP_TOKEN", raising=False)
    c = socrata.SocrataClient()
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await c.catalog_search(query="x")
    assert "x-app-token" not in httpx_mock.get_requests()[-1].headers
    await c.close()


async def test_app_token_read_from_env_at_construction(monkeypatch, httpx_mock):
    """Read on construction, not on each call, so a test can set the env
    without reaching into the module-level singleton."""
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "tok-123")
    c = socrata.SocrataClient()
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await c.catalog_search(query="x")
    assert httpx_mock.get_requests()[-1].headers["x-app-token"] == "tok-123"
    await c.close()


async def test_explicit_app_token_beats_env(monkeypatch, httpx_mock):
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "from-env")
    c = socrata.SocrataClient(app_token="explicit")
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await c.catalog_search(query="x")
    assert httpx_mock.get_requests()[-1].headers["x-app-token"] == "explicit"
    await c.close()


# ─── Parameter building ──────────────────────────────────────────────────────


async def test_catalog_search_scopes_to_the_colombian_domain(client, httpx_mock):
    """Without both `domains` and `search_context` the cross-portal catalog
    would happily return datasets from other countries."""
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await client.catalog_search(query="presupuesto")
    params = httpx_mock.get_requests()[-1].url.params
    assert params["domains"] == socrata.PORTAL_HOST
    assert params["search_context"] == socrata.PORTAL_HOST
    assert params["only"] == "dataset"
    assert params["q"] == "presupuesto"


async def test_catalog_search_clamps_limit(client, httpx_mock):
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await client.catalog_search(limit=99999)
    assert httpx_mock.get_requests()[-1].url.params["limit"] == "100"


async def test_catalog_search_floors_negative_offset(client, httpx_mock):
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await client.catalog_search(offset=-5)
    assert httpx_mock.get_requests()[-1].url.params["offset"] == "0"


async def test_none_parameters_are_dropped_not_sent_as_none(client, httpx_mock):
    """A literal "None" in a query string is a silent filter for nothing."""
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await client.catalog_search(query=None, categories=None, tags=None)
    params = httpx_mock.get_requests()[-1].url.params
    assert "q" not in params
    assert "categories" not in params
    assert "tags" not in params


async def test_catalog_recent_orders_by_updated_at(client, httpx_mock):
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []})
    await client.catalog_recent(limit=5)
    assert httpx_mock.get_requests()[-1].url.params["order"] == "updatedAt"


# ─── The 4x4 gate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["abcd", "ABCD-1234", "../../etc/passwd", "abcd-1234?x=1", ""])
async def test_get_view_rejects_bad_ids_before_any_request(client, bad):
    """No mock is registered: reaching the network would raise a different error."""
    with pytest.raises(socrata.SocrataError, match="4x4"):
        await client.get_view(bad)


@pytest.mark.parametrize("bad", ["abcd", "x", "abcd-1234/../.."])
async def test_resource_query_rejects_bad_ids(client, bad):
    with pytest.raises(socrata.SocrataError, match="4x4"):
        await client.resource_query(bad, {})


async def test_get_view_metadata_rejects_bad_ids(client):
    with pytest.raises(socrata.SocrataError, match="4x4"):
        await client.get_view_metadata("nope")


async def test_valid_4x4_reaches_the_views_endpoint(client, httpx_mock):
    httpx_mock.add_response(url=ANY_DOMAIN, json={"id": "abcd-1234", "columns": []})
    await client.get_view("abcd-1234")
    assert httpx_mock.get_requests()[-1].url.path == "/api/views/abcd-1234.json"


# ─── Failure modes ───────────────────────────────────────────────────────────


async def test_http_error_becomes_socrata_error_with_body_excerpt(client, httpx_mock):
    """503 is retried before it is reported; the excerpt survives the retries."""
    httpx_mock.add_response(
        url=ANY_CATALOG, status_code=503, text="upstream unavailable", is_reusable=True
    )
    with pytest.raises(socrata.SocrataError, match="HTTP 503"):
        await client.catalog_search(query="x")


async def test_timeout_becomes_socrata_error(client, httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("too slow"), url=ANY_CATALOG)
    with pytest.raises(socrata.SocrataError, match="timeout"):
        await client.catalog_search(query="x")


async def test_transport_error_becomes_socrata_error(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("dns"), url=ANY_CATALOG, is_reusable=True)
    with pytest.raises(socrata.SocrataError, match="network error"):
        await client.catalog_search(query="x")


async def test_non_json_body_becomes_socrata_error(client, httpx_mock):
    httpx_mock.add_response(url=ANY_CATALOG, text="<html>captive portal</html>")
    with pytest.raises(socrata.SocrataError, match="non-JSON"):
        await client.catalog_search(query="x")


async def test_resource_query_rejects_a_non_list_body(client, httpx_mock):
    """The data API returns a JSON array. An object here means an error page
    dressed as a 200, and treating it as rows would corrupt the answer."""
    httpx_mock.add_response(url=ANY_RESOURCE, json={"error": True, "message": "nope"})
    with pytest.raises(socrata.SocrataError, match="Expected list"):
        await client.resource_query("abcd-1234", {})


# ─── Domain endpoints ────────────────────────────────────────────────────────


async def test_domain_categories_unwraps_the_results_envelope(client, httpx_mock):
    httpx_mock.add_response(
        url=ANY_DOMAIN,
        json={"results": [{"domain_category": "Salud"}, {"domain_category": "Educación"}]},
    )
    assert await client.domain_categories() == ["Salud", "Educación"]


async def test_domain_tags_unwraps_the_results_envelope(client, httpx_mock):
    httpx_mock.add_response(url=ANY_DOMAIN, json={"results": [{"domain_tag": "presupuesto"}]})
    assert await client.domain_tags() == ["presupuesto"]


async def test_domain_owners_returns_name_and_count(client, httpx_mock):
    httpx_mock.add_response(url=ANY_DOMAIN, json={"results": [{"owner": "MinSalud", "count": 42}]})
    assert await client.domain_owners() == [{"owner": "MinSalud", "count": 42}]


async def test_unexpected_envelope_shape_yields_empty_not_a_crash(client, httpx_mock):
    httpx_mock.add_response(url=ANY_DOMAIN, json=["not", "an", "envelope"])
    assert await client.domain_categories() == []


# ─── Autocomplete ────────────────────────────────────────────────────────────


async def test_autocomplete_datasets_returns_titles(client, httpx_mock):
    httpx_mock.add_response(
        url=ANY_CATALOG, json={"results": [{"title": "Presupuesto 2024"}, {"title": None}]}
    )
    out = await client.autocomplete("dataset", "presu")
    assert out == ["Presupuesto 2024"]


async def test_autocomplete_tags_filters_client_side(client, httpx_mock):
    httpx_mock.add_response(
        url=ANY_DOMAIN,
        json={"results": [{"domain_tag": "presupuesto"}, {"domain_tag": "salud"}]},
    )
    assert await client.autocomplete("tag", "presu") == ["presupuesto"]


async def test_autocomplete_categories_filters_client_side(client, httpx_mock):
    httpx_mock.add_response(
        url=ANY_DOMAIN,
        json={"results": [{"domain_category": "Salud"}, {"domain_category": "Arte"}]},
    )
    assert await client.autocomplete("category", "sal") == ["Salud"]


async def test_autocomplete_owners_filters_client_side(client, httpx_mock):
    httpx_mock.add_response(
        url=ANY_DOMAIN,
        json={"results": [{"owner": "MinSalud", "count": 1}, {"owner": "MinTIC", "count": 2}]},
    )
    assert await client.autocomplete("owner", "minsal") == ["MinSalud"]


async def test_autocomplete_rejects_unknown_kind(client):
    with pytest.raises(ValueError, match="dataset, tag, category, owner"):
        await client.autocomplete("planeta", "x")


# ─── Site stats ──────────────────────────────────────────────────────────────


async def test_site_stats_counts_only_datasets(client, httpx_mock):
    """Regression: it used to count every asset type and saturate at 10,000.

    An unfiltered catalogue query against this domain returns exactly 10,000
    while the per-type counts sum to 12,251 — so 10,000 was a ceiling reported
    as a total. The real dataset count is 8,391, which is also the only figure
    `search_datasets` can actually reach.
    """
    httpx_mock.add_response(url=ANY_CATALOG, json={"resultSetSize": 8391})
    httpx_mock.add_response(
        url=ANY_DOMAIN, json={"results": [{"domain_category": "Salud"}]}, is_reusable=True
    )
    stats = await client.site_stats()
    assert stats["total_datasets"] == 8391
    only = httpx_mock.get_requests()[0].url.params.get("only")
    assert only == "dataset", "site_stats must scope to datasets, not all assets"


async def test_site_stats_composes_three_calls(client, httpx_mock):
    httpx_mock.add_response(url=ANY_CATALOG, json={"resultSetSize": 8391})
    httpx_mock.add_response(
        url=ANY_DOMAIN, json={"results": [{"domain_category": "Salud"}]}, is_reusable=True
    )
    stats = await client.site_stats()
    assert stats["country"] == "CO"
    assert stats["platform"] == "socrata"
    assert stats["total_datasets"] == 8391


# ─── Connection lifecycle ────────────────────────────────────────────────────


async def test_client_is_reused_between_calls(client, httpx_mock):
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []}, is_reusable=True)
    await client.catalog_search(query="a")
    first = client._client
    await client.catalog_search(query="b")
    assert client._client is first


async def test_close_is_idempotent(client):
    await client.close()
    await client.close()
    assert client._client is None


async def test_client_is_recreated_after_close(client, httpx_mock):
    httpx_mock.add_response(url=ANY_CATALOG, json={"results": []}, is_reusable=True)
    await client.catalog_search(query="a")
    await client.close()
    await client.catalog_search(query="b")
    assert client._client is not None


class TestRequestTimeout:
    """Twenty seconds is right for the catalogue and wrong for the few datasets
    that are genuinely large. A ``median()`` over SECOP II's six million
    contracts answers in roughly twenty-one seconds, which the fixed default
    turned into a permanent failure rather than a slow success."""

    def test_the_default_applies_when_the_variable_is_unset(self, monkeypatch):
        monkeypatch.delenv(socrata.TIMEOUT_ENV, raising=False)
        assert socrata.request_timeout() == socrata.DEFAULT_TIMEOUT

    def test_an_operator_can_raise_it(self, monkeypatch):
        monkeypatch.setenv(socrata.TIMEOUT_ENV, "90")
        assert socrata.request_timeout() == 90.0

    @pytest.mark.parametrize("bad", ["0", "-5", "abc", "", "999999"])
    def test_an_unusable_value_falls_back_rather_than_raising(self, monkeypatch, bad):
        """A bad environment variable must not stop the server from starting."""
        monkeypatch.setenv(socrata.TIMEOUT_ENV, bad)
        assert socrata.request_timeout() == socrata.DEFAULT_TIMEOUT

    def test_the_ceiling_is_enforced(self, monkeypatch):
        """Unbounded would let one call hang a stdio session with no way for
        the client to tell why."""
        monkeypatch.setenv(socrata.TIMEOUT_ENV, str(socrata.MAX_TIMEOUT + 1))
        assert socrata.request_timeout() == socrata.DEFAULT_TIMEOUT
        monkeypatch.setenv(socrata.TIMEOUT_ENV, str(socrata.MAX_TIMEOUT))
        assert socrata.request_timeout() == socrata.MAX_TIMEOUT

    def test_the_timeout_message_names_the_variable(self):
        """A timeout that does not say what to do about it is a dead end."""
        assert socrata.TIMEOUT_ENV == "CO_MCP_TIMEOUT"


async def test_the_socrata_client_installs_the_netguard_hook(client):
    """Same promise as for the CKAN client: docs/PRIVACY.md says the guard
    runs on every redirect hop of every request this server makes."""
    from colombian_open_data_mcp.netguard import guard_request_hook

    http = await client._get_client()
    assert guard_request_hook in http.event_hooks["request"]
