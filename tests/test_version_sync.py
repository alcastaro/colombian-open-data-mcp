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

    Under SDK v1 this was a real bug and an invisible one: FastMCP took no
    `version` argument, the low-level server defaulted to the installed SDK's
    version, and every client's handshake reported e.g. "1.27.1" as ours. The
    fix was to reach past the wrapper and assign `_mcp_server.version`.

    SDK v2 takes `version` in the constructor, so the reach-around is gone —
    but the assertion stays, because nothing inside the server ever showed the
    symptom. Only a client did.
    """
    from colombian_open_data_mcp.server import mcp

    opts = mcp._lowlevel_server.create_initialization_options()
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
    """The mcp dependency must always name a major it cannot cross.

    This is not hypothetical caution. SDK 2.0 renamed FastMCP to MCPServer and
    replaced `mcp.server.fastmcp` with a stub that raises ModuleNotFoundError —
    verified against the live index — and this server was pinned below 2 for
    exactly that reason until it migrated. A major bump can do the same again,
    and the lockfile protects this checkout, never someone installing the
    published wheel.
    """
    pyproject_text, _ = repo_files
    assert re.search(r'"mcp>=[\d.]+,<\d+"', pyproject_text), (
        "the mcp dependency must keep an upper bound on the major version"
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
        properties = set(tool.input_schema.get("properties") or {})
        assert not (properties & {"url", "uri", "address", "endpoint", "host"}), tool.name


def test_the_sdist_is_an_allowlist_not_a_denylist(repo_files):
    """A local tool directory once shipped inside the published package.

    `uv build` does not honour nested .gitignore files, so
    `.code-review-graph/graph.db` — an 852 KB SQLite database git had never
    tracked, carrying absolute paths under the maintainer's home directory —
    ended up in the source distribution, at 74% of its size. Nothing in the
    repository was wrong; the build simply swept up a working directory.

    A denylist cannot fix that, because the next tool will use a different
    directory name. Naming what goes in can.
    """
    pyproject, _ = repo_files
    assert "[tool.hatch.build.targets.sdist]" in pyproject, (
        "the sdist must declare an explicit include list; without one the build "
        "packages whatever happens to be in the working tree"
    )
    section = pyproject.split("[tool.hatch.build.targets.sdist]", 1)[1]
    include = section.split("\n[", 1)[0]
    assert "include = [" in include, "the sdist section must use include, not exclude"
    for required in ("/src", "/tests", "/README.md", "/LICENSE", "/server.json"):
        assert f'"{required}"' in include, f"the sdist would ship without {required}"


def test_the_package_description_names_every_portal(repo_files):
    """The one-line summary is what PyPI search shows and what a user reads
    first. A portal missing from it is a portal nobody discovers."""
    from colombian_open_data_mcp import ckan

    pyproject, _ = repo_files
    description = ""
    for line in pyproject.splitlines():
        if line.startswith("description ="):
            description = line
            break
    assert description, "pyproject has no description"
    for portal in ckan.PORTALS.values():
        # A portal's territory name is one to three words ("Bogotá D.C.",
        # "Santiago de Cali", "Valle del Cauca") and the description need only
        # carry the part a person would actually search for, so any
        # substantial word counts.
        words = [w.strip(",.") for w in portal.city.split() if len(w) > 3]
        assert any(w in description for w in words), (
            f"{portal.key} is missing from the package description"
        )


def test_the_github_workflows_parse():
    """A broken release workflow fails at the worst possible moment.

    CI failing is loud and cheap. A release workflow with a YAML error fails
    only after a tag is pushed, on a version number PyPI will never let us
    reuse — the package must then be republished under a new number for no
    reason but a typo.
    """
    import glob

    import yaml

    workflows = glob.glob(str(ROOT / ".github" / "workflows" / "*.yml"))
    if not workflows:
        pytest.skip("workflows not present (installed-package run)")
    for path in workflows:
        with open(path, encoding="utf-8") as fh:
            assert yaml.safe_load(fh) is not None, path


def test_the_release_workflow_holds_no_credential():
    """Publication must use PyPI Trusted Publishing, never a stored token.

    A long-lived API token is a credential that can leak, has to be rotated, and
    can be committed by accident. Trusted Publishing mints a short-lived one per
    run through OIDC, so there is nothing to leak. This test fails if anyone
    reintroduces a token, which is the easy thing to do when a publish breaks at
    an inconvenient moment.
    """
    workflow = ROOT / ".github" / "workflows" / "release.yml"
    if not workflow.exists():
        pytest.skip("release workflow not present (installed-package run)")
    text = workflow.read_text(encoding="utf-8")
    assert "id-token: write" in text, "Trusted Publishing needs the id-token permission"
    for forbidden in ("secrets.PYPI", "password:", "api-token", "TWINE_PASSWORD"):
        assert forbidden not in text, f"the release workflow must not carry {forbidden}"


def test_the_privacy_notes_name_every_host_the_server_can_reach():
    """A privacy policy that understates network reach is the fastest way to
    fail a directory review, and it drifts silently.

    Through 0.3 this server only contacted hosts written in its own source, and
    both privacy notes said exactly that. Version 0.4 added ESRI queries and
    tabular downloads, which follow addresses that come out of a portal's
    catalogue — so the claim stopped being true while the documents still made
    it. This test fails if a portal is added without the notes following, and
    if the retired "cannot be directed to a fourth host" claim ever returns.
    """
    from colombian_open_data_mcp import ckan

    notes = {
        name: (ROOT / "docs" / name).read_text(encoding="utf-8")
        for name in ("PRIVACY.md", "PRIVACIDAD.md")
    }
    for name, text in notes.items():
        for portal in ckan.PORTALS.values():
            assert portal.base_url.split("//")[-1].rstrip("/") in text, (
                f"docs/{name} does not name {portal.key}'s host"
            )
        assert "www.datos.gov.co" in text, f"docs/{name} omits the national portal"
        assert "netguard" in text, (
            f"docs/{name} does not mention the network policy that bounds "
            "which catalogue-supplied hosts are reachable"
        )
        for retired in ("fourth host", "cuarto host"):
            assert retired not in text, (
                f"docs/{name} still claims the reachable host set is closed; "
                "it has not been since 0.4"
            )
