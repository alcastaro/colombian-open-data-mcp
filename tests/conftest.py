"""Shared pytest fixtures."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-skip live tests unless RUN_LIVE_TESTS=1."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skipper = pytest.mark.skip(reason="set RUN_LIVE_TESTS=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skipper)
