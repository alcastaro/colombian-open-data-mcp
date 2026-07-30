"""Shared pytest fixtures."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    """Collapse the retry backoff for every test.

    The real delays (0.4s then 0.8s) are deliberate politeness toward two
    public portals. Paying them in a hermetic suite would add over a second to
    each failure-path test for no signal, and a slow suite is a suite people
    stop running. tests/test_retry.py asserts the real values separately.
    """
    from colombian_open_data_mcp import retry

    monkeypatch.setattr(retry, "BASE_DELAY", 0.0)


@pytest.fixture(autouse=True)
def _netguard_off_by_default(monkeypatch):
    """Disable the SSRF guard for the hermetic suite.

    It resolves DNS, and a hermetic test must never touch the network — a
    fixture host like ``fixtures.test`` does not resolve, so every mocked
    request would be refused before pytest-httpx ever saw it. Turning the guard
    off here is not weakening a check: tests/test_netguard.py exercises the
    guard directly, with resolution substituted, and its own fixture clears this
    variable so it tests the shipped default rather than this override.
    """
    from colombian_open_data_mcp import netguard

    monkeypatch.setenv(netguard.MODE_ENV, "off")


def pytest_collection_modifyitems(config, items):
    """Auto-skip live tests unless RUN_LIVE_TESTS=1."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skipper = pytest.mark.skip(reason="set RUN_LIVE_TESTS=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skipper)
