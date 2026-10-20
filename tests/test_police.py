"""The Policía Nacional crime spreadsheets.

Fixtures are trimmed copies of three real files: a 2025 .xlsx, the 2023 one
whose name carries no year, and a 2022 .xls in the legacy format. Each keeps
the institutional header, a few data rows, the totals row and a footer, because
every one of those is something the parser has to handle and got wrong at least
once while it was being written. The officers' names in the fixture footers are
placeholders; the real files carry real ones, which is exactly why they are
dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from colombian_open_data_mcp import police

FIXTURES = Path(__file__).parent / "fixtures" / "police"
X2025 = FIXTURES / "homicidio_2025.xlsx"
X2023 = FIXTURES / "homicidio_sin_anio_2023.xlsx"
XLS2022 = FIXTURES / "homicidios_2022.xls"

try:
    import xlrd  # noqa: F401

    HAS_XLRD = True
except ImportError:  # pragma: no cover - depends on how the env was installed
    HAS_XLRD = False

#: The legacy format is an optional extra, so a suite installed without it must
#: skip these rather than fail. The error path when it is missing is covered by
#: test_a_legacy_file_without_xlrd_says_how_to_install_it below.
needs_xlrd = pytest.mark.skipif(not HAS_XLRD, reason="the 'xls' extra is not installed")


def parse(path: Path) -> dict:
    fmt = "xls" if path.suffix == ".xls" else "xlsx"
    return police.parse_workbook(path.read_bytes(), fmt)


# ─── file ids are not URLs ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "notaspreadsheet",
        "../../etc/passwd",
        "../secret.xlsx",
        "sub/dir/file.xlsx",
        "dir\\file.xlsx",
        "file.xlsx?x=1",
        "file.xlsx#frag",
        "https://evil.test/file.xlsx",
        "x" * 200 + ".xlsx",
    ],
)
def test_rejects_anything_that_is_not_a_bare_file_name(bad):
    """The host and path prefix are constants; only the last segment varies.

    A separator in the id is the one thing that could steer the request, so it
    is refused before a URL is built rather than sanitised afterwards.
    """
    assert police.is_valid_file_id(bad) is False
    with pytest.raises(police.PoliceError):
        police.file_url(bad)


@pytest.mark.parametrize(
    "good",
    [
        "Homicidio Intencional2025.xlsx",
        "homicidio intencional_0.xlsx",
        "hurto a residencias....xlsx",
        "Lesiones en accidente de tránsito2025.xlsx",
        "homicidios2022.xls",
    ],
)
def test_accepts_the_real_names_including_the_ugly_ones(good):
    """Accents, spaces and runs of dots are what the source actually publishes."""
    assert police.is_valid_file_id(good) is True
    url = police.file_url(good)
    assert url.startswith(police.FILE_BASE)
    assert "/sites/default/files/delitos-impacto/" in url


def test_the_built_url_never_leaves_the_fixed_prefix():
    url = police.file_url("Homicidio Intencional2025.xlsx")
    assert url.count("://") == 1
    assert url.split("/delitos-impacto/")[1].count("/") == 0


# ─── the file name is a hint, the file is the fact ───────────────────────────


def test_year_is_parsed_from_the_name_when_it_is_there():
    meta = police.parse_file_name("Homicidio%20Intencional2025.xlsx")
    assert meta["year_hint"] == 2025
    assert meta["crime_key"] == "homicidio intencional"


def test_a_name_without_a_year_yields_no_year_rather_than_a_guess():
    """34 of the 215 published files carry no year at all."""
    meta = police.parse_file_name("homicidio intencional_0.xlsx")
    assert meta["year_hint"] is None
    assert meta["crime_key"] == "homicidio intencional"


def test_the_period_inside_the_file_recovers_the_missing_year():
    """The decisive case. This file's name says nothing and its contents say
    2023 — a year a reader going by file names records as missing entirely."""
    out = parse(X2023)
    assert out["period_year"] == 2023
    assert "2023" in out["period_text"]


@needs_xlrd
def test_the_period_also_reveals_a_partial_year():
    """2022 runs to 31 August. A series built from file names alone would
    compare eight months against twelve and call it a drop in homicide."""
    out = parse(XLS2022)
    assert out["period_year"] == 2022
    assert "AGOSTO" in out["period_text"].upper()


# ─── layout ──────────────────────────────────────────────────────────────────


def test_the_header_is_found_by_content_not_by_position():
    """Row 10 in the 2025 file, row 9 in the 2023 one, under a variable block
    of institutional lines."""
    assert (
        police.find_header(
            [[""], ["MINISTERIO"], ["ARMAS MEDIOS", "DEPARTAMENTO", "MUNICIPIO", "CANTIDAD"]]
        )
        == 2
    )


def test_a_workbook_without_the_known_columns_fails_loudly():
    with pytest.raises(police.PoliceError, match="column row"):
        police.find_header([["a", "b"], ["c", "d"]])


@pytest.mark.parametrize("path", [X2025, X2023, pytest.param(XLS2022, marks=needs_xlrd)])
def test_every_year_normalises_to_the_same_columns(path):
    """The source renames its own columns between years: FECHA HECHO and
    FECHA, ARMAS MEDIOS and ARMA MEDIO, three spellings of the age group."""
    out = parse(path)
    for expected in ("departamento", "municipio", "codigo_dane", "fecha_hecho", "cantidad"):
        assert expected in out["columns"], (path.name, out["columns"])


@pytest.mark.parametrize("path", [X2025, X2023, pytest.param(XLS2022, marks=needs_xlrd)])
def test_dates_parse_in_all_three_shapes(path):
    """Text dd/mm/yyyy, a datetime cell, and an .xls serial number."""
    out = parse(path)
    assert out["records"]
    assert all(r["anio"] for r in out["records"]), path.name


# ─── what must never come back ───────────────────────────────────────────────


@pytest.mark.parametrize("path", [X2025, X2023, pytest.param(XLS2022, marks=needs_xlrd)])
def test_the_officers_who_signed_the_report_are_never_returned(path):
    """The footer names the people who prepared, reviewed and authorised each
    report. That is personal data of identifiable people and it is incidental
    to a homicide count, so it does not travel."""
    out = parse(path)
    blob = repr(out["records"]).lower()
    for marcador in ("elabor", "revis", "autoriz", "nombre apellido"):
        assert marcador not in blob, (path.name, marcador)
    assert out["footer_lines_dropped"] >= 1


@pytest.mark.parametrize("path", [X2025, X2023])
def test_the_totals_row_is_dropped(path):
    """The sheet ends with a grand total carrying every dimension blank.
    Counting it reported 27,110 homicides for a year that had 13,555."""
    out = parse(path)
    assert out["totals_rows_dropped"] >= 1
    assert all(r.get("departamento") or r.get("municipio") for r in out["records"])


def test_the_totals_row_is_not_counted_in_the_sum():
    """The fixture's totals row carries 9999, far above its six real rows."""
    out = parse(X2025)
    total = sum(r["cantidad"] or 0 for r in out["records"])
    assert total < 9999, total


# ─── rollups ─────────────────────────────────────────────────────────────────


def test_grouping_sums_cases_not_rows():
    out = parse(X2025)
    por_anio = police.aggregate(out["records"], ["anio"])
    assert [r["anio"] for r in por_anio] == ["2025"]
    assert por_anio[0]["casos"] == sum(r["cantidad"] or 0 for r in out["records"])


def test_grouping_by_an_unknown_column_is_refused():
    with pytest.raises(police.PoliceError, match="Cannot group by"):
        police.aggregate([], ["nombre_victima"])


def test_every_groupable_column_actually_groups():
    out = parse(X2025)
    for col in police.GROUPABLE:
        rows = police.aggregate(out["records"], [col])
        assert rows, col


# ─── the index ───────────────────────────────────────────────────────────────


def test_the_summary_separates_what_is_known_from_what_is_guessed():
    entries = [
        police.parse_file_name("/x/Homicidio Intencional2025.xlsx"),
        police.parse_file_name("/x/homicidio intencional_0.xlsx"),
        police.parse_file_name("/x/Delitos sexuales2024.xlsx"),
    ]
    out = police.summarise_index(entries)
    assert out["experimental"] is True
    assert out["total_files"] == 3
    assert out["files_without_year_in_name"] == 1
    hom = out["crimes"]["homicidio intencional"]
    assert hom["years_in_name"] == [2025]
    assert hom["files_without_year_in_name"] == 1


async def test_an_index_that_answers_with_no_links_is_an_error_not_an_empty_list(httpx_mock):
    """A silent empty list would read as 'the Policía published nothing'. The
    page's own URL ends in '-old', so it changing shape is expected."""
    httpx_mock.add_response(url=police.INDEX_URL, text="<html><body>nada</body></html>")
    with pytest.raises(police.PoliceError, match="changed structure"):
        await police.fetch_index()


async def test_an_index_that_refuses_the_first_page_is_an_error(httpx_mock):
    httpx_mock.add_response(url=police.INDEX_URL, status_code=503)
    with pytest.raises(police.PoliceError, match="Could not read the index"):
        await police.fetch_index()


# ─── the envelope the tools return ───────────────────────────────────────────


async def test_the_tool_refuses_a_bad_id_without_calling_out():
    """No httpx_mock is registered, so any outbound request would fail loudly."""
    from colombian_open_data_mcp import server

    tool = server.mcp._tool_manager.get_tool("police_read_crime_file")
    assert tool is not None
    fn = tool.fn
    out = await fn(file_id="../../etc/passwd")
    assert "error" in out and "hint" in out


def test_the_notes_say_what_the_numbers_are_and_are_not():
    out = police._notes(police.parse_file_name("Homicidio Intencional2025.xlsx"), parse(X2025))
    blob = " ".join(out).lower()
    assert "denunciados" in blob
    assert "prevalence" in blob
    assert "under-registered" in blob


def test_a_legacy_file_without_xlrd_says_how_to_install_it(monkeypatch):
    """The failure a user hits on nine of the 217 files has to name the fix.

    An ImportError surfacing as "No module named 'xlrd'" tells someone reading
    a homicide series nothing they can act on.
    """
    import builtins

    real = builtins.__import__

    def sin_xlrd(name, *a, **kw):
        if name == "xlrd":
            raise ImportError("No module named 'xlrd'")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", sin_xlrd)
    with pytest.raises(police.PoliceError, match=r"colombian-open-data-mcp\[xls\]"):
        police.parse_workbook(XLS2022.read_bytes(), "xls")
