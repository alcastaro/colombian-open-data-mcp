"""Unit tests for socrata module — formatters + helpers (no network)."""

from __future__ import annotations

import pytest

from colombian_open_data_mcp import socrata

# ─── 4x4 validation ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("abcd-1234", True),
        ("a1b2-c3d4", True),
        ("ABCD-1234", False),  # Socrata uses lowercase
        ("abcd1234", False),  # No dash
        ("abc-1234", False),  # Wrong length
        ("abcd-12345", False),  # Wrong length
        ("", False),
        (None, False),
    ],
)
def test_is_valid_4x4(value, expected):
    assert socrata.is_valid_4x4(value) == expected


# ─── _truncate ────────────────────────────────────────────────────────────────


def test_truncate_short_unchanged():
    assert socrata._truncate("hola", 10) == "hola"


def test_truncate_long_ellipsis():
    out = socrata._truncate("x" * 500, 50)
    assert out and out.endswith("…")
    assert len(out) <= 51


def test_truncate_none():
    assert socrata._truncate(None, 10) is None


# ─── Catalog response formatter ───────────────────────────────────────────────


def test_format_catalog_dataset_minimum():
    raw = {
        "resource": {
            "id": "abcd-1234",
            "name": "Presupuesto",
            "description": "x" * 500,
            "type": "dataset",
            "columns_field_name": ["departamento", "valor"],
            "columns_datatype": ["text", "number"],
            "columns_description": ["Nombre", "Pesos"],
            "createdAt": "2024-01-01T00:00:00Z",
        },
        "classification": {
            "domain_category": "Economía",
            "domain_tags": ["finanzas"],
        },
        "metadata": {"license": "CC BY 4.0"},
        "owner": {"display_name": "Min. Hacienda"},
        "permalink": "https://www.datos.gov.co/d/abcd-1234",
    }
    d = socrata.format_catalog_dataset(raw)
    assert d["id"] == "abcd-1234"
    assert d["url"] == "https://www.datos.gov.co/d/abcd-1234"
    assert d["data_api_endpoint"] == "https://www.datos.gov.co/resource/abcd-1234.json"
    assert d["category"] == "Economía"
    assert d["tags"] == ["finanzas"]
    assert d["owner"] == "Min. Hacienda"
    assert len(d["description"]) <= 301
    cols = d["columns"]
    assert len(cols) == 2
    assert cols[0]["field_name"] == "departamento"
    assert cols[0]["type"] == "text"
    assert cols[1]["field_name"] == "valor"


def test_format_catalog_response_shape():
    raw = {
        "resultSetSize": 12345,
        "results": [
            {
                "resource": {"id": "abcd-1234", "name": "x"},
                "classification": {},
                "metadata": {},
                "owner": {},
            }
        ],
    }
    r = socrata.format_catalog_response(raw)
    assert r["country"] == "CO"
    assert r["portal"] == "datos.gov.co"
    assert r["total"] == 12345
    assert r["returned"] == 1
    assert r["datasets"][0]["id"] == "abcd-1234"


# ─── View formatter ───────────────────────────────────────────────────────────


def test_format_view_compact():
    raw = {
        "id": "abcd-1234",
        "name": "Test",
        "description": "y" * 500,
        "category": "Salud",
        "tags": ["tag1"],
        "owner": {"displayName": "MinSalud"},
        "createdAt": "2024",
        "rowsUpdatedAt": "2025",
        "rowsCount": 1000,
        "license": {"name": "CC BY", "termsLink": "https://cc.org"},
        "columns": [
            {
                "fieldName": "depto",
                "name": "Departamento",
                "dataTypeName": "text",
                "description": "DANE code",
                "position": 0,
            }
        ],
    }
    v = socrata.format_view(raw)
    assert v["country"] == "CO"
    assert v["id"] == "abcd-1234"
    assert v["owner"] == "MinSalud"
    assert v["row_count"] == 1000
    assert v["license"] == "CC BY"
    assert v["url"] == "https://www.datos.gov.co/d/abcd-1234"
    assert v["columns"][0]["field_name"] == "depto"
    assert len(v["description"]) <= 301


class TestRowCount:
    """``get_dataset`` used to report ``row_count: None`` for every dataset on
    the portal, because ``format_view`` read a top-level ``rowsCount`` that
    datos.gov.co never sends. The size of a table is what decides between
    previewing it and aggregating it server-side, so a model that cannot see it
    is choosing blind."""

    def test_the_count_comes_from_the_column_profile(self):
        view = {
            "id": "abcd-1234",
            "columns": [
                {"fieldName": "a", "cachedContents": {"count": "19160"}},
                {"fieldName": "b", "cachedContents": {"count": "19160"}},
            ],
        }
        assert socrata.format_view(view)["row_count"] == 19160

    def test_columns_without_a_profile_do_not_lower_the_count(self):
        view = {
            "id": "abcd-1234",
            "columns": [
                {"fieldName": "a"},
                {"fieldName": "b", "cachedContents": {"count": "462"}},
            ],
        }
        assert socrata.format_view(view)["row_count"] == 462

    def test_a_top_level_count_is_the_fallback(self):
        view = {"id": "abcd-1234", "rowsCount": 7, "columns": [{"fieldName": "a"}]}
        assert socrata.format_view(view)["row_count"] == 7

    def test_no_profile_anywhere_reports_none_rather_than_zero(self):
        """Zero would be a claim about the data; None says the portal didn't say."""
        view = {"id": "abcd-1234", "columns": [{"fieldName": "a"}]}
        assert socrata.format_view(view)["row_count"] is None

    def test_a_non_numeric_count_is_ignored(self):
        view = {
            "id": "abcd-1234",
            "columns": [{"fieldName": "a", "cachedContents": {"count": "n/a"}}],
        }
        assert socrata.format_view(view)["row_count"] is None
