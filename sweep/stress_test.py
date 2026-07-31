#!/usr/bin/env python3
"""Stress test: exercise the real MCP tools against a random sample of both portals.

Why this exists
---------------
The hermetic suite proves the code does what we told it to. It cannot prove the
portals behave the way we assumed, because it never touches them. This harness
answers the other question: across a few hundred real datasets picked at random,
**how often does each tool actually return usable data, and when it fails, why?**

That distinction matters. A tool that works on the three datasets a developer
happened to try is not the same as a tool that works on the catalogue.

What it does
------------
1. Draws a random sample from each portal, using random offsets across the whole
   catalogue rather than the first N results — the head of a catalogue is not
   representative of it (Bogotá's first 100 datasets are overwhelmingly
   geospatial, which would have put DataStore coverage at 9% instead of 43%).
2. Runs the **real MCP tool functions**, not the HTTP clients underneath them.
   Anything this harness reports is what a model would actually receive.
3. Records every outcome — success, failure, error class, latency, row counts —
   to JSONL, then writes a Markdown report.

The seed is recorded in the report so any run can be reproduced exactly.

Usage
-----
    uv run python sweep/stress_test.py                    # 450 datasets, evenly split
    uv run python sweep/stress_test.py --total 900
    uv run python sweep/stress_test.py --seed 42 --concurrency 4
    uv run python sweep/stress_test.py --only bogota      # or national, cali

Politeness
----------
Both portals are public infrastructure and Bogotá's sits behind a WAF that has
refused probes before. Concurrency defaults to 4 and there is a small delay
between requests. Do not raise it to "go faster" — a refused sample is worse
data than a slow one, and getting an IP throttled hurts the next person too.

Never runs in CI. This is a deliberate, occasional measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from colombian_open_data_mcp import ckan, esri, server, socrata, tabular  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"
REPORT_DIR = ROOT / "internal" / "reportes"

# Catalogue sizes, refreshed at run time. These are only fallbacks for the
# random-offset draw if the count call itself fails.
FALLBACK_NATIONAL = 8391
FALLBACK_CKAN = {"bogota": 1917, "cali": 657, "valle": 50, "cartagena": 38}

# Formats a hypothetical download-and-parse layer could read. Used to size the
# gap honestly: the rest is geospatial and no CSV parser would help.
TABULAR = {"CSV", "XLSX", "XLS", "JSON", "ODS", "TSV", "TXT"}
# Formats that are queryable *services* rather than static files — these can be
# read without downloading anything, which is the interesting part.
SERVICE = {"ESRI REST", "WFS", "WMS"}


@dataclass
class Probe:
    """One dataset put through the tools, with every outcome recorded."""

    portal: str
    prefix: str = ""
    city: str = ""
    dataset_id: str = ""
    title: str = ""
    steps: dict[str, str] = field(default_factory=dict)  # step -> ok | error class
    errors: dict[str, str] = field(default_factory=dict)  # step -> message
    timings_ms: dict[str, float] = field(default_factory=dict)
    n_resources: int = 0
    n_queryable: int = 0
    formats: list[str] = field(default_factory=list)
    rows_available: int | None = None
    columns: int | None = None
    row_sample_ok: bool = False
    # Which of the three avenues actually delivered readable rows, if any.
    # Recorded so the headline coverage figure can be broken down rather than
    # being an opaque number that grew when new tools landed.
    avenue: str = ""


def classify(result: Any) -> tuple[str, str]:
    """Turn a tool's return value into (status, message).

    Tools never raise; they return an envelope. So "did it work" is a question
    about the shape of a dict, and the error text is the only clue to why.
    """
    if not isinstance(result, dict):
        return "ok", ""
    if "error" not in result:
        return "ok", ""
    msg = str(result["error"])
    low = msg.lower()
    if "not a valid" in low:
        return "invalid-id", msg
    if "timeout" in low:
        return "timeout", msg
    if "404" in msg:
        return "http-404", msg
    if "403" in msg or "blocked" in low or "waf" in low:
        return "blocked", msg
    if "500" in msg or "502" in msg or "503" in msg:
        return "http-5xx", msg
    if "non-json" in low:
        return "non-json", msg
    if "reported failure" in low:
        return "ckan-failure", msg
    return "other", msg


async def timed(probe: Probe, step: str, coro) -> Any:
    t0 = time.perf_counter()
    try:
        result = await coro
    except Exception as exc:  # a tool raising is itself a finding
        probe.timings_ms[step] = (time.perf_counter() - t0) * 1000
        probe.steps[step] = "RAISED"
        probe.errors[step] = f"{type(exc).__name__}: {exc}"
        return None
    probe.timings_ms[step] = (time.perf_counter() - t0) * 1000
    status, msg = classify(result)
    probe.steps[step] = status
    if msg:
        probe.errors[step] = msg[:300]
    return result


# ─── National portal (Socrata) ───────────────────────────────────────────────


async def probe_national(item: dict) -> Probe:
    """Walk one national dataset the way a model would: metadata, rows, rollup."""
    res = item.get("resource") or {}
    p = Probe(portal="datos.gov.co", dataset_id=res.get("id", ""), title=res.get("name", "")[:90])

    meta = await timed(p, "get_dataset", _tool("get_dataset")(id=p.dataset_id))
    columns: list[dict] = []
    if isinstance(meta, dict) and "error" not in meta:
        columns = meta.get("columns") or []
        p.columns = len(columns)
        p.n_resources = 1
        p.n_queryable = 1

    preview = await timed(
        p, "download_dataset_preview", _tool("download_dataset_preview")(id=p.dataset_id, rows=2)
    )
    if isinstance(preview, dict) and "error" not in preview:
        p.row_sample_ok = bool(preview.get("rows"))

    agg = await timed(
        p,
        "aggregate_dataset",
        _tool("aggregate_dataset")(
            id=p.dataset_id, aggregations=[{"col": None, "fn": "count", "alias": "n"}]
        ),
    )
    if isinstance(agg, dict) and "error" not in agg:
        rows = agg.get("rows") or []
        if rows:
            try:
                p.rows_available = int(rows[0].get("n"))
            except (TypeError, ValueError):
                pass

    # A typed filter needs a real column name; skip rather than fake one, so a
    # skip is never counted as a pass.
    if columns:
        col = columns[0].get("field_name")
        if col:
            await timed(
                p,
                "filter_dataset",
                _tool("filter_dataset")(id=p.dataset_id, columns=[col], limit=2),
            )
    return p


# ─── Bogotá (CKAN) ───────────────────────────────────────────────────────────


def _tool(name: str):
    """The callable behind a registered tool name.

    The city tools are closures produced by ckan_tools.register and never bound
    to a module-level name, so this is how a caller reaches them — which is
    also what makes this harness exercise the same path a client would.
    """
    return server.mcp._tool_manager.get_tool(name).fn


async def probe_ckan(portal: ckan.CkanPortal, pkg: dict) -> Probe:
    """Walk one territorial dataset through every avenue until one returns rows.

    The order is deliberate and is the same one a model should follow: the
    DataStore first, because it is a typed query against a table the portal
    already built; then the ESRI service, because it is still an API and still
    transfers nothing but the answer; and only then the published file, which
    is a download. Each step stops as soon as rows come back, so the polite
    order and the fast order are the same order.

    `avenue` records which one delivered, so the headline number can be broken
    down. A coverage figure that rose because new tools landed, without saying
    which tool did the work, would be a number nobody could check.
    """
    city = portal.key
    p = Probe(
        portal=portal.host,
        prefix=city,
        city=portal.city,
        dataset_id=pkg.get("name") or pkg.get("id", ""),
        title=(pkg.get("title") or "")[:90],
    )

    meta = await timed(p, "city_get_dataset", _tool("city_get_dataset")(city=city, id=p.dataset_id))
    if not isinstance(meta, dict) or "error" in meta:
        return p

    resources = meta.get("resources") or []
    p.n_resources = len(resources)
    p.n_queryable = sum(1 for r in resources if r.get("queryable"))
    p.formats = [r.get("format") or "?" for r in resources]

    # ── Avenue 1: the CKAN DataStore ─────────────────────────────────────────
    target = next((r for r in resources if r.get("queryable")), None)
    if target is None:
        # Not a failure of the server — the portal never pushed this one into
        # the DataStore. Recorded distinctly so it does not pollute error rates.
        p.steps["city_resource_preview"] = "no-datastore"
    else:
        prev = await timed(
            p,
            "city_resource_preview",
            _tool("city_resource_preview")(city=city, resource_id=target["id"], rows=2),
        )
        if isinstance(prev, dict) and "error" not in prev and prev.get("rows"):
            p.row_sample_ok = True
            p.avenue = "datastore"
            p.rows_available = prev.get("total_rows_matching")
            p.columns = len(prev.get("fields") or [])

            fields = [f["name"] for f in (prev.get("fields") or []) if f.get("name") != "_id"]
            if fields:
                await timed(
                    p,
                    "city_filter_resource",
                    _tool("city_filter_resource")(
                        city=city, resource_id=target["id"], columns=fields[:2], limit=2
                    ),
                )
            return p

    # ── Avenue 2: an ArcGIS REST service ─────────────────────────────────────
    service = next(
        (
            r
            for r in resources
            if "ESRI" in (r.get("format") or "").upper() and esri.is_layer_url(r.get("url") or "")
        ),
        None,
    )
    if service is not None:
        rows = await timed(
            p,
            "city_esri_query",
            _tool("city_esri_query")(city=city, resource_id=service["id"], limit=2),
        )
        if isinstance(rows, dict) and "error" not in rows and rows.get("rows"):
            p.row_sample_ok = True
            p.avenue = "esri"
            p.columns = len(rows.get("fields") or [])
            return p

    # ── Avenue 3: a published file ───────────────────────────────────────────
    readable = next(
        (
            r
            for r in resources
            if tabular.classify_format(r.get("format"), r.get("url") or "")
            in tabular.READABLE_FORMATS
        ),
        None,
    )
    if readable is not None:
        rows = await timed(
            p,
            "city_read_resource_file",
            _tool("city_read_resource_file")(city=city, resource_id=readable["id"], rows=2),
        )
        if isinstance(rows, dict) and "error" not in rows and rows.get("rows"):
            p.row_sample_ok = True
            p.avenue = "archivo"
            p.columns = len(rows.get("columns") or [])

    return p


# ─── Sampling ────────────────────────────────────────────────────────────────


async def catalogue_size_national() -> int:
    try:
        raw = await server._client.catalog_search(limit=1)
        return int(raw.get("resultSetSize") or FALLBACK_NATIONAL)
    except Exception:
        return FALLBACK_NATIONAL


async def catalogue_size_ckan(key: str) -> int:
    try:
        body = await server._ckan_clients[key].package_search(rows=1)
        return int(body.get("count") or FALLBACK_CKAN[key])
    except Exception:
        return FALLBACK_CKAN[key]


async def sample_national(n: int, rng: random.Random) -> list[dict]:
    """Draw n datasets spread across the whole Socrata catalogue.

    Spread the same way ``sample_ckan`` is, and for the same reason: a
    catalogue's ordering correlates with what its datasets contain, so taking
    whole consecutive pages measures the ordering as much as the catalogue.
    """
    size = await catalogue_size_national()
    page = 25
    pages = max(1, math.ceil(size / page))
    offsets = rng.sample(range(pages), k=min(pages, max(n // 5, 8)))
    per_page = max(1, math.ceil(n / len(offsets)))
    out: list[dict] = []
    for off in offsets:
        try:
            raw = await server._client.catalog_search(limit=page, offset=off * page)
        except socrata.SocrataError:
            continue
        results = raw.get("results") or []
        rng.shuffle(results)
        out.extend(results[:per_page])
        await asyncio.sleep(0.25)
        if len(out) >= n:
            break
    rng.shuffle(out)
    return out[:n]


async def sample_ckan(key: str, n: int, rng: random.Random) -> list[dict]:
    """Draw n datasets spread across the catalogue, or all of it if smaller.

    **This function had a clustering defect worth describing, because the number
    it produced was wrong in a way that looked plausible.** It drew random page
    offsets and then took all 25 datasets on each page. A CKAN catalogue is not
    randomly ordered: Cali's 144 IDESC cartographic bundles — JPEG, RAR and WMS,
    none of them a table — sit contiguously, so landing on two of those pages
    put fifty unreadable datasets into a sample of a hundred and twenty. The
    harness reported 45.8% coverage for a portal whose whole catalogue, counted
    exhaustively, carries a DataStore resource on 440 of 657 datasets — 67%.

    Neither figure was a lie and the sample was genuinely random; it was random
    over *pages*, and the thing being measured varies by page. So the fix is to
    spread the draw: take a few datasets from many pages rather than every
    dataset from a few. Same request budget, an estimate that tracks the
    population instead of the catalogue's ordering.

    The page count also used to floor, which quietly capped Cartagena at 25 of
    its 38 datasets — one page by that arithmetic, and the last 13 unreachable
    whatever was asked for. Valle happened to work only because 50 divides into
    two whole pages. Both are small enough to walk entirely, and walking them
    entirely is a better measurement than sampling them.
    """
    size = await catalogue_size_ckan(key)
    page = 25
    pages = max(1, math.ceil(size / page))

    if n >= size:  # small catalogue: take all of it
        offsets, per_page = list(range(pages)), page
    else:
        # Aim for ~5 datasets from each of as many pages as the budget allows.
        offsets = rng.sample(range(pages), k=min(pages, max(n // 5, 8)))
        per_page = max(1, math.ceil(n / len(offsets)))

    out: list[dict] = []
    for off in offsets:
        try:
            body = await server._ckan_clients[key].package_search(rows=page, start=off * page)
        except ckan.CkanError:
            continue
        results = body.get("results") or []
        rng.shuffle(results)
        out.extend(results[:per_page])
        await asyncio.sleep(0.35)
        if len(out) >= n:
            break
    rng.shuffle(out)
    return out[:n]


# ─── Report ──────────────────────────────────────────────────────────────────


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def percentiles(values: list[float]) -> str:
    if not values:
        return "n/a"
    s = sorted(values)
    p50 = statistics.median(s)
    p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
    return f"p50 {p50:.0f} ms · p95 {p95:.0f} ms · max {s[-1]:.0f} ms"


def step_table(probes: list[Probe], steps: list[str]) -> str:
    """One row per tool.

    `no-datastore` is counted apart from failures on purpose. It means the
    portal never pushed that resource into its database — the tool behaved
    correctly by saying so. Folding it into the failure rate would blame this
    server for a decision made by Bogotá's publishing pipeline.
    """
    lines = [
        "| Herramienta | Aplicables | OK | Tasa | No aplica | Latencia | Fallos por clase |",
        "|---|---|---|---|---|---|---|",
    ]
    for step in steps:
        attempted = [p for p in probes if step in p.steps]
        if not attempted:
            continue
        na = [p for p in attempted if p.steps[step] == "no-datastore"]
        applicable = [p for p in attempted if p.steps[step] != "no-datastore"]
        ok = [p for p in applicable if p.steps[step] == "ok"]
        fails = Counter(p.steps[step] for p in applicable if p.steps[step] != "ok")
        lat = [p.timings_ms[step] for p in attempted if step in p.timings_ms]
        fail_txt = ", ".join(f"{k} {v}" for k, v in fails.most_common()) or "—"
        lines.append(
            f"| `{step}` | {len(applicable)} | {len(ok)} | {pct(len(ok), len(applicable))} "
            f"| {len(na) or '—'} | {percentiles(lat)} | {fail_txt} |"
        )
    return "\n".join(lines)


NATIONAL_STEPS = [
    "get_dataset",
    "download_dataset_preview",
    "aggregate_dataset",
    "filter_dataset",
]
CITY_STEPS = [
    "city_get_dataset",
    "city_resource_preview",
    "city_filter_resource",
    "city_esri_query",
    "city_read_resource_file",
]

NATIONAL_HOST = "datos.gov.co"


def build_report(by_portal: dict[str, list[Probe]], meta: dict) -> str:
    all_probes = [p for probes in by_portal.values() for p in probes]
    L: list[str] = []
    A = L.append

    A("# Informe de prueba de fuerza — portales colombianos")
    A("")
    A(f"**Ejecutado:** {meta['started']}  ")
    A(f"**Duración:** {meta['elapsed_s']:.0f} s  ")
    A(f"**Semilla:** `{meta['seed']}` (reproducible con `--seed {meta['seed']}`)  ")
    A(f"**Concurrencia:** {meta['concurrency']}  ")
    A(f"**Versión del servidor:** {meta['version']}")
    A("")
    A(
        f"Se sometieron **{len(all_probes)} conjuntos de datos** elegidos al azar en "
        f"{len(by_portal)} portales a las herramientas reales del MCP — no a los "
        "clientes HTTP internos, sino a las mismas funciones que invoca un modelo. "
        "Todo lo que sigue es lo que un asistente recibiría de verdad."
    )
    A("")
    A(
        "La muestra se reparte a lo largo de todo el catálogo — unos pocos "
        "conjuntos de cada una de muchas páginas — y no se toma ni de los "
        "primeros N resultados ni de páginas completas. Las dos formas cortas "
        "han dado cifras equivocadas aquí: los primeros 100 datasets de Bogotá "
        "son casi todos geoespaciales, y una página al azar tampoco sirve, "
        "porque el orden de un catálogo CKAN se correlaciona con lo que "
        "contienen sus conjuntos — los 144 paquetes cartográficos del IDESC de "
        "Cali están contiguos, y muestrear por páginas enteras reportó un 46% "
        "de cobertura para un portal cuyo conteo exhaustivo da 67%."
    )
    A("")

    # ── Headline
    A("## Resumen")
    A("")
    A(
        "| Portal | Plataforma | Muestra | Devolvió filas reales | Tasa | DataStore | ESRI | Archivo |"
    )
    A("|---|---|---|---|---|---|---|---|")
    for host in sorted(by_portal, key=lambda h: (h != NATIONAL_HOST, h)):
        probes = by_portal[host]
        ok = sum(1 for p in probes if p.row_sample_ok)
        platform = "Socrata" if host == NATIONAL_HOST else "CKAN"
        by_avenue = Counter(p.avenue for p in probes if p.row_sample_ok)
        A(
            f"| `{host}` | {platform} | {len(probes)} | {ok} | **{pct(ok, len(probes))}** "
            f"| {by_avenue.get('datastore', 0) or '—'} | {by_avenue.get('esri', 0) or '—'} "
            f"| {by_avenue.get('archivo', 0) or '—'} |"
        )
    A("")
    A(
        "«Devolvió filas reales» es la prueba dura: el MCP entregó datos que el "
        "modelo puede leer, no solo metadatos. Las tres últimas columnas dicen "
        "**por qué vía** llegaron, y esa desagregación es deliberada: una cifra "
        "de cobertura que sube cuando se añaden herramientas, sin decir cuál "
        "hizo el trabajo, no es un número que nadie pueda comprobar."
    )
    A("")
    A(
        "El orden de intento es DataStore → servicio ESRI → archivo publicado, y "
        "se detiene en la primera que devuelve filas. Es a la vez el orden "
        "cortés y el rápido: el DataStore es una consulta tipada contra una "
        "tabla que el portal ya construyó, ESRI sigue siendo una API que no "
        "transfiere más que la respuesta, y solo el archivo implica una descarga."
    )
    A("")
    ckan_probes = {h: v for h, v in by_portal.items() if h != NATIONAL_HOST}
    if ckan_probes:
        worst = min(
            (sum(1 for p in v if p.row_sample_ok) / len(v), h) for h, v in ckan_probes.items()
        )
        A(
            f"Portal CKAN con menor cobertura: `{worst[1]}` con "
            f"**{100 * worst[0]:.1f}%**. El objetivo de v0.4.0 era ≥60% en todos."
        )
        A("")

    for host in sorted(by_portal, key=lambda h: (h != NATIONAL_HOST, h)):
        probes = by_portal[host]
        is_national = host == NATIONAL_HOST
        steps = NATIONAL_STEPS if is_national else CITY_STEPS
        title = "Portal nacional" if is_national else probes[0].city or host

        A(f"## {title} — `{host}`")
        A("")
        A(step_table(probes, steps))
        A("")

        if is_national:
            sizes = [p.rows_available for p in probes if p.rows_available is not None]
            if sizes:
                sizes.sort()
                A(
                    f"Tamaño de los datasets consultados: mediana "
                    f"**{statistics.median(sizes):,.0f} filas**, máximo "
                    f"**{sizes[-1]:,} filas**. Ninguna de esas filas viajó por la "
                    "red: `count(*)` lo resolvió Socrata en su servidor."
                )
                A("")
            continue

        with_ds = sum(1 for p in probes if p.n_queryable > 0)
        res_total = sum(p.n_resources for p in probes)
        res_query = sum(p.n_queryable for p in probes)
        A("### Cobertura de DataStore")
        A("")
        A("| Medida | Valor |")
        A("|---|---|")
        A(
            f"| Datasets con al menos un recurso marcado consultable | "
            f"{with_ds} / {len(probes)} — **{pct(with_ds, len(probes))}** |"
        )
        A(
            f"| Recursos individuales marcados consultables | "
            f"{res_query} / {res_total} — **{pct(res_query, res_total)}** |"
        )
        lying = sum(1 for p in probes if p.steps.get("city_resource_preview") == "http-404")
        tried = sum(
            1 for p in probes if p.steps.get("city_resource_preview") not in (None, "no-datastore")
        )
        if tried:
            A(
                f"| Marcados consultables que **no** tienen tabla (404) | "
                f"{lying} / {tried} — **{pct(lying, tried)}** |"
            )
        A("")
        if lying:
            A(
                "La última fila es el catálogo del portal equivocándose sobre sí "
                "mismo. `datastore_active` dice que el recurso es consultable y no "
                "lo es. El servidor reescribe ese 404 para decirlo explícitamente, "
                "en vez de reenviar un error crudo que el modelo leería como culpa "
                "suya."
            )
            A("")

        fmt = Counter(f for p in probes for f in p.formats)
        if fmt:
            tab = sum(v for k, v in fmt.items() if k in TABULAR)
            svc = sum(v for k, v in fmt.items() if k in SERVICE)
            other = sum(fmt.values()) - tab - svc
            A("### De qué está hecho el resto")
            A("")
            A("| Formato | Recursos | Naturaleza |")
            A("|---|---|---|")
            for k, v in fmt.most_common(12):
                nature = (
                    "tabular — lo leería un parser CSV/XLSX"
                    if k in TABULAR
                    else "**servicio consultable**"
                    if k in SERVICE
                    else "geoespacial / archivo estático"
                )
                A(f"| {k} | {v} | {nature} |")
            A("")
            A(
                f"De {sum(fmt.values())} recursos: {tab} tabulares, **{svc} son "
                f"servicios consultables** (ESRI REST / WFS / WMS — tienen "
                f"`?query=`, no hay que descargarlos), y {other} son archivos "
                "geoespaciales o estáticos."
            )
            A("")

    # ── Errors
    A("## Fallos observados")
    A("")
    errs = Counter()
    for p in all_probes:
        for step, status in p.steps.items():
            if status not in ("ok", "no-datastore"):
                errs[f"{step} → {status}"] += 1
    if not errs:
        A("Ninguno. Todas las llamadas devolvieron datos utilizables.")
    else:
        A("| Herramienta → clase | Veces |")
        A("|---|---|")
        for k, v in errs.most_common(25):
            A(f"| `{k}` | {v} |")
        A("")
        A("Ejemplos textuales (uno por clase):")
        A("")
        seen: set[str] = set()
        for p in all_probes:
            for step, status in p.steps.items():
                key = f"{step}:{status}"
                if status in ("ok", "no-datastore") or key in seen:
                    continue
                seen.add(key)
                A(f"- **`{step}` → {status}** — `{p.dataset_id}` — {p.errors.get(step, '')[:180]}")
    A("")

    raised = [(p, s) for p in all_probes for s, v in p.steps.items() if v == "RAISED"]
    calls = sum(len(p.steps) for p in all_probes)
    A("## Garantía de que ninguna herramienta lanza excepción")
    A("")
    if raised:
        A(
            f"**{len(raised)} de {calls} llamadas lanzaron una excepción en vez de "
            "devolver un sobre de error. Esto es un defecto:**"
        )
        for p, s in raised[:10]:
            A(f"- `{s}` sobre `{p.dataset_id}`: {p.errors.get(s, '')[:200]}")
    else:
        A(
            f"**0 de {calls} llamadas lanzaron excepción.** Cada fallo llegó como "
            '`{"error": …, "hint": …}`, que es lo que permite al modelo reaccionar '
            "en vez de quedarse con un error opaco de protocolo."
        )
    A("")

    lat_all = [v for p in all_probes for v in p.timings_ms.values()]
    A("## Latencia agregada")
    A("")
    A(f"{len(lat_all)} llamadas: {percentiles(lat_all)}")
    A("")
    A("---")
    A("")
    A(f"Datos crudos: `sweep/out/{meta['stem']}.jsonl` (una línea por dataset).")
    A(
        "Generado por `sweep/stress_test.py`. Reproducir: "
        f"`uv run python sweep/stress_test.py --seed {meta['seed']} --total {meta['total']}`."
    )
    return "\n".join(L)


# ─── Main ────────────────────────────────────────────────────────────────────


async def run(args) -> int:
    rng = random.Random(args.seed)
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    ckan_keys = list(ckan.PORTALS)
    targets = ["national", *ckan_keys]
    if args.only:
        targets = [args.only]
    per = max(1, args.total // len(targets))

    print(f"Muestreando {per} de cada uno de {', '.join(targets)} (semilla {args.seed})…")

    items: list[tuple[str, dict]] = []
    if "national" in targets:
        items += [("national", i) for i in await sample_national(per, rng)]
    for key in ckan_keys:
        if key in targets:
            items += [(key, i) for i in await sample_ckan(key, per, rng)]

    print(f"Muestra obtenida: {len(items)}. Ejecutando herramientas…")

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    total = len(items)

    async def guarded(kind: str, item: dict) -> Probe:
        nonlocal done
        async with sem:
            if kind == "national":
                probe = await probe_national(item)
            else:
                probe = await probe_ckan(ckan.PORTALS[kind], item)
            await asyncio.sleep(args.delay)
            done += 1
            if done % 25 == 0 or done == total:
                print(f"  {done}/{total}…", flush=True)
            return probe

    probes = await asyncio.gather(*[guarded(k, i) for k, i in items])

    by_portal: dict[str, list[Probe]] = {}
    for probe in probes:
        by_portal.setdefault(probe.portal, []).append(probe)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"stress_{started:%Y-%m-%d_%H%M}"
    raw = OUT_DIR / f"{stem}.jsonl"
    with raw.open("w", encoding="utf-8") as fh:
        for probe in probes:
            fh.write(json.dumps(asdict(probe), ensure_ascii=False) + "\n")

    from colombian_open_data_mcp import __version__

    meta = {
        "started": started.strftime("%Y-%m-%d %H:%M UTC"),
        "elapsed_s": time.perf_counter() - t0,
        "seed": args.seed,
        "concurrency": args.concurrency,
        "total": args.total,
        "version": __version__,
        "stem": stem,
    }
    report = REPORT_DIR / f"PRUEBA_FUERZA_{started:%Y-%m-%d}.md"
    report.write_text(build_report(by_portal, meta), encoding="utf-8")

    await server._close_clients()
    print(f"\nCrudo:   {raw}")
    print(f"Informe: {report}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--total", type=int, default=450, help="datasets to probe (default 450)")
    ap.add_argument("--seed", type=int, default=None, help="random seed (default: time-based)")
    ap.add_argument(
        "--concurrency", type=int, default=4, help="parallel probes (default 4; be kind)"
    )
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between probes per worker")
    ap.add_argument("--only", choices=["national", *ckan.PORTALS], default=None)
    args = ap.parse_args()
    if args.seed is None:
        args.seed = int(time.time())
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
