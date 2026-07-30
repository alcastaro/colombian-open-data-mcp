"""Tests for the download-and-parse avenue.

The encoding cases are the ones carrying real evidence. Colombian portals emit
the DOS codepages Excel still writes under a Spanish Windows locale, and the
live C4 incident file from Bogotá — 122,435 rows — is semicolon-separated
CP1252. A parser that assumed commas and UTF-8 would return one nonsense column
and call it data.
"""

from __future__ import annotations

import io
import json
import re

import pytest

from colombian_open_data_mcp import tabular
from colombian_open_data_mcp.tabular import TabularError

URL = "https://datosabiertos.example.gov.co/dataset/a/resource/b/download/datos.csv"
ANY = re.compile(r"https://.*example\.gov\.co/.*")

CSV_COMMA = b"id,nombre,valor\n1,Ana,10\n2,Luis,20\n3,Sofia,30\n"
# What Excel writes under a Spanish locale: semicolons, and CP1252 accents.
CSV_SEMI_CP1252 = "ID;AÑO;LOCALIDAD\n1;2026;USAQUÉN\n2;2026;SUBA\n".encode("cp1252")


def xlsx_bytes(rows: list[list], sheet_title: str = "Datos") -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = sheet_title
    for row in rows:
        sheet.append(row)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


# ─── Format classification ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fmt", "url", "expected"),
    [
        ("CSV", "https://x/y", "CSV"),
        ("csv", "https://x/y", "CSV"),
        (".XLSX", "https://x/y", "XLSX"),
        ("GeoJSON", "https://x/y", "GEOJSON"),
        # The format field is missing or nonsense — and both happen — so the
        # extension is the only evidence left.
        (None, "https://x/y/datos.csv", "CSV"),
        ("", "https://x/y/libro.xlsx?v=2", "XLSX"),
        ("Documento", "https://x/y/tabla.json#frag", "JSON"),
        ("SHP", "https://x/y/capa.zip", "SHP"),
        (None, "https://x/y/sin-extension", "UNKNOWN"),
    ],
)
def test_format_classification(fmt, url, expected):
    assert tabular.classify_format(fmt, url) == expected


# ─── Encoding ────────────────────────────────────────────────────────────────


def test_utf8_wins_outright():
    assert tabular.detect_encoding("Año, Región".encode()) == "utf-8"


def test_the_decoding_that_recovers_the_most_spanish_wins():
    """CP850 and CP437 are what Excel emits on a Latin American Windows.

    Read as CP1252 they turn `NIÑOS` into `NI¥OS` — which is exactly the
    mojibake found in 1,308 real Bogotá and Cali column names. Choosing on
    recovered Spanish rather than on a confidence number is what fixes it.
    """
    data = "PISCINA NIÑOS, ÁREA".encode("cp850")
    chosen = tabular.detect_encoding(data)
    assert "Ñ" in data.decode(chosen)


def test_empty_bytes_do_not_crash_the_detector():
    assert tabular.detect_encoding(b"") == "utf-8"


def test_mojibake_scoring_prefers_recovered_spanish():
    assert tabular.mojibake_score("Año Región ÁREA") < tabular.mojibake_score("A±o Regi≤n ┴REA")


# ─── HTML masquerading as data ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        b"<!DOCTYPE html>\n<html><body>Inicie sesion</body></html>",
        b"<html lang='es'><head><title>Error</title></head>",
        b"  \n <head>algo</head>",
    ],
)
def test_html_is_recognised(body):
    assert tabular.looks_like_html(body) is True


def test_a_csv_is_not_mistaken_for_html():
    assert tabular.looks_like_html(CSV_COMMA) is False


async def test_an_html_login_page_served_as_data_is_refused(httpx_mock):
    """A portal answering a download URL with its own login page returns HTTP
    200 and HTML. Parsed as CSV that yields one column and a row count, which a
    model would report as data."""
    httpx_mock.add_response(url=ANY, content=b"<!DOCTYPE html><html>Inicie sesion</html>")
    with pytest.raises(TabularError, match="web page, not data"):
        await tabular.read_resource_file(URL, "CSV")


# ─── CSV ─────────────────────────────────────────────────────────────────────


def test_comma_separated_rows_parse():
    out = tabular.parse_csv(CSV_COMMA, rows=2)
    assert out["delimiter"] == ","
    assert out["columns"] == ["id", "nombre", "valor"]
    assert out["rows_returned"] == 2
    assert out["rows_in_downloaded_part"] == 3
    assert out["rows"][0] == {"id": "1", "nombre": "Ana", "valor": "10"}


def test_semicolon_separated_cp1252_rows_parse():
    """The live Bogotá C4 file is exactly this shape."""
    out = tabular.parse_csv(CSV_SEMI_CP1252, rows=5)
    assert out["delimiter"] == ";"
    assert out["encoding"] == "cp1252"
    assert out["columns"] == ["ID", "AÑO", "LOCALIDAD"]
    assert out["rows"][0]["LOCALIDAD"] == "USAQUÉN"


def test_a_short_row_does_not_lose_its_values():
    out = tabular.parse_csv(b"a,b,c\n1,2\n", rows=5)
    assert out["rows"][0] == {"a": "1", "b": "2"}


def test_a_long_row_keeps_its_extra_values_under_generated_names():
    """Dropping them would silently discard data; a generated name is honest
    about not knowing what the column is called."""
    out = tabular.parse_csv(b"a,b\n1,2,3\n", rows=5)
    assert out["rows"][0] == {"a": "1", "b": "2", "col_2": "3"}


def test_an_empty_csv_is_refused():
    with pytest.raises(TabularError, match="empty"):
        tabular.parse_csv(b"", rows=5)


# ─── Excel ───────────────────────────────────────────────────────────────────


def test_a_workbook_parses():
    """Cartagena publishes 34 of its 38 datasets as XLSX, so this path is not
    an afterthought — it is most of one portal."""
    data = xlsx_bytes([["ID", "Barrio"], [1, "Getsemaní"], [2, "Manga"]])
    out = tabular.parse_excel(data, rows=5)
    assert out["format"] == "xlsx"
    assert out["sheet"] == "Datos"
    assert out["columns"] == ["ID", "Barrio"]
    assert out["rows"][1] == {"ID": 2, "Barrio": "Manga"}


def test_a_workbook_row_limit_is_respected():
    data = xlsx_bytes([["A"], [1], [2], [3], [4]])
    out = tabular.parse_excel(data, rows=2)
    assert out["rows_returned"] == 2
    assert out["rows_in_downloaded_part"] == 4


def test_a_workbook_datetime_becomes_a_string():
    """JSON has no date type, so a datetime would fail to serialise on the way
    to the model."""
    import datetime

    data = xlsx_bytes([["fecha"], [datetime.datetime(2026, 8, 29, 12, 0)]])
    out = tabular.parse_excel(data, rows=1)
    assert out["rows"][0]["fecha"].startswith("2026-08-29")
    json.dumps(out)  # must not raise


def test_a_workbook_with_no_header_is_refused():
    with pytest.raises(TabularError, match="empty"):
        tabular.parse_excel(xlsx_bytes([]), rows=5)


def test_bytes_that_are_not_a_workbook_are_refused():
    with pytest.raises(TabularError, match="workbook"):
        tabular.parse_excel(b"not a zip archive at all", rows=5)


# ─── JSON and GeoJSON ────────────────────────────────────────────────────────


def test_a_json_array_parses():
    out = tabular.parse_json(b'[{"a": 1, "b": 2}, {"a": 3, "c": 4}]', rows=5)
    assert out["format"] == "json"
    assert out["columns"] == ["a", "b", "c"]
    assert out["rows_returned"] == 2


def test_a_json_object_wrapping_a_list_is_unwrapped():
    out = tabular.parse_json(b'{"total": 2, "result": [{"a": 1}, {"a": 2}]}', rows=5)
    assert out["rows"] == [{"a": 1}, {"a": 2}]


def test_a_bare_json_object_becomes_one_row():
    out = tabular.parse_json(b'{"a": 1}', rows=5)
    assert out["rows"] == [{"a": 1}]


def test_geojson_keeps_properties_and_drops_geometry():
    """A polygon's vertices are thousands of numbers that answer no question."""
    body = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "properties": {"NOMBRE": "Suba", "AREA": 10},
                    "geometry": {"type": "Polygon", "coordinates": [[[1, 2]] * 500]},
                }
            ],
        }
    ).encode()
    out = tabular.parse_json(body, rows=5)
    assert out["format"] == "geojson"
    assert out["rows"] == [{"NOMBRE": "Suba", "AREA": 10}]
    assert "coordinates" not in json.dumps(out["rows"])
    assert "Geometry was dropped" in out["note"]


def test_invalid_json_is_refused():
    with pytest.raises(TabularError, match="not valid JSON"):
        tabular.parse_json(b"{no esto no", rows=5)


def test_a_json_scalar_is_refused():
    with pytest.raises(TabularError, match="scalar"):
        tabular.parse_json(b"42", rows=5)


# ─── Download ────────────────────────────────────────────────────────────────


async def test_a_download_is_capped(httpx_mock):
    """A mistyped resource id must not pull a multi-gigabyte shapefile into
    memory. The cap truncates rather than failing, and the answer says so."""
    httpx_mock.add_response(url=ANY, content=b"x" * 5000)
    data, truncated = await tabular.download_capped(URL, max_bytes=1000)
    assert len(data) == 1000
    assert truncated is True


async def test_a_small_download_is_not_marked_truncated(httpx_mock):
    httpx_mock.add_response(url=ANY, content=CSV_COMMA)
    data, truncated = await tabular.download_capped(URL)
    assert data == CSV_COMMA
    assert truncated is False


async def test_an_http_error_says_the_portal_publishes_but_does_not_serve(httpx_mock):
    httpx_mock.add_response(url=ANY, status_code=404)
    with pytest.raises(TabularError, match="does not currently serve"):
        await tabular.download_capped(URL)


async def test_a_timeout_becomes_a_tabular_error(httpx_mock):
    import httpx

    httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=ANY)
    with pytest.raises(TabularError, match="timeout"):
        await tabular.download_capped(URL)


async def test_a_transport_error_becomes_a_tabular_error(httpx_mock):
    import httpx

    httpx_mock.add_exception(httpx.ConnectError("dns"), url=ANY, is_reusable=True)
    with pytest.raises(TabularError, match="network error"):
        await tabular.download_capped(URL)


# ─── End to end ──────────────────────────────────────────────────────────────


async def test_reading_a_csv_resource_end_to_end(httpx_mock):
    httpx_mock.add_response(url=ANY, content=CSV_COMMA)
    out = await tabular.read_resource_file(URL, "CSV", rows=2)
    assert out["rows_returned"] == 2
    assert out["source_url"] == URL
    assert out["bytes_downloaded"] == len(CSV_COMMA)
    assert out["download_truncated"] is False


async def test_reading_an_xlsx_resource_end_to_end(httpx_mock):
    httpx_mock.add_response(url=ANY, content=xlsx_bytes([["A", "B"], [1, 2]]))
    out = await tabular.read_resource_file(URL.replace(".csv", ".xlsx"), "XLSX", rows=5)
    assert out["columns"] == ["A", "B"]


async def test_a_truncated_download_says_the_row_count_is_a_floor(httpx_mock):
    """Reporting a truncated file's row count as the file's row count would be
    a wrong answer that looks exactly like a right one."""
    httpx_mock.add_response(url=ANY, content=b"a,b\n" + b"1,2\n" * 1000)
    out = await tabular.read_resource_file(URL, "CSV", rows=2, max_bytes=100)
    assert out["download_truncated"] is True
    assert "floor" in out["truncation_note"]


@pytest.mark.parametrize("fmt", ["SHP", "GPKG", "KML", "PDF", "DWG", "JPEG"])
async def test_unreadable_formats_are_refused_with_a_route_out(fmt):
    """Refusing with an explanation beats returning an empty table, which looks
    identical to a legitimately empty dataset."""
    with pytest.raises(TabularError, match="city_esri_query"):
        await tabular.read_resource_file("https://x.gov.co/a.zip", fmt)


async def test_an_empty_body_is_refused(httpx_mock):
    httpx_mock.add_response(url=ANY, content=b"")
    with pytest.raises(TabularError, match="empty body"):
        await tabular.read_resource_file(URL, "CSV")


async def test_the_row_ceiling_is_enforced(httpx_mock):
    httpx_mock.add_response(url=ANY, content=b"a\n" + b"1\n" * 900)
    out = await tabular.read_resource_file(URL, "CSV", rows=99999)
    assert out["rows_returned"] == tabular.MAX_ROWS


async def test_fetch_metadata_headers_are_sent(httpx_mock):
    """Some institutional WAFs treat their absence as the signal of an
    unattended client. The values are the true ones for this request — a
    cross-site programmatic fetch — not a claim to be a browser navigating."""
    httpx_mock.add_response(url=ANY, content=CSV_COMMA)
    await tabular.download_capped(URL)
    headers = httpx_mock.get_requests()[-1].headers
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Sec-Fetch-Dest"] == "empty"
    assert "colombian-open-data-mcp" in headers["User-Agent"]
