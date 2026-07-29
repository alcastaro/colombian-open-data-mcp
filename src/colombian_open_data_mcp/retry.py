"""Retries for the failures that are the network's fault, and only those.

A stress run over ~600 random datasets produced three failures reading
``Server disconnected without sending a response``. Each one reached the model
as a failed tool call, and each would have succeeded on a second attempt. That
is a bad trade: the caller pays for a connection that was dropped mid-flight,
on a portal that was otherwise healthy.

What is retried, and what deliberately is not
--------------------------------------------
**Retried** — the transport gave up before the server answered, so there is no
answer to respect: connect errors, read/write errors, a connection dropped
mid-response (``RemoteProtocolError``), and pool timeouts. Also 502/503/504,
which are a gateway saying "not now" rather than "no".

**Not retried** — a ``ReadTimeout``. The server accepted the request and is
still working on it. On Socrata this is what a ``count(*)`` over three million
rows looks like, and the observed cases took the full 20 seconds. Retrying
turns a 20-second wait into a 60-second one and then fails anyway, which is
worse for the caller than failing fast with a message that says so.

**Not retried** — any other 4xx. A 404 on a resource the catalogue wrongly
flagged as queryable will be a 404 on every attempt. Retrying an answer the
server was sure about only wastes the caller's time and the portal's.

Both portals are public infrastructure, so the backoff is real (0.4s, 0.8s) and
attempts are capped at three. This is not a load generator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 3
BASE_DELAY = 0.4

# The transport never got a complete answer, so there is nothing to honour.
_TRANSIENT_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)

# A gateway declining for now, as opposed to the origin declining outright.
_TRANSIENT_STATUS = {502, 503, 504}


async def get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
) -> httpx.Response:
    """GET ``url``, retrying only the failures a second attempt could fix.

    Returns the response — including a 4xx, which is the caller's to interpret.
    Raises the last transport exception if every attempt failed.
    """
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(url, params=params or {})
        except _TRANSIENT_EXCEPTIONS as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = BASE_DELAY * (2 ** (attempt - 1))
            logger.debug(
                "transient %s on %s (attempt %d/%d), retrying in %.1fs",
                type(exc).__name__,
                url,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
            continue
        except httpx.HTTPError:
            # ReadTimeout and anything else: the server engaged with us, so the
            # outcome stands. See the module docstring.
            raise

        if response.status_code in _TRANSIENT_STATUS and attempt < attempts:
            delay = BASE_DELAY * (2 ** (attempt - 1))
            logger.debug(
                "HTTP %d from %s (attempt %d/%d), retrying in %.1fs",
                response.status_code,
                url,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        return response

    assert last_exc is not None  # only reachable after a transient failure
    raise last_exc
