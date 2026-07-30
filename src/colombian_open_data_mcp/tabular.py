"""Download and parse a published tabular file, for resources no DataStore holds.

**Why this exists.** Bogotá's CKAN DataStore returns rows for about 27% of its
catalogue. :mod:`.esri` reaches a further third of what is left. The remainder
that is still readable is plain files — CSV, XLSX, JSON — sitting at a download
URL with nothing querying them. Measured on a random Bogotá sample: 38 of the
150 datasets fall in that bucket, all 25 sampled files downloaded on the first
attempt with the right content type, and 88% of them are served from a
``*.bogota.gov.co`` address. Adding this avenue moves Bogotá from roughly 63%
(with ESRI alone) to roughly 88%.

This is the same job the Dominican MCP does for its whole catalogue, because
``datos.gob.do`` runs CKAN with **no** DataStore extension at all — there, every
single row has to come out of a downloaded file. Here it is the third fallback,
not the main road, and that difference justifies a much smaller module. Three
things the Dominican version needs and this one does not:

* **No link resolver.** Its catalogue registers Google Drive viewer pages and
  HTML landing pages as if they were data. Nothing of the kind was found here.
* **No reachability census.** Its files live on 266 institutional sites and
  about half refuse programmatic requests. Bogotá hosts its own.
* **No cache and no DuckDB.** Download, parse the first N rows, answer, discard.
  Persisting anything would turn this server into a data controller under Ley
  1581 de 2012 for datasets containing identifiable people, which is a decision
  to take deliberately and separately, not to acquire as a side effect of a
  performance optimisation.

What is ported, because it was learned from real bytes rather than guessed: the
encoding heuristic. Colombian portals emit the same DOS codepages Excel still
writes in Latin America, and a survey of 1,308 Bogotá and Cali column names
turned up ``Correo electr¢nico`` and ``PISCINA NI¥OS`` — CP850 and CP437 read as
CP1252. Choosing the decoding that recovers the most Spanish, rather than the
one chardet is least unsure about, is what turns those back into ``electrónico``
and ``NIÑOS``.

Every request goes through :func:`netguard.guard_request_hook`, and the URL is
never supplied by the model: the tool takes a resource UUID and asks the
portal's own catalogue for the address.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

import httpx

from . import USER_AGENT
from .netguard import NetGuardError, guard_request_hook

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0

#: Hard byte ceiling for one call. Big enough for the overwhelming majority of
#: these catalogues' files, small enough that a mistake costs seconds. Bogotá's
#: largest sampled CSV was 34 MB; the cap truncates rather than failing, and the
#: answer says so.
MAX_BYTES = 12 * 1024 * 1024

#: Rows returned at most. The point is a readable sample, not a bulk transfer.
MAX_ROWS = 500
DEFAULT_ROWS = 25

#: Extensions and CKAN format strings this module can parse.
CSV_FORMATS = {"CSV", "TSV", "TXT", "TAB"}
EXCEL_FORMATS = {"XLSX", "XLSM", "XLS"}
JSON_FORMATS = {"JSON", "GEOJSON"}
READABLE_FORMATS = CSV_FORMATS | EXCEL_FORMATS | JSON_FORMATS

# Fetch-metadata headers. Browsers have sent these since 2020 and most HTTP
# libraries still do not, and some institutional WAFs treat their absence as the
# signal of an unattended client. The values here are the true ones for what
# this client does — a cross-site programmatic fetch whose destination is not a
# document. Claiming `navigate`/`document` would also pass and would be a lie
# about the request, so it is not done.
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Dest": "empty",
}

_SPANISH_LETTERS = "áéíóúüñÁÉÍÓÚÜÑ¿¡"
_MOJIBAKE_PAIRS = ("Ã¡", "Ã©", "Ã­", "Ã³", "Ãº", "Ã±", "Â¿", "â€", "ï¿½", "�")
_CODEPAGE_LADDER = ("cp1252", "cp850", "cp437", "iso-8859-1")

# Scripts that cannot occur in a Colombian government file but appear the moment
# a decoding goes wrong in a specific direction: the DOS codepages map their
# high bytes to Greek letters, box-drawing pieces and maths operators, so `Año`
# misread as CP437 becomes `A±o`. Those are the tell.
_ALIEN_RANGES = (
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x052F),  # Cyrillic
    (0x0590, 0x08FF),  # Hebrew, Arabic
    (0x2200, 0x22FF),  # mathematical operators
    (0x2500, 0x259F),  # box drawing
    (0x2E00, 0x9FFF),  # CJK
)


class TabularError(RuntimeError):
    """Raised when a resource cannot be downloaded or parsed."""


def _is_alien(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _ALIEN_RANGES)


def mojibake_score(text: str) -> int:
    """How wrong a decoding looks. Lower is better.

    Counting *suspicious* characters is the wrong question: every candidate
    renders the same byte as some odd symbol, so oddness under all of them is
    noise. What separates a right decoding from a wrong one is how much Spanish
    it recovers — the correct codepage yields ``Año`` and ``ÁREA``, the wrong one
    ``A¥o`` and ``┴REA`` — so Spanish letters score positively and impossible
    scripts are penalised hard.
    """
    spanish = sum(text.count(c) for c in _SPANISH_LETTERS)
    alien = sum(1 for c in text if _is_alien(ord(c)))
    mangled = sum(text.count(p) for p in _MOJIBAKE_PAIRS)
    return 5 * alien + 2 * mangled - spanish


def detect_encoding(data: bytes) -> str:
    """The encoding that yields the least garbled text.

    UTF-8 wins outright when the bytes decode as it, which is the common case.
    Otherwise the candidates compete on :func:`mojibake_score` and the bytes
    decide, rather than a confidence number deciding for them.
    """
    if not data:
        return "utf-8"
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    sample = data[:100_000]
    best, best_score = _CODEPAGE_LADDER[0], None
    for enc in _CODEPAGE_LADDER:
        score = mojibake_score(sample.decode(enc, errors="replace"))
        if best_score is None or score < best_score:
            best, best_score = enc, score
    return best


def looks_like_html(data: bytes) -> bool:
    """Whether these bytes are a web page rather than data.

    A portal answering a download URL with its own login page or an error page
    returns HTTP 200 and HTML. Parsing that as CSV yields one nonsense column
    and a row count, which a model would report as data. Detecting it costs
    nothing and turns a silent wrong answer into an explicit one.
    """
    head = data[:1024].lstrip().lower()
    return (
        head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head>" in head[:200]
    )


def classify_format(fmt: str | None, url: str) -> str:
    """The parser to use, from the catalogue's format field and the URL.

    The format field is checked first and the URL second, because the field is
    what a publisher curated and the extension is what survived a CMS. When they
    disagree the format field wins; when the field is missing or nonsense — and
    both happen — the extension is the only evidence left.
    """
    f = (fmt or "").strip().upper().lstrip(".")
    if f in READABLE_FORMATS:
        return f
    tail = url.split("?")[0].split("#")[0].rsplit(".", 1)
    if len(tail) == 2:
        ext = tail[1].strip().upper()
        if ext in READABLE_FORMATS:
            return ext
    return f or "UNKNOWN"


async def download_capped(url: str, max_bytes: int = MAX_BYTES) -> tuple[bytes, bool]:
    """Stream ``url`` up to ``max_bytes``. Returns the bytes and whether it was cut.

    Streaming with a cap rather than reading the whole body is what keeps a
    mistyped resource id from pulling a multi-gigabyte shapefile into memory.
    The cap truncates instead of failing so a partial answer is still an answer,
    and the caller is told the sample is partial.
    """
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
        headers=_HEADERS,
        # Validates this request and every redirect hop.
        event_hooks={"request": [guard_request_hook]},
    ) as client:
        try:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise TabularError(
                        f"HTTP {r.status_code} downloading the resource file. The portal "
                        "publishes this address but does not currently serve it."
                    )
                buf = bytearray()
                truncated = False
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= max_bytes:
                        truncated = True
                        break
                return bytes(buf[:max_bytes]), truncated
        except NetGuardError:
            raise
        except httpx.TimeoutException as e:
            raise TabularError(f"timeout downloading the resource (>{DEFAULT_TIMEOUT}s)") from e
        except httpx.HTTPError as e:
            raise TabularError(f"network error downloading the resource: {e}") from e


def parse_csv(data: bytes, rows: int) -> dict[str, Any]:
    """First ``rows`` records of a delimited text file.

    The delimiter is sniffed rather than assumed: Colombian portals publish
    semicolon-separated files as often as comma-separated ones, because that is
    what Excel writes under a Spanish locale.
    """
    encoding = detect_encoding(data)
    text = data.decode(encoding, errors="replace")
    try:
        dialect: Any = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        dialect, delimiter = csv.excel, ","
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise TabularError("The file is empty.") from None
    out: list[dict[str, Any]] = []
    total = 0
    for record in reader:
        total += 1
        if len(out) < rows:
            out.append(
                {(header[i] if i < len(header) else f"col_{i}"): v for i, v in enumerate(record)}
            )
    return {
        "format": "csv",
        "delimiter": delimiter,
        "encoding": encoding,
        "columns": header,
        "rows_in_downloaded_part": total,
        "rows_returned": len(out),
        "rows": out,
    }


def parse_excel(data: bytes, rows: int) -> dict[str, Any]:
    """First ``rows`` records of the first worksheet.

    Read-only and values-only: formulas are not evaluated and styling is not
    loaded, which is both faster and the only way openpyxl will open some of the
    files these portals publish.
    """
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover - openpyxl is a hard dependency
        raise TabularError("openpyxl is required to read Excel resources") from e
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise TabularError(
            f"Could not open this file as a workbook ({type(e).__name__}). It may be "
            "the older .xls format, which openpyxl does not read, or truncated by the "
            "download cap."
        ) from e
    try:
        sheet = book.worksheets[0]
        iterator = sheet.iter_rows(values_only=True)
        try:
            header_row = next(iterator)
        except StopIteration:
            raise TabularError("The workbook's first sheet is empty.") from None
        header = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(header_row)]
        out: list[dict[str, Any]] = []
        total = 0
        for record in iterator:
            total += 1
            if len(out) < rows:
                out.append(
                    {
                        (header[i] if i < len(header) else f"col_{i}"): (
                            v.isoformat() if hasattr(v, "isoformat") else v
                        )
                        for i, v in enumerate(record)
                    }
                )
        return {
            "format": "xlsx",
            "sheet": sheet.title,
            "sheet_names": book.sheetnames,
            "columns": header,
            "rows_in_downloaded_part": total,
            "rows_returned": len(out),
            "rows": out,
        }
    finally:
        book.close()


def parse_json(data: bytes, rows: int) -> dict[str, Any]:
    """First ``rows`` records of a JSON array, or of a GeoJSON feature collection.

    GeoJSON geometry is dropped for the same reason the ESRI client never asks
    for it: a polygon's vertices are thousands of numbers that answer no
    question, and they would crowd out the properties that do.
    """
    encoding = detect_encoding(data)
    try:
        body = json.loads(data.decode(encoding, errors="replace"))
    except ValueError as e:
        raise TabularError(f"The file is not valid JSON: {e}") from e

    geometry_dropped = False
    if isinstance(body, dict) and isinstance(body.get("features"), list):
        geometry_dropped = True
        records = [f.get("properties", {}) for f in body["features"] if isinstance(f, dict)]
        fmt = "geojson"
    elif isinstance(body, list):
        records, fmt = body, "json"
    elif isinstance(body, dict):
        for value in body.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                records, fmt = value, "json"
                break
        else:
            records, fmt = [body], "json"
    else:
        raise TabularError("The JSON file holds a scalar, not a table.")

    out = records[:rows]
    columns: list[str] = []
    for record in out:
        if isinstance(record, dict):
            for k in record:
                if k not in columns:
                    columns.append(k)
    result = {
        "format": fmt,
        "encoding": encoding,
        "columns": columns,
        "rows_in_downloaded_part": len(records),
        "rows_returned": len(out),
        "rows": out,
    }
    if geometry_dropped:
        result["note"] = (
            "This is a GeoJSON feature collection; each row is a feature's "
            "properties. Geometry was dropped — it would be thousands of "
            "coordinates per row."
        )
    return result


async def read_resource_file(
    url: str,
    fmt: str | None = None,
    rows: int = DEFAULT_ROWS,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Download ``url`` and return its first ``rows`` records.

    Raises :class:`TabularError` with an explanation a model can act on for
    everything that is not readable — a geospatial archive, a PDF, an HTML page
    served in place of data — rather than returning an empty table that looks
    like a legitimately empty dataset.
    """
    rows = min(max(int(rows), 1), MAX_ROWS)
    kind = classify_format(fmt, url)
    if kind not in READABLE_FORMATS:
        raise TabularError(
            f"Resource format {kind!r} is not a readable table. This tool reads "
            f"{', '.join(sorted(READABLE_FORMATS))}. Geospatial archives (SHP, GPKG, "
            "KML, DWG), PDFs and images have to be opened by a specialised tool; if "
            "the dataset also publishes an ESRI REST service, city_esri_query reaches "
            "the same data."
        )

    data, truncated = await download_capped(url, max_bytes=max_bytes)
    if not data:
        raise TabularError("The portal returned an empty body for this resource.")
    if kind not in EXCEL_FORMATS and looks_like_html(data):
        raise TabularError(
            "This address returns a web page, not data — usually a login or error "
            "page served with HTTP 200. The published file is not currently "
            "retrievable."
        )

    if kind in EXCEL_FORMATS:
        result = parse_excel(data, rows)
    elif kind in JSON_FORMATS:
        result = parse_json(data, rows)
    else:
        result = parse_csv(data, rows)

    result["source_url"] = url
    result["bytes_downloaded"] = len(data)
    result["download_truncated"] = truncated
    if truncated:
        result["truncation_note"] = (
            f"Only the first {max_bytes:,} bytes were downloaded, so "
            "`rows_in_downloaded_part` is a floor, not the file's row count."
        )
    return result
