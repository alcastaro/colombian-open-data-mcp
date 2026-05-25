"""Tests for SoQL builder + validator (no network)."""

from __future__ import annotations

import pytest

from colombian_open_data_mcp import soql


# ─── quote_ident ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("departamento", "`departamento`"),
        ("año", "`año`"),
        ("sueldo bruto", "`sueldo bruto`"),
        ("col_with_underscore", "`col_with_underscore`"),
    ],
)
def test_quote_ident_valid(name, expected):
    assert soql.quote_ident(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "has'quote",
        'has"doublequote',
        "has;semicolon",
        "has--comment",
        "has/*comment*/",
        "has\nnewline",
        "",
    ],
)
def test_quote_ident_rejects_invalid(name):
    with pytest.raises(soql.SoqlError):
        soql.quote_ident(name)


# ─── quote_literal ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "NULL"),
        (True, "TRUE"),
        (False, "FALSE"),
        (42, "42"),
        (3.14, "3.14"),
        ("hola", "'hola'"),
        ("O'Brien", "'O''Brien'"),
        ("with 'apostrophe'", "'with ''apostrophe'''"),
    ],
)
def test_quote_literal(value, expected):
    assert soql.quote_literal(value) == expected


# ─── Filter clauses ───────────────────────────────────────────────────────────


def test_filter_eq():
    out = soql.build_filter_clause({"col": "mes", "op": "=", "val": "Abril"})
    assert out == "`mes` = 'Abril'"


def test_filter_lt():
    out = soql.build_filter_clause({"col": "valor", "op": "<", "val": 1000})
    assert out == "`valor` < 1000"


def test_filter_in_list():
    out = soql.build_filter_clause(
        {"col": "estatus", "op": "in", "val": ["activo", "pasivo"]}
    )
    assert out == "`estatus` IN ('activo', 'pasivo')"


def test_filter_in_requires_list():
    with pytest.raises(soql.SoqlError):
        soql.build_filter_clause({"col": "x", "op": "in", "val": "no_list"})


def test_filter_contains_uses_upper_like():
    out = soql.build_filter_clause(
        {"col": "nombre", "op": "contains", "val": "PEREZ"}
    )
    assert out.startswith("upper(`nombre`) LIKE")
    assert "PEREZ" in out


def test_filter_starts_with():
    out = soql.build_filter_clause(
        {"col": "nombre", "op": "starts_with", "val": "ANA"}
    )
    assert "LIKE upper('ANA%')" in out


def test_filter_is_null_ignores_val():
    out = soql.build_filter_clause({"col": "fecha", "op": "is_null"})
    assert out == "`fecha` IS NULL"


def test_filter_rejects_unknown_op():
    with pytest.raises(soql.SoqlError):
        soql.build_filter_clause({"col": "x", "op": "DROP TABLE", "val": 1})


def test_filter_rejects_non_string_col():
    with pytest.raises(soql.SoqlError):
        soql.build_filter_clause({"col": None, "op": "=", "val": 1})


def test_build_where_none_returns_none():
    assert soql.build_where(None) is None


def test_build_where_multi_combines_with_and():
    out = soql.build_where(
        [
            {"col": "a", "op": "=", "val": 1},
            {"col": "b", "op": "=", "val": 2},
        ]
    )
    assert " AND " in out
    assert out == "`a` = 1 AND `b` = 2"


# ─── Aggregations ─────────────────────────────────────────────────────────────


def test_agg_count_star():
    out = soql.build_agg_expr({"col": None, "fn": "count", "alias": "total"})
    assert out == "count(*) AS `total`"


def test_agg_count_col():
    out = soql.build_agg_expr({"col": "id", "fn": "count", "alias": "n"})
    assert out == "count(`id`) AS `n`"


def test_agg_count_distinct():
    out = soql.build_agg_expr(
        {"col": "nombre", "fn": "count_distinct", "alias": "personas"}
    )
    assert out == "count(DISTINCT `nombre`) AS `personas`"


def test_agg_sum():
    out = soql.build_agg_expr(
        {"col": "valor", "fn": "sum", "alias": "suma"}
    )
    assert out == "sum(`valor`) AS `suma`"


def test_agg_rejects_unknown_fn():
    with pytest.raises(soql.SoqlError):
        soql.build_agg_expr({"col": "x", "fn": "EXEC", "alias": "y"})


def test_agg_count_distinct_requires_col():
    with pytest.raises(soql.SoqlError):
        soql.build_agg_expr({"col": None, "fn": "count_distinct", "alias": "y"})


# ─── Order by ─────────────────────────────────────────────────────────────────


def test_order_by_multi():
    out = soql.build_order_by(
        [{"col": "departamento", "dir": "asc"}, {"col": "fecha", "dir": "desc"}]
    )
    assert out == "`departamento` ASC, `fecha` DESC"


def test_order_by_bad_dir_rejected():
    with pytest.raises(soql.SoqlError):
        soql.build_order_by([{"col": "x", "dir": "drop"}])


def test_order_by_none_returns_none():
    assert soql.build_order_by(None) is None


# ─── SoQL validator (the escape hatch) ────────────────────────────────────────


def test_validate_select_passes():
    out = soql.validate_soql("SELECT * WHERE x=1")
    assert out == "SELECT * WHERE x=1"


def test_validate_strips_trailing_semi():
    out = soql.validate_soql("SELECT 1;")
    assert out == "SELECT 1"


@pytest.mark.parametrize(
    "bad",
    [
        "DROP TABLE x",
        "DELETE FROM x",
        "INSERT INTO x VALUES (1)",
        "UPDATE x SET y=1",
        "ALTER TABLE x ADD c INT",
        "CREATE TABLE x (a INT)",
        "COPY x TO 'out.csv'",
        "PRAGMA something",
        "ATTACH y",
        "LOAD extension",
        "TRUNCATE x",
        "GRANT SELECT TO foo",
        "REVOKE all",
        "VACUUM",
    ],
)
def test_validate_rejects_dangerous(bad):
    with pytest.raises(soql.SoqlError):
        soql.validate_soql(bad)


def test_validate_rejects_multi_statement():
    with pytest.raises(soql.SoqlError):
        soql.validate_soql("SELECT 1; SELECT 2")


def test_validate_rejects_empty():
    with pytest.raises(soql.SoqlError):
        soql.validate_soql("")
    with pytest.raises(soql.SoqlError):
        soql.validate_soql("   ")


def test_validate_rejects_dangerous_in_middle():
    with pytest.raises(soql.SoqlError):
        soql.validate_soql("SELECT * UNION INSERT INTO foo VALUES (1)")


# ─── compose_soql_params ──────────────────────────────────────────────────────


def test_compose_drops_nones():
    p = soql.compose_soql_params(select="a", where=None, limit=10)
    assert p == {"$select": "a", "$limit": "10"}


def test_compose_all_clauses():
    p = soql.compose_soql_params(
        select="a, b",
        where="a > 5",
        group="b",
        having="count(*) > 1",
        order="b DESC",
        limit=100,
        offset=20,
        q="texto",
    )
    assert p == {
        "$select": "a, b",
        "$where": "a > 5",
        "$group": "b",
        "$having": "count(*) > 1",
        "$order": "b DESC",
        "$limit": "100",
        "$offset": "20",
        "$q": "texto",
    }
