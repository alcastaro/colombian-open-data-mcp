"""SoQL query builder + safe identifier/literal escaping.

Socrata's SoQL (Socrata Query Language) is SQL-ish but with $-prefixed
clauses ($select, $where, $group, $order, $limit, $offset, $having, $q).
This module builds clauses programmatically with defence-in-depth against
injection: allowlist regex on column identifiers + denylist of forbidden
substrings + proper single-quote escaping on literals.

Tools receive structured JSON (filters as dicts, aggregations as dicts)
and we transform to safe SoQL here. Raw user SoQL goes through validate_soql
which rejects DDL/DML and multi-statement queries.
"""

from __future__ import annotations

import re
from typing import Any, Literal

# ─── Identifier discipline ────────────────────────────────────────────────────
#
# Socrata column names ("field_name" in their parlance) are typically lowercase
# alphanumeric with underscores. Some portals expose original-case names. The
# allowlist below covers both, plus the accented-Latin range for Spanish field
# names that some datos.gov.co publishers use.

_IDENT_OK = re.compile(r"^[\w .À-ſ]+$", re.UNICODE)
_IDENT_FORBIDDEN_SUBSTR = ("--", "/*", "*/", ";")


class SoqlError(ValueError):
    """Raised for any invalid SoQL construction (injection attempt, bad value)."""


def quote_ident(name: str) -> str:
    """Quote a SoQL field identifier.

    Socrata accepts backtick-quoted identifiers. We use that. Two layers:
        1. Regex allowlist (alphanumeric + underscore + dot + space + Latin
           accents).
        2. Denylist of comment/statement-break substrings.

    Either failure raises SoqlError.
    """
    if not name or not _IDENT_OK.match(name):
        raise SoqlError(f"Invalid SoQL identifier: {name!r}")
    for bad in _IDENT_FORBIDDEN_SUBSTR:
        if bad in name:
            raise SoqlError(f"Forbidden substring in identifier: {name!r}")
    return "`" + name.replace("`", "") + "`"


def quote_literal(value: Any) -> str:
    """Quote a SoQL literal value."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    return "'" + s.replace("'", "''") + "'"


# ─── Filters ──────────────────────────────────────────────────────────────────

ALLOWED_OPS = {
    "=",
    "!=",
    "<>",
    "<",
    "<=",
    ">",
    ">=",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "ends_with",
    "is_null",
    "is_not_null",
}

Op = Literal[
    "=",
    "!=",
    "<>",
    "<",
    "<=",
    ">",
    ">=",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "ends_with",
    "is_null",
    "is_not_null",
]


def build_filter_clause(f: dict[str, Any]) -> str:
    """Convert one filter dict to a SoQL boolean expression."""
    col = f.get("col")
    op = f.get("op", "=")
    val = f.get("val")
    if not isinstance(col, str):
        raise SoqlError("filter.col must be a string")
    if op not in ALLOWED_OPS:
        raise SoqlError(f"Operator not allowed: {op}")

    q = quote_ident(col)

    if op == "is_null":
        return f"{q} IS NULL"
    if op == "is_not_null":
        return f"{q} IS NOT NULL"
    if op == "in":
        if not isinstance(val, list) or not val:
            raise SoqlError("'in' requires non-empty list")
        joined = ", ".join(quote_literal(v) for v in val)
        return f"{q} IN ({joined})"
    if op == "not_in":
        if not isinstance(val, list) or not val:
            raise SoqlError("'not_in' requires non-empty list")
        joined = ", ".join(quote_literal(v) for v in val)
        return f"{q} NOT IN ({joined})"
    if op == "contains":
        if not isinstance(val, str):
            raise SoqlError("'contains' requires string val")
        return f"upper({q}) LIKE upper({quote_literal('%' + val + '%')})"
    if op == "starts_with":
        if not isinstance(val, str):
            raise SoqlError("'starts_with' requires string val")
        return f"upper({q}) LIKE upper({quote_literal(val + '%')})"
    if op == "ends_with":
        if not isinstance(val, str):
            raise SoqlError("'ends_with' requires string val")
        return f"upper({q}) LIKE upper({quote_literal('%' + val)})"

    # Comparison ops.
    cmp_op = "<>" if op == "!=" else op
    return f"{q} {cmp_op} {quote_literal(val)}"


def build_where(filters: list[dict] | None) -> str | None:
    """Build the value for the $where clause from a list of filter dicts."""
    if not filters:
        return None
    return " AND ".join(build_filter_clause(f) for f in filters)


# ─── Aggregations ─────────────────────────────────────────────────────────────

ALLOWED_AGG_FNS = {
    "count",
    "count_distinct",
    "sum",
    "avg",
    "mean",
    "median",
    "min",
    "max",
    "stddev",
}


def build_agg_expr(agg: dict) -> str:
    """Build a single aggregation expression (for inclusion in $select)."""
    col = agg.get("col")
    fn = (agg.get("fn") or "").lower()
    alias = agg.get("alias") or f"{fn}_{col or 'all'}"
    if fn not in ALLOWED_AGG_FNS:
        raise SoqlError(f"Aggregation not allowed: {fn}")

    alias_q = quote_ident(alias)

    if fn == "count" and col in (None, "*"):
        return f"count(*) AS {alias_q}"
    # Every remaining function names a column. Saying so once here beats a
    # confusing "expected str, got None" from quote_ident five branches down.
    if col is None:
        raise SoqlError(f"Aggregation {fn!r} requires a column name in 'col'")
    if fn == "count":
        return f"count({quote_ident(col)}) AS {alias_q}"
    if fn == "count_distinct":
        return f"count(DISTINCT {quote_ident(col)}) AS {alias_q}"
    if fn in ("avg", "mean"):
        return f"avg({quote_ident(col)}) AS {alias_q}"
    if fn == "median":
        return f"median({quote_ident(col)}) AS {alias_q}"
    if fn in ("sum", "min", "max", "stddev"):
        return f"{fn}({quote_ident(col)}) AS {alias_q}"
    raise SoqlError(f"Unhandled fn: {fn}")


# ─── Order by ─────────────────────────────────────────────────────────────────


def build_order_by(order_by: list[dict] | None) -> str | None:
    """Build the value for the $order clause."""
    if not order_by:
        return None
    parts: list[str] = []
    for ob in order_by:
        col = ob.get("col")
        if col is None:
            raise SoqlError("Each order_by entry needs a 'col'")
        direction = (ob.get("dir") or "asc").lower()
        if direction not in ("asc", "desc"):
            raise SoqlError(f"Invalid order direction: {direction}")
        parts.append(f"{quote_ident(col)} {direction.upper()}")
    return ", ".join(parts)


# ─── Raw-SoQL validator (the escape hatch) ────────────────────────────────────
#
# Socrata DOES allow some write operations via the data API (POST/PUT/DELETE
# /resource/<4x4>.json with auth), but our MCP only sends GET. So at the HTTP
# layer we're already read-only. The validator below adds defence at the
# query-string layer too: rejects SQL DDL/DML keywords and multi-statement
# queries.

_SOQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|"
    r"copy|attach|detach|pragma|set|load|install|vacuum|analyze)\b",
    re.IGNORECASE,
)


def validate_soql(soql: str) -> str:
    """Reject any SoQL containing DDL/DML keywords or multi-statement breaks.

    Returns the cleaned SoQL (trailing semicolons stripped).
    """
    s = (soql or "").strip().rstrip(";").strip()
    if not s:
        raise SoqlError("Empty SoQL query")
    if ";" in s:
        raise SoqlError("Multiple statements are not allowed")
    if _SOQL_FORBIDDEN.search(s):
        raise SoqlError("SoQL contains a forbidden keyword (write ops disallowed)")
    return s


# ─── Compose query-string params from a structured spec ───────────────────────


def compose_soql_params(
    *,
    select: str | None = None,
    where: str | None = None,
    group: str | None = None,
    having: str | None = None,
    order: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    q: str | None = None,
) -> dict[str, str]:
    """Build the $-prefixed query params for a Socrata data API call."""
    params: dict[str, str] = {}
    if select:
        params["$select"] = select
    if where:
        params["$where"] = where
    if group:
        params["$group"] = group
    if having:
        params["$having"] = having
    if order:
        params["$order"] = order
    if limit is not None:
        params["$limit"] = str(int(limit))
    if offset is not None:
        params["$offset"] = str(int(offset))
    if q:
        params["$q"] = q
    return params
