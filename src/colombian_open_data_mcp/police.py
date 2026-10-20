"""Estadística delictiva published as spreadsheets by the Policía Nacional.

**This source is experimental and it is the only one in this server that is not
an open-data portal.** Everything else here talks to Socrata or CKAN: a
catalogue, stable identifiers, an API contract. This talks to a page on an
institutional website whose URL already ends in ``-old``, publishing 215 Excel
files with inconsistent names. It can change shape or disappear without notice,
and when it does these two tools break while the other twenty-four keep working.
That is why the distinction is stated in the tool descriptions rather than left
for a user to discover.

It is here for the age breakdown, and that is a narrower claim than the one
this module first carried. The DIJIN publishes eleven crime datasets to
datos.gov.co and intentional homicide is not among them, which makes these
files look like the only route to it. They are not: ``m8fd-ahd9``, from the
Ministerio de Defensa's observatory, carries homicide from 2003 to 2026 by
municipality with a real date column, urban/rural zone, presumed modality and a
characterisation field marking feminicide, under CC BY-SA. The two agree —
both put 2023 at 13,555 — and for homicide that one is better on every
structural count. What these files add is the victim's **age group**, which
``m8fd-ahd9`` does not carry.

Three properties of the source shape this module:

**The file name does not tell you the year.** 34 of the 215 files carry no year
at all (``homicidio intencional_0.xlsx``, ``amenzas..xlsx``), and the one that
looks least informative turns out to hold 2023, a year a reader would otherwise
record as missing. The year is only authoritative inside the file, on the line
reading ``PERÍODO DEL 01 DE ENERO AL 31 DE DICIEMBRE DEL 2023``. So the listing
reports the file-name year as a hint and marks it as such, and the reader
returns the period it actually found.

**The header row is not at a fixed offset.** It sits at row 10 in one 2025 file
and at row 9 in the 2023 one, under a variable block of institutional lines. It
is found by looking for the column names, never by counting rows.

**The footer carries the names of three police officers** who prepared, reviewed
and authorised each report. Those lines are dropped before anything is returned:
they are personal data of identifiable people, they are incidental to the
statistics, and nobody querying homicide counts needs them.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from collections import Counter
from typing import Any
from urllib.parse import quote, unquote

import httpx

from . import USER_AGENT
from .netguard import assert_public_url
from .tabular import MAX_BYTES, download_capped

logger = logging.getLogger(__name__)

INDEX_URL = "https://www.policia.gov.co/estadistica-delictiva-old"
FILE_BASE = "https://www.policia.gov.co/sites/default/files/delitos-impacto/"

#: The crawl stops at the first page with no files. The cap is a guard against a
#: site change that makes every page look non-empty, not a statement about how
#: many pages exist (31 at the last count).
MAX_PAGES = 40

INDEX_TIMEOUT = 45.0

#: A file id is the decoded base name and nothing else. The host and the whole
#: path prefix are constants above, so a caller cannot steer the request
#: anywhere: the only thing that varies is the last path segment, and a segment
#: containing a separator is refused outright.
# Traversal needs a separator, and those are refused outright, so a run of
# dots is harmless here. It also has to be allowed: the source publishes
# "hurto a residencias....xlsx" and a dozen like it.
_FORBIDDEN_IN_ID = ("/", "\\", "\x00", "?", "#")

_LINK = re.compile(
    r'href="(/sites/default/files/delitos-impacto/[^"]+\.(?:xlsx|xls))"',
    re.IGNORECASE,
)

#: ``Homicidio Intencional2025.xlsx``, ``Homicidio Intencional 2010.xlsx`` and
#: ``homicidios_en_accidente_de_transito_2013_1.xlsx`` all parse; the 34 files
#: with no four-digit year in the name do not, and are reported with a null
#: year rather than a guessed one.
_NAME = re.compile(r"^(?P<delito>.+?)[ _]*(?P<anio>(?:19|20)\d{2})(?:[ _]\d+)?$")

#: Three of these on one row is what identifies the header, in a file whose
#: first column is ``ARMAS MEDIOS`` in one year and ``ARMA MEDIO`` in another.
_HEADER_KEYS = ("DEPARTAMENTO", "MUNICIPIO", "CANTIDAD", "FECHA HECHO", "CODIGO DANE")

#: Dropped wherever they appear. The first three carry officers' names; the
#: asterisked line is a footnote about the age-group coding.
_FOOTER_MARKERS = (
    "elabor",
    "revis",
    "autoriz",
    "proyect",
    "consolid",
)

_PERIODO = re.compile(r"PER[ÍI]ODO[^\n]{0,120}", re.IGNORECASE)
_PERIODO_ANIO = re.compile(r"(?:19|20)\d{2}")
_FECHA = re.compile(r"(\d{1,2})/(\d{1,2})/((?:19|20)\d{2})")

#: Canonical names for the columns, matched by substring and in this order.
#: An exact table does not survive this source: the same report calls its date
#: column "FECHA HECHO" in 2025 and "FECHA" in 2022, its weapon column "ARMAS
#: MEDIOS" and "ARMA MEDIO", and its age column "*AGRUPA EDAD PERSONA*",
#: "*AGRUPA_EDAD_PERSONA" and "*AGRUPA EDAD".
_COLUMN_RULES: tuple[tuple[str, str], ...] = (
    ("DEPARTAMENTO", "departamento"),
    ("MUNICIPIO", "municipio"),
    ("DANE", "codigo_dane"),
    ("FECHA", "fecha_hecho"),
    ("GENERO", "genero"),
    ("SEXO", "genero"),
    ("EDAD", "grupo_etario"),
    ("AGRUPA", "grupo_etario"),
    ("ARMA", "armas_medios"),
    ("CANTIDAD", "cantidad"),
)

GROUPABLE = ("anio", "mes", "departamento", "municipio", "codigo_dane", "genero", "grupo_etario")

MAX_ROWS = 500


class PoliceError(RuntimeError):
    """Raised when the index or a workbook cannot be read as expected."""


# ─── identifiers ─────────────────────────────────────────────────────────────


def is_valid_file_id(value: str) -> bool:
    """True if ``value`` is a bare spreadsheet base name.

    Deliberately strict about separators and deliberately permissive about
    everything else: the real names carry accents, spaces, parentheses and runs
    of dots (``hurto a residencias....xlsx``), and rejecting those would reject
    most of the catalogue.
    """
    v = (value or "").strip()
    if not v or len(v) > 160:
        return False
    if not v.lower().endswith((".xlsx", ".xls")):
        return False
    return not any(bad in v for bad in _FORBIDDEN_IN_ID)


def file_url(file_id: str) -> str:
    """Absolute URL for a validated file id."""
    if not is_valid_file_id(file_id):
        raise PoliceError(
            f"Not a valid file id: {file_id!r}. Ids are bare file names ending in "
            ".xlsx or .xls, exactly as police_list_crime_files reports them."
        )
    return FILE_BASE + quote(file_id)


def _slug(text: str) -> str:
    """Accent-free lowercase key, for matching a crime name a caller typed."""
    n = unicodedata.normalize("NFKD", text or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()


def parse_file_name(path_or_id: str) -> dict[str, Any]:
    """Split a file name into crime and year, without inventing either."""
    base = unquote(path_or_id).rsplit("/", 1)[-1]
    stem, _, ext = base.rpartition(".")
    stem = stem.rstrip(". _")
    # Drupal appends _0, _1, _17 when a name is reused. It marks the upload,
    # not the data, so it is removed before reading the crime and the year.
    stem = re.sub(r"[ _]\d{1,2}$", "", stem).rstrip(". _") if not _NAME.match(stem) else stem
    m = _NAME.match(stem)
    if m:
        delito = m.group("delito").replace("_", " ").strip(" .-")
        anio: int | None = int(m.group("anio"))
    else:
        delito, anio = stem.replace("_", " ").strip(" .-"), None
    return {
        "file_id": base,
        "crime_name_hint": delito,
        "crime_key": _slug(delito),
        "year_hint": anio,
        "format": ext.lower(),
    }


# ─── index ───────────────────────────────────────────────────────────────────


async def fetch_index(max_pages: int = MAX_PAGES) -> list[dict[str, Any]]:
    """Walk the paginated index and return one entry per published file.

    An empty page ends the walk. A page that answers but holds no links at all
    on the *first* request is treated as an error rather than an empty result:
    the whole point of the tool is that the site can change shape, and a silent
    empty list would read as "the Policía published nothing".
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=INDEX_TIMEOUT,
        follow_redirects=True,
    ) as client:
        for page in range(max_pages):
            url = INDEX_URL if page == 0 else f"{INDEX_URL}?page={page}"
            await assert_public_url(url)
            try:
                r = await client.get(url)
                r.raise_for_status()
            except httpx.HTTPError as e:
                if page == 0:
                    raise PoliceError(f"Could not read the index at {INDEX_URL}: {e}") from e
                logger.warning("police index page %s failed: %s", page, e)
                break
            links = _LINK.findall(r.text)
            if not links:
                if page == 0:
                    raise PoliceError(
                        f"The index at {INDEX_URL} answered but contained no spreadsheet "
                        "links. The page has most likely changed structure, which this "
                        "source has always been able to do; its URL ends in '-old'."
                    )
                break
            for href in links:
                if href in seen:
                    continue
                seen.add(href)
                entry = parse_file_name(href)
                entry["page"] = page
                out.append(entry)
    return out


def summarise_index(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Group the flat index by crime, and say plainly what is not known."""
    por: dict[str, dict[str, Any]] = {}
    for e in entries:
        g = por.setdefault(
            e["crime_key"], {"crime": e["crime_name_hint"], "years_in_name": [], "files": []}
        )
        if e["year_hint"] is not None:
            g["years_in_name"].append(e["year_hint"])
        g["files"].append(
            {"file_id": e["file_id"], "year_hint": e["year_hint"], "format": e["format"]}
        )
    for g in por.values():
        g["years_in_name"] = sorted(set(g["years_in_name"]))
        g["files_without_year_in_name"] = sum(1 for f in g["files"] if f["year_hint"] is None)
    sin_anio = sum(1 for e in entries if e["year_hint"] is None)
    return {
        "source": "Policía Nacional — estadística delictiva",
        "source_url": INDEX_URL,
        "experimental": True,
        "total_files": len(entries),
        "files_without_year_in_name": sin_anio,
        "crimes": dict(sorted(por.items())),
    }


# ─── workbook ────────────────────────────────────────────────────────────────


def _rows_from_xlsx(data: bytes) -> list[list[str]]:
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheet = book.worksheets[0]
        return [
            ["" if c is None else str(c).strip() for c in row]
            for row in sheet.iter_rows(values_only=True)
        ]
    finally:
        book.close()


def _rows_from_xls(data: bytes) -> list[list[str]]:
    try:
        import xlrd
    except ImportError as e:
        raise PoliceError(
            "This file is in the legacy .xls format, which needs the optional 'xlrd' "
            "package. Install it with: pip install 'colombian-open-data-mcp[xls]'. "
            "Nine of the Policía's files are .xls; the rest are .xlsx and read without it."
        ) from e
    book = xlrd.open_workbook(file_contents=data)
    sheet = book.sheet_by_index(0)

    def cell(r: int, c: int) -> str:
        # A date in .xls arrives as an Excel serial number, so the raw value
        # reads as "45017.0" and no date pattern matches it. Converting here
        # keeps every later step working on one shape of date.
        if sheet.cell_type(r, c) == xlrd.XL_CELL_DATE:
            try:
                y, mo, d, *_ = xlrd.xldate_as_tuple(sheet.cell_value(r, c), book.datemode)
                return f"{d:02d}/{mo:02d}/{y:04d}"
            except Exception:
                pass
        return str(sheet.cell_value(r, c)).strip()

    return [[cell(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]


def _is_footer(cells: list[str]) -> bool:
    first = (cells[0] if cells else "").strip().lower()
    if first.startswith("*"):
        return True
    return any(first.startswith(m) for m in _FOOTER_MARKERS)


def find_header(rows: list[list[str]]) -> int:
    """Index of the column row, located by its content.

    Counting a fixed number of institutional lines would be wrong: the same
    report puts the header on row 10 one year and row 9 the next.
    """
    for i, cells in enumerate(rows[:60]):
        joined = " ".join(cells).upper().replace("_", " ")
        if sum(1 for k in _HEADER_KEYS if k in joined) >= 3:
            return i
    raise PoliceError(
        "Could not find the column row in this workbook. It is identified by its "
        "column names rather than by position, so either the file is not one of the "
        "crime reports or the Policía changed its layout."
    )


def _canonical(name: str) -> str:
    key = re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper())
    key = re.sub(r"\s+", " ", key).strip()
    for needle, canonical in _COLUMN_RULES:
        if needle in key:
            return canonical
    return re.sub(r"[^a-z0-9]+", "_", _slug(name)).strip("_") or "col"


def extract_period(rows: list[list[str]], header_at: int) -> dict[str, Any]:
    """The period line above the header, which is the authoritative year."""
    for cells in rows[:header_at]:
        joined = " ".join(c for c in cells if c).strip()
        m = _PERIODO.search(joined)
        if m:
            texto = re.sub(r"\s+", " ", m.group(0)).strip()
            anios = [int(a) for a in _PERIODO_ANIO.findall(texto)]
            return {"period_text": texto, "period_year": anios[-1] if anios else None}
    return {"period_text": None, "period_year": None}


def _year_month(value: str) -> tuple[str | None, str | None]:
    m = _FECHA.search(value or "")
    if m:
        return m.group(3), f"{int(m.group(2)):02d}"
    # Some years store a real date cell, which arrives as YYYY-MM-DD…
    m2 = re.match(r"((?:19|20)\d{2})-(\d{2})", (value or "").strip())
    if m2:
        return m2.group(1), m2.group(2)
    return None, None


def parse_workbook(data: bytes, fmt: str) -> dict[str, Any]:
    """Rows, period and column names from one crime report.

    The institutional header and the whole footer are dropped. The footer is
    dropped for a reason worth stating: it names the three officers who
    prepared, reviewed and authorised the report, and that is personal data
    this server has no reason to move.
    """
    rows = _rows_from_xls(data) if fmt == "xls" else _rows_from_xlsx(data)
    if not rows:
        raise PoliceError("The workbook's first sheet is empty.")
    header_at = find_header(rows)
    period = extract_period(rows, header_at)
    raw_header = rows[header_at]
    names = [_canonical(h) for h in raw_header]
    seen: Counter[str] = Counter()
    header: list[str] = []
    for n in names:
        seen[n] += 1
        header.append(n if seen[n] == 1 else f"{n}_{seen[n]}")

    records: list[dict[str, Any]] = []
    dropped_footer = 0
    dropped_totals = 0
    for cells in rows[header_at + 1 :]:
        if not any(c for c in cells):
            continue
        if _is_footer(cells):
            dropped_footer += 1
            continue
        rec: dict[str, Any] = {
            header[i]: cells[i] for i in range(min(len(header), len(cells))) if header[i]
        }
        if not any(str(v).strip() for v in rec.values()):
            continue
        # The sheet ends with a totals row: every dimension blank and the whole
        # year's count in `cantidad`. Counting it doubles the total, which is
        # how 13,555 homicides in 2023 first read as 27,110 here.
        if (
            not str(rec.get("departamento", "")).strip()
            and not str(rec.get("municipio", "")).strip()
        ):
            dropped_totals += 1
            continue
        anio, mes = _year_month(str(rec.get("fecha_hecho", "")))
        rec["anio"], rec["mes"] = anio, mes
        try:
            rec["cantidad"] = int(float(str(rec.get("cantidad", "")).replace(",", "") or 0))
        except (TypeError, ValueError):
            rec["cantidad"] = None
        records.append(rec)
    return {
        "columns": [h for h in header if h],
        "records": records,
        "footer_lines_dropped": dropped_footer,
        "totals_rows_dropped": dropped_totals,
        **period,
    }


def aggregate(records: list[dict[str, Any]], group_by: list[str]) -> list[dict[str, Any]]:
    """Sum ``cantidad`` over the requested grouping."""
    bad = [g for g in group_by if g not in GROUPABLE]
    if bad:
        raise PoliceError(f"Cannot group by {bad}. Available: {list(GROUPABLE)}")
    acc: dict[tuple, dict[str, Any]] = {}
    for r in records:
        key = tuple(r.get(g) for g in group_by)
        slot = acc.setdefault(key, {**{g: r.get(g) for g in group_by}, "casos": 0, "filas": 0})
        slot["casos"] += r.get("cantidad") or 0
        slot["filas"] += 1
    return sorted(acc.values(), key=lambda x: tuple(str(x.get(g) or "") for g in group_by))


async def read_crime_file(
    file_id: str,
    rows: int = 25,
    group_by: list[str] | None = None,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Download and parse one crime report."""
    url = file_url(file_id)
    await assert_public_url(url)
    data, truncated = await download_capped(url, max_bytes=max_bytes)
    if truncated:
        raise PoliceError(
            f"This workbook is larger than the {max_bytes:,}-byte download cap and only "
            "part of it arrived. A spreadsheet cannot be parsed from a prefix."
        )
    fmt = "xls" if file_id.lower().endswith(".xls") else "xlsx"
    parsed = parse_workbook(data, fmt)
    meta = parse_file_name(file_id)
    out: dict[str, Any] = {
        "source": "Policía Nacional — estadística delictiva",
        "experimental": True,
        "file_id": meta["file_id"],
        "crime_name_hint": meta["crime_name_hint"],
        "year_in_file_name": meta["year_hint"],
        "period_text": parsed["period_text"],
        "period_year": parsed["period_year"],
        "columns": parsed["columns"],
        "rows_total": len(parsed["records"]),
        "cases_total": sum(r.get("cantidad") or 0 for r in parsed["records"]),
        "licence": (
            "Not declared. The page publishing these files states no licence and no "
            "terms of use, so this server does not assert one. Check with the Policía "
            "Nacional before redistributing, and cite the source URL."
        ),
        "source_url": INDEX_URL,
        "notes": _notes(meta, parsed),
    }
    if group_by:
        out["group_by"] = group_by
        out["rows"] = aggregate(parsed["records"], group_by)
        out["rows_returned"] = len(out["rows"])
    else:
        n = max(1, min(int(rows), MAX_ROWS))
        out["rows"] = parsed["records"][:n]
        out["rows_returned"] = len(out["rows"])
    return out


def _notes(meta: dict[str, Any], parsed: dict[str, Any]) -> list[str]:
    notes = [
        "Casos denunciados ante la Policía Nacional. These are reports received, not "
        "prevalence: a change in the series can reflect reporting behaviour as much as "
        "the underlying phenomenon.",
        "Figures are subject to revision by the source; the Policía re-publishes files "
        "without changing their names.",
        "The last month of any cut is under-registered, because cases reported late are "
        "added after the file is published.",
    ]
    if meta["year_hint"] is None and parsed["period_year"]:
        notes.append(
            f"This file's name carries no year; the period inside it says "
            f"{parsed['period_year']}. Trust the period, not the file name."
        )
    elif meta["year_hint"] and parsed["period_year"] and meta["year_hint"] != parsed["period_year"]:
        notes.append(
            f"The file name says {meta['year_hint']} and the period inside says "
            f"{parsed['period_year']}. The period is authoritative."
        )
    if parsed.get("totals_rows_dropped"):
        notes.append(
            f"{parsed['totals_rows_dropped']} totals row(s) were dropped. The source "
            "puts a grand total at the foot of the sheet with every dimension blank; "
            "counting it doubles the year."
        )
    if parsed["footer_lines_dropped"]:
        notes.append(
            f"{parsed['footer_lines_dropped']} footer lines were dropped, including the "
            "names of the officers who prepared the report; they are personal data and "
            "are never returned."
        )
    return notes
