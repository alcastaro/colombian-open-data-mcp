"""Tests for the SSRF guard.

These are the tests that matter most in the whole suite, because they are the
only ones standing between a URL the server did not write and a socket. v0.3
had no outbound reach beyond five hardcoded hosts; v0.4 follows addresses that
come out of a portal catalogue, and a catalogue is data, not code.

Nothing here performs DNS. ``resolve_host`` is substituted in every case, which
keeps the suite hermetic and — more usefully — lets a test assert what happens
when a hostname resolves to 127.0.0.1, which no honest public name ever will.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from colombian_open_data_mcp import netguard
from colombian_open_data_mcp.netguard import NetGuardError

PUBLIC_V4 = ["190.0.0.1"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Each test starts from the shipped defaults, not from the developer's shell."""
    monkeypatch.delenv(netguard.MODE_ENV, raising=False)
    monkeypatch.delenv(netguard.HOSTS_ENV, raising=False)


def resolves_to(monkeypatch, addresses: list[str]) -> None:
    async def fake(host: str) -> list[str]:
        return addresses

    monkeypatch.setattr(netguard, "resolve_host", fake)


# ─── The addresses an SSRF attack actually wants ─────────────────────────────


@pytest.mark.parametrize(
    ("address", "what"),
    [
        ("127.0.0.1", "loopback"),
        ("127.1.2.3", "the rest of the loopback /8"),
        ("::1", "IPv6 loopback"),
        ("169.254.169.254", "the AWS/GCP/Azure instance metadata endpoint"),
        ("169.254.1.1", "link-local generally"),
        ("10.0.0.5", "RFC 1918 10/8"),
        ("172.16.4.4", "RFC 1918 172.16/12"),
        ("192.168.1.1", "RFC 1918 192.168/16"),
        ("100.64.0.1", "carrier-grade NAT"),
        ("fd00::1", "IPv6 unique-local"),
        ("0.0.0.0", "the unspecified address"),
    ],
)
async def test_non_public_addresses_are_refused(monkeypatch, address, what):
    """A hostname resolving to any of these is refused, whatever it is called.

    The check is on the resolved address, never on the name, because the name
    is chosen by whoever registered the resource.
    """
    resolves_to(monkeypatch, [address])
    with pytest.raises(NetGuardError, match="non-public address"):
        await netguard.assert_public_url("https://data.example.gov.co/file.csv")


async def test_one_bad_address_among_good_ones_still_refuses(monkeypatch):
    """DNS returning several answers is normal; all of them must be public.

    A name that resolves to both a real CDN address and to loopback is the
    textbook rebinding setup. Accepting it because the first answer looked fine
    would defeat the whole module.
    """
    resolves_to(monkeypatch, ["190.0.0.1", "2800:3f0::1", "127.0.0.1"])
    with pytest.raises(NetGuardError, match="127.0.0.1"):
        await netguard.assert_public_url("https://mixed.example.gov.co/x")


async def test_a_public_address_passes(monkeypatch):
    resolves_to(monkeypatch, PUBLIC_V4)
    await netguard.assert_public_url("https://datosabiertos.bogota.gov.co/x.csv")


async def test_decimal_encoded_metadata_ip_is_normalised(monkeypatch):
    """``http://2852039166/`` is 169.254.169.254 written as one decimal number.

    Everything is resolved through ``getaddrinfo``, including bare literals,
    precisely so the obfuscated spellings arrive at the check as an address
    rather than as a string nobody compared correctly.
    """

    async def fake(host: str) -> list[str]:
        assert host == "2852039166"
        return ["169.254.169.254"]

    monkeypatch.setattr(netguard, "resolve_host", fake)
    with pytest.raises(NetGuardError, match="169.254.169.254"):
        await netguard.assert_public_url("http://2852039166/latest/meta-data/")


# ─── Malformed and non-HTTP addresses ────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://files.example.gov.co/data.csv",
        "gopher://example.gov.co:70/",
        "data:text/plain;base64,aGk=",
        "/relative/path.csv",
    ],
)
async def test_non_http_schemes_are_refused(url):
    with pytest.raises(NetGuardError, match="scheme"):
        await netguard.assert_public_url(url)


async def test_url_without_a_hostname_is_refused():
    with pytest.raises(NetGuardError, match="no hostname"):
        await netguard.assert_public_url("https:///just/a/path")


async def test_dns_failure_is_refused_not_ignored(monkeypatch):
    """A name that does not resolve is refused rather than passed to httpx.

    The outcome is the same either way, but the message is not: the model is
    told the address is unreachable instead of receiving a transport traceback.
    """

    async def fake(host: str) -> list[str]:
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(netguard, "resolve_host", fake)
    with pytest.raises(NetGuardError, match="DNS resolution failed"):
        await netguard.assert_public_url("https://nope.example.gov.co/x")


async def test_resolution_returning_nothing_is_refused(monkeypatch):
    resolves_to(monkeypatch, [])
    with pytest.raises(NetGuardError, match="no addresses"):
        await netguard.assert_public_url("https://empty.example.gov.co/x")


def test_an_unparseable_address_is_refused():
    with pytest.raises(NetGuardError, match="unparseable"):
        netguard.check_address("not-an-ip", "weird.example.gov.co")


# ─── Modes ───────────────────────────────────────────────────────────────────


async def test_off_mode_skips_everything(monkeypatch):
    monkeypatch.setenv(netguard.MODE_ENV, "off")

    async def explode(host: str) -> list[str]:
        raise AssertionError("resolution must not happen when the guard is off")

    monkeypatch.setattr(netguard, "resolve_host", explode)
    await netguard.assert_public_url("http://127.0.0.1:8080/admin")


async def test_strict_mode_refuses_a_host_outside_the_allowlist(monkeypatch):
    monkeypatch.setenv(netguard.MODE_ENV, "strict")
    resolves_to(monkeypatch, PUBLIC_V4)
    with pytest.raises(NetGuardError, match="strict allowlist"):
        await netguard.assert_public_url("https://some-ministry.example.com/data.csv")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.datos.gov.co/resource/abcd-1234.json",
        "https://datosabiertos.bogota.gov.co/dataset/x",
        "https://datos.cali.gov.co/dataset/x",
        "https://datosabiertos.valledelcauca.gov.co/dataset/x",
        "https://datosabiertos.cartagena.gov.co/dataset/x",
        "https://mapas.bogota.gov.co/arcgis/rest/services/x/FeatureServer/0",
        "https://services9.arcgis.com/abc/arcgis/rest/services/y/FeatureServer/0",
    ],
)
async def test_strict_mode_allows_the_portals_this_server_serves(monkeypatch, url):
    monkeypatch.setenv(netguard.MODE_ENV, "strict")
    resolves_to(monkeypatch, PUBLIC_V4)
    await netguard.assert_public_url(url)


async def test_strict_mode_still_checks_the_address(monkeypatch):
    """Being on the allowlist is not a licence to resolve to loopback.

    The allowlist narrows *which names* are acceptable. It does not replace the
    address check, because a portal's own DNS could be wrong or hijacked.
    """
    monkeypatch.setenv(netguard.MODE_ENV, "strict")
    resolves_to(monkeypatch, ["127.0.0.1"])
    with pytest.raises(NetGuardError, match="non-public address"):
        await netguard.assert_public_url("https://datos.cali.gov.co/x")


def test_an_unknown_mode_raises_rather_than_falling_back(monkeypatch):
    """A typo must not silently downgrade a hosted deployment's policy."""
    monkeypatch.setenv(netguard.MODE_ENV, "strictt")
    with pytest.raises(NetGuardError, match="Invalid"):
        netguard.current_mode()


def test_the_default_mode_is_public_only(monkeypatch):
    assert netguard.current_mode() == "public-only"


def test_an_empty_env_var_means_the_default(monkeypatch):
    monkeypatch.setenv(netguard.MODE_ENV, "   ")
    assert netguard.current_mode() == "public-only"


# ─── Operator allowlist ──────────────────────────────────────────────────────


async def test_an_operator_host_bypasses_resolution(monkeypatch):
    monkeypatch.setenv(netguard.HOSTS_ENV, "localhost, fixtures.test")

    async def explode(host: str) -> list[str]:
        raise AssertionError("an operator-vouched host must not be resolved")

    monkeypatch.setattr(netguard, "resolve_host", explode)
    await netguard.assert_public_url("http://fixtures.test/data.csv")


async def test_a_host_outside_the_operator_list_is_still_checked(monkeypatch):
    monkeypatch.setenv(netguard.HOSTS_ENV, "fixtures.test")
    resolves_to(monkeypatch, ["10.1.1.1"])
    with pytest.raises(NetGuardError):
        await netguard.assert_public_url("http://other.test/data.csv")


@pytest.mark.parametrize(
    ("host", "pattern", "expected"),
    [
        ("datos.cali.gov.co", "datos.cali.gov.co", True),
        ("DATOS.CALI.GOV.CO", "datos.cali.gov.co", True),
        ("datos.cali.gov.co.", "datos.cali.gov.co", True),
        ("evil.com", "datos.cali.gov.co", False),
        ("mapas.bogota.gov.co", "*.bogota.gov.co", True),
        ("a.b.bogota.gov.co", "*.bogota.gov.co", True),
        # The apex is not matched by a wildcard, and neither is a name that
        # merely ends with the same letters — "notbogota.gov.co" must not slip
        # through a suffix comparison written without the dot.
        ("bogota.gov.co", "*.bogota.gov.co", False),
        ("notbogota.gov.co", "*.bogota.gov.co", False),
    ],
)
def test_host_matching(host, pattern, expected):
    assert netguard.host_matches(host, pattern) is expected


# ─── Installed as an httpx hook ──────────────────────────────────────────────


async def test_the_hook_refuses_a_redirect_to_a_private_address(monkeypatch, httpx_mock):
    """The reason the guard is a request hook rather than a single up-front check.

    A resource URL that passes validation and then answers ``302 Location:
    http://127.0.0.1/`` would otherwise be followed. With the hook installed,
    the second request is validated like the first and refused.
    """

    async def fake(host: str) -> list[str]:
        return ["127.0.0.1"] if host == "internal.test" else PUBLIC_V4

    monkeypatch.setattr(netguard, "resolve_host", fake)
    httpx_mock.add_response(
        url="https://datosabiertos.bogota.gov.co/redirect",
        status_code=302,
        headers={"Location": "http://internal.test/secret"},
    )

    async with httpx.AsyncClient(
        follow_redirects=True,
        event_hooks={"request": [netguard.guard_request_hook]},
    ) as client:
        with pytest.raises(NetGuardError, match="non-public address"):
            await client.get("https://datosabiertos.bogota.gov.co/redirect")


async def test_the_hook_lets_a_normal_request_through(monkeypatch, httpx_mock):
    resolves_to(monkeypatch, PUBLIC_V4)
    httpx_mock.add_response(url="https://datosabiertos.bogota.gov.co/ok.csv", text="a,b\n1,2\n")

    async with httpx.AsyncClient(
        event_hooks={"request": [netguard.guard_request_hook]},
    ) as client:
        r = await client.get("https://datosabiertos.bogota.gov.co/ok.csv")

    assert r.text.startswith("a,b")
