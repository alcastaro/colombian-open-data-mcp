"""Version-drift guard.

The version of this package lives in five places: ``pyproject.toml``,
``server.json`` (twice — the server and its package entry), ``__version__``,
the ``USER_AGENT`` string, and the ``serverInfo.version`` the MCP handshake
reports. Nothing was checking that they agreed, and by v0.1.0 three of them
had already drifted: the User-Agent said "0.1", and the handshake reported the
installed mcp SDK's version instead of ours.

These tests run in CI, so a bump that misses a file fails the build instead of
shipping skewed metadata to a registry.

Ported from the Dominican MCP, where the same drift happened first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from colombian_open_data_mcp import USER_AGENT, __version__

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def repo_files():
    pyproject = ROOT / "pyproject.toml"
    server_json = ROOT / "server.json"
    if not pyproject.exists() or not server_json.exists():
        pytest.skip("repo metadata files not present (installed-package run)")
    return (
        pyproject.read_text(encoding="utf-8"),
        json.loads(server_json.read_text(encoding="utf-8")),
    )


def test_pyproject_version_matches_package(repo_files):
    pyproject_text, _ = repo_files
    m = re.search(r'^version = "([^"]+)"', pyproject_text, re.MULTILINE)
    assert m, "no version field in pyproject.toml"
    assert m.group(1) == __version__


def test_server_json_versions_match_package(repo_files):
    _, server_json = repo_files
    assert server_json["version"] == __version__
    for pkg in server_json.get("packages", []):
        assert pkg["version"] == __version__


def test_user_agent_carries_package_version():
    """The portal sees this string. A stale one misattributes our traffic."""
    assert __version__ in USER_AGENT


def test_both_clients_send_the_same_user_agent():
    from colombian_open_data_mcp import ckan, socrata

    assert socrata.USER_AGENT is USER_AGENT
    assert ckan.USER_AGENT is USER_AGENT


def test_serverinfo_reports_package_version_not_sdk_version():
    """serverInfo.version must be the package version.

    FastMCP takes no `version` argument, so the low-level server defaults to
    the installed mcp SDK version and clients saw e.g. "1.27.1" as ours.
    """
    from colombian_open_data_mcp.server import mcp

    opts = mcp._mcp_server.create_initialization_options()
    assert opts.server_version == __version__


def test_console_script_matches_distribution_name(repo_files):
    """`uvx colombian-open-data-mcp` only resolves if a console script has
    exactly the distribution's name."""
    pyproject_text, _ = repo_files
    scripts = pyproject_text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    assert re.search(
        r'^colombian-open-data-mcp = "colombian_open_data_mcp\.server:main"$',
        scripts,
        re.MULTILINE,
    )


def test_mcp_dependency_has_upper_bound(repo_files):
    """mcp 2.x removed `mcp.server.fastmcp`; an unbounded pin breaks installs.

    Verified against the live index: `pip install mcp==2.1.1` then importing
    `mcp.server.fastmcp` raises ModuleNotFoundError. The lockfile protects this
    checkout, not anyone installing the published wheel.
    """
    pyproject_text, _ = repo_files
    assert re.search(r'"mcp>=[\d.]+,<2"', pyproject_text), (
        "the mcp dependency must keep an upper bound until the SDK v2 migration lands"
    )


def test_security_policy_supports_the_shipping_minor():
    """SECURITY.md's table is a claim about which code gets security fixes.

    A version bump that leaves it behind tells a reader that the release they
    are running is unsupported.
    """
    policy = ROOT / "SECURITY.md"
    if not policy.exists():
        pytest.skip("SECURITY.md not present (installed-package run)")
    text = policy.read_text(encoding="utf-8")
    minor = ".".join(__version__.split(".")[:2])
    assert f"| {minor}.x" in text, f"SECURITY.md does not list {minor}.x as supported"
    assert f"| < {minor}" in text, f"SECURITY.md does not mark < {minor} unsupported"


def test_changelog_has_an_entry_for_the_shipping_version():
    changelog = ROOT / "CHANGELOG.md"
    if not changelog.exists():
        pytest.skip("CHANGELOG.md not present (installed-package run)")
    assert f"[{__version__}]" in changelog.read_text(encoding="utf-8")


def test_the_city_enum_and_the_portal_registry_agree():
    """`ckan.CityKey` has to be a literal expression, so it can drift.

    A type checker cannot read a Literal built from ``tuple(PORTALS)``, so the
    accepted city values are spelled out by hand next to the registry. That
    means adding a portal in one place and not the other is possible, and the
    consequences are silent: a value in the tool schema with no portal behind it
    is a call that can only fail, and a portal missing from the schema is one no
    model can reach. This is the test that makes the drift loud.
    """
    from typing import get_args

    from colombian_open_data_mcp import ckan

    assert sorted(get_args(ckan.CityKey)) == sorted(ckan.PORTALS)


def test_no_tool_accepts_a_url():
    """The load-bearing property behind the whole v0.4 outbound-request design.

    ESRI queries and file downloads reach hosts this source does not name. That
    is only defensible because the address comes from a portal catalogue rather
    than from an argument. A `url` parameter anywhere would quietly turn this
    server into a general-purpose fetcher, so its absence is asserted rather
    than assumed.
    """
    import asyncio

    from colombian_open_data_mcp.server import mcp

    for tool in asyncio.run(mcp.list_tools()):
        properties = set(tool.inputSchema.get("properties") or {})
        assert not (properties & {"url", "uri", "address", "endpoint", "host"}), tool.name
