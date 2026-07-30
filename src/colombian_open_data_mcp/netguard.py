"""SSRF guard for the outbound requests this server makes to catalogue-chosen URLs.

Until v0.4 this server only ever contacted hosts written in its own source:
``www.datos.gov.co`` and the four CKAN portals. That property was worth having
and was stated in ``SECURITY.md``. Two new capabilities break it, deliberately:

* **ESRI REST queries** live on whatever host the publisher registered —
  ``oaiee.scj.gov.co``, ``mapas.bogota.gov.co``, ``services*.arcgis.com``.
* **Tabular downloads** reach the file's own address. 88% of Bogotá's sit on
  ``*.bogota.gov.co``, but the remaining 12% do not, and refusing them would
  drop real datasets on the floor.

So the address now comes from data rather than from code, and something has to
stand between the catalogue and the socket. That is this module.

The policy is **not** a host allowlist by default. A resource legitimately
hosted on a ministry's own domain or an S3 bucket is normal, and an allowlist
would either be permanently incomplete or so broad it guaranteed nothing. The
default is *public internet only*: the scheme must be http or https, and every
address the hostname resolves to must be globally routable. That refuses the
targets an SSRF attack actually wants — cloud instance metadata at
``169.254.169.254``, loopback, the RFC-1918 private ranges, IPv6 unique-local —
while leaving legitimate external hosts reachable.

Modes, selected with the ``CO_MCP_NETGUARD`` environment variable:

``public-only`` (default)
    Scheme check, then resolve the hostname and require every returned address
    to be globally routable.
``strict``
    Additionally the hostname must match one of :data:`DEFAULT_STRICT_HOSTS`
    (the five Colombian portals plus the ArcGIS Online service domains) or an
    entry in ``CO_MCP_ALLOW_HOSTS``. This is the setting for a hosted
    deployment, where the operator would rather lose the 12% of external files
    than accept any outbound reach at all.
``off``
    No checks. For a trusted local machine, and for test suites that must not
    perform DNS.

``CO_MCP_ALLOW_HOSTS`` is a comma-separated list, each entry either a hostname
or a ``*.``-prefixed wildcard. Listed hosts skip resolution entirely, which is
what makes this module testable without a network and what lets a fork point at
a portal these defaults never heard of.

**Two limits worth stating plainly rather than discovering later.**

First, a residual DNS-rebinding window: this module resolves the hostname, and
then httpx resolves it again when it opens the connection. An attacker who
controls a domain's DNS and can flip the answer between those two lookups is
not blocked. Closing it means pinning the validated address at the transport,
which is a larger change; it is recorded here as a known limit rather than
implied away.

Second, validation is only as good as its placement. Installing this as an
httpx *request* event hook — see :func:`guard_request_hook` — is what makes
every redirect hop get checked too. A single check before the first request
would be trivially defeated by a 302 to ``127.0.0.1``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

#: Hosts allowed under ``strict`` mode without an explicit allowlist entry.
#: The five Colombian portals this server knows about, plus the two ArcGIS
#: Online service domains that host a large share of Bogotá's ESRI layers.
DEFAULT_STRICT_HOSTS: tuple[str, ...] = (
    "www.datos.gov.co",
    "datos.gov.co",
    "datosabiertos.bogota.gov.co",
    "datos.cali.gov.co",
    "datosabiertos.valledelcauca.gov.co",
    "datosabiertos.cartagena.gov.co",
    "*.bogota.gov.co",
    "*.cali.gov.co",
    "*.arcgis.com",
    # Bogotá's ESRI layers are not under bogota.gov.co at all: they live on
    # serviciosgis.catastrobogota.gov.co, portalgis.habitatbogota.gov.co and
    # several more, each its own second-level domain. A strict mode that
    # refused those would refuse the whole ESRI avenue, so the Colombian
    # government namespace is the boundary. It is broad, which is why it is not
    # the default — public-only is.
    "*.gov.co",
)

MODE_ENV = "CO_MCP_NETGUARD"
HOSTS_ENV = "CO_MCP_ALLOW_HOSTS"
VALID_MODES = ("public-only", "strict", "off")
DEFAULT_MODE = "public-only"


class NetGuardError(RuntimeError):
    """Raised when an outbound URL fails SSRF validation.

    Deliberately a distinct type. Tools catch it separately from transport
    errors so the model is told the address was refused on policy, which is a
    different situation from a host being down.
    """


def current_mode() -> str:
    """The active mode, validated.

    An unrecognised value raises rather than falling back to the default. A
    typo in ``CO_MCP_NETGUARD`` on a hosted deployment would otherwise silently
    downgrade ``strict`` to ``public-only``, which is exactly the kind of
    security setting that must fail loudly instead of quietly.
    """
    mode = os.environ.get(MODE_ENV, DEFAULT_MODE).strip().lower() or DEFAULT_MODE
    if mode not in VALID_MODES:
        raise NetGuardError(
            f"Invalid {MODE_ENV}={mode!r}; expected one of {', '.join(VALID_MODES)}"
        )
    return mode


def operator_hosts() -> tuple[str, ...]:
    """Hostnames the operator has vouched for, from ``CO_MCP_ALLOW_HOSTS``."""
    raw = os.environ.get(HOSTS_ENV, "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def host_matches(host: str, pattern: str) -> bool:
    """Whether ``host`` matches an allowlist ``pattern``.

    ``*.example.gov.co`` matches a subdomain but not the bare apex, which is
    the conventional reading and avoids an entry meaning more than it looks
    like it means.
    """
    host = (host or "").lower().rstrip(".")
    pattern = (pattern or "").lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".example.gov.co"
        return host.endswith(suffix) and host != suffix.lstrip(".")
    return host == pattern


async def resolve_host(host: str) -> list[str]:
    """Every A/AAAA address for ``host``.

    A separate function so tests can substitute it and never touch DNS.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def check_address(ip_str: str, host: str) -> None:
    """Raise unless ``ip_str`` is a globally routable address.

    ``ipaddress.is_global`` is False for the private ranges, loopback,
    link-local (which is what makes ``169.254.169.254`` — the cloud metadata
    endpoint on AWS, GCP and Azure — refused), carrier-grade NAT, IPv6
    unique-local and the reserved blocks. Using the stdlib's classification
    rather than a hand-written range list means new reserved blocks are covered
    by a Python upgrade instead of by remembering to edit this file.
    """
    try:
        ip = ipaddress.ip_address(ip_str.split("%")[0])  # drop any IPv6 zone id
    except ValueError as e:
        raise NetGuardError(f"Blocked URL: host '{host}' resolved to unparseable {ip_str!r}") from e
    if not ip.is_global:
        raise NetGuardError(
            f"Blocked URL: host '{host}' resolves to the non-public address {ip}. "
            "Private, loopback, link-local and reserved ranges are refused; the "
            "cloud metadata endpoint 169.254.169.254 is one of them."
        )


async def assert_public_url(url: str) -> None:
    """Raise :class:`NetGuardError` unless ``url`` is safe to fetch.

    This is the single entry point. Every outbound request to an address that
    came from a portal catalogue goes through it, either directly or via
    :func:`guard_request_hook`.
    """
    mode = current_mode()
    if mode == "off":
        return

    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        raise NetGuardError(
            f"Blocked URL: scheme {parts.scheme or '(none)'!r} is not allowed; "
            "only http and https are fetched."
        )
    host = parts.hostname
    if not host:
        raise NetGuardError(f"Blocked URL: no hostname in {url!r}")

    # An operator-vouched host bypasses everything below, including resolution.
    # This is the fork/test escape hatch and it is intentionally absolute.
    if any(host_matches(host, pat) for pat in operator_hosts()):
        return

    if mode == "strict" and not any(host_matches(host, pat) for pat in DEFAULT_STRICT_HOSTS):
        raise NetGuardError(
            f"Blocked URL: host '{host}' is not in the strict allowlist. "
            f"Set {HOSTS_ENV} to add it, or {MODE_ENV}=public-only to allow any "
            "publicly routable host."
        )

    # Resolve, then require every returned address to be public. Passing even a
    # bare IP literal through getaddrinfo is deliberate: it normalises the
    # obfuscated forms — decimal ("2852039166"), octal, and IPv4-mapped IPv6 —
    # into something ipaddress can classify, so those cannot slip past.
    try:
        addresses = await resolve_host(host)
    except socket.gaierror as e:
        raise NetGuardError(f"Blocked URL: DNS resolution failed for '{host}': {e}") from e
    if not addresses:
        raise NetGuardError(f"Blocked URL: '{host}' resolved to no addresses")
    for addr in addresses:
        check_address(addr, host)


async def guard_request_hook(request: httpx.Request) -> None:
    """httpx event hook validating the initial request and every redirect hop.

    Register with ``httpx.AsyncClient(event_hooks={"request": [guard_request_hook]})``.
    Installing it here rather than checking once before the call is what closes
    the redirect hole: a publisher's URL that answers ``302 Location:
    http://169.254.169.254/`` is refused on the hop, not waved through because
    the address originally looked fine.
    """
    await assert_public_url(str(request.url))
