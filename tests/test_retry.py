"""Tests for the retry policy — including what it deliberately does not retry.

The negative cases matter more than the positive ones here. Retrying the wrong
failure is worse than not retrying at all: it triples the caller's wait before
delivering the same answer, and it triples the load on a public portal that
already told us no.
"""

from __future__ import annotations

import re

import httpx
import pytest

from colombian_open_data_mcp import retry, socrata

URL = "https://example.test/x"
ANY = re.compile(r"https://example\.test/.*")


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


# ─── What is retried ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("dns"),
        httpx.ConnectTimeout("handshake"),
        httpx.ReadError("reset"),
        httpx.WriteError("broken pipe"),
        # The one actually observed in a stress run over ~600 datasets:
        # "Server disconnected without sending a response."
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.PoolTimeout("no connection"),
    ],
)
async def test_transient_transport_errors_are_retried(client, httpx_mock, exc):
    httpx_mock.add_exception(exc, url=ANY, is_reusable=True)
    with pytest.raises(type(exc)):
        await retry.get_with_retries(client, URL)
    assert len(httpx_mock.get_requests()) == retry.DEFAULT_ATTEMPTS


async def test_a_retry_that_succeeds_returns_the_good_response(client, httpx_mock):
    """The whole point: one dropped connection should not reach the model."""
    httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"), url=ANY)
    httpx_mock.add_response(url=ANY, json={"ok": True})

    response = await retry.get_with_retries(client, URL)

    assert response.json() == {"ok": True}
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.parametrize("status", [502, 503, 504])
async def test_gateway_statuses_are_retried(client, httpx_mock, status):
    """A gateway saying 'not now' is not the origin saying 'no'."""
    httpx_mock.add_response(url=ANY, status_code=status, is_reusable=True)
    response = await retry.get_with_retries(client, URL)
    assert response.status_code == status
    assert len(httpx_mock.get_requests()) == retry.DEFAULT_ATTEMPTS


async def test_gateway_status_that_recovers_returns_the_good_response(client, httpx_mock):
    httpx_mock.add_response(url=ANY, status_code=503)
    httpx_mock.add_response(url=ANY, json={"ok": True})
    response = await retry.get_with_retries(client, URL)
    assert response.status_code == 200


# ─── What is deliberately not retried ────────────────────────────────────────


async def test_read_timeout_is_not_retried(client, httpx_mock):
    """The server accepted the request and is still working.

    On Socrata this is what count(*) over three million rows looks like, and
    the observed cases used the full 20-second budget. Retrying turns a
    20-second wait into 60 and then fails anyway.
    """
    httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=ANY, is_reusable=True)
    with pytest.raises(httpx.ReadTimeout):
        await retry.get_with_retries(client, URL)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500])
async def test_definitive_statuses_are_returned_untouched(client, httpx_mock, status):
    """A 404 on a resource the catalogue wrongly flagged will be a 404 every
    time. Retrying an answer the server was sure about wastes everyone's time.
    """
    httpx_mock.add_response(url=ANY, status_code=status, is_reusable=True)
    response = await retry.get_with_retries(client, URL)
    assert response.status_code == status
    assert len(httpx_mock.get_requests()) == 1


async def test_success_makes_exactly_one_request(client, httpx_mock):
    httpx_mock.add_response(url=ANY, json={"ok": True})
    await retry.get_with_retries(client, URL)
    assert len(httpx_mock.get_requests()) == 1


# ─── Policy values ───────────────────────────────────────────────────────────


def test_backoff_is_real_and_bounded():
    """conftest zeroes BASE_DELAY for speed, so the shipped values are asserted
    here against the module rather than observed through a sleeping test."""
    import inspect

    source = inspect.getsource(retry)
    assert "BASE_DELAY = 0.4" in source, "the shipped backoff must not be zero"
    assert retry.DEFAULT_ATTEMPTS == 3, "three attempts is the agreed ceiling"


async def test_attempts_is_configurable(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("dns"), url=ANY, is_reusable=True)
    with pytest.raises(httpx.ConnectError):
        await retry.get_with_retries(client, URL, attempts=2)
    assert len(httpx_mock.get_requests()) == 2


async def test_params_are_forwarded(client, httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*"), json={})
    await retry.get_with_retries(client, URL, {"q": "presupuesto"})
    assert httpx_mock.get_requests()[-1].url.params["q"] == "presupuesto"


# ─── Wired into the real clients ─────────────────────────────────────────────


async def test_socrata_client_recovers_from_a_dropped_connection(httpx_mock):
    """End to end: the failure the stress run saw no longer reaches the caller."""
    httpx_mock.add_exception(
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        url=re.compile(r".*socrata.*"),
    )
    httpx_mock.add_response(
        url=re.compile(r".*socrata.*"), json={"results": [], "resultSetSize": 0}
    )

    c = socrata.SocrataClient()
    body = await c.catalog_search(query="presupuesto")
    await c.close()

    assert body["resultSetSize"] == 0
    assert len(httpx_mock.get_requests()) == 2


async def test_ckan_client_recovers_from_a_dropped_connection(httpx_mock):
    from colombian_open_data_mcp import ckan

    pattern = re.compile(r".*datosabiertos\.bogota\.gov\.co.*")
    httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"), url=pattern)
    httpx_mock.add_response(url=pattern, json={"success": True, "result": ["salud"]})

    c = ckan.CkanClient(ckan.BOGOTA)
    result = await c.action("tag_list")
    await c.close()

    assert result == ["salud"]
    assert len(httpx_mock.get_requests()) == 2
