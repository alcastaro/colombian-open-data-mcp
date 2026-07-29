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
    uv run python sweep/stress_test.py                    # 300 datasets, 50/50
    uv run python sweep/stress_test.py --total 600
    uv run python sweep/stress_test.py --seed 42 --concurrency 4
    uv run python sweep/stress_test.py --only bogota

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

from colombian_open_data_mcp import ckan, server, socrata  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"
REPORT_DIR = ROOT / "internal" / "reportes"

# Catalogue sizes, refreshed at run time. These are only fallbacks for the
# random-offset draw if the count call itself fails.
FALLBACK_NATIONAL = 8391
FALLBACK_BOGOTA = 1917

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

    meta = await timed(p, "get_dataset", server.get_dataset(id=p.dataset_id))
    columns: list[dict] = []
    if isinstance(meta, dict) and "error" not in meta:
        columns = meta.get("columns") or []
        p.columns = len(columns)
        p.n_resources = 1
        p.n_queryable = 1

    preview = await timed(
        p, "download_dataset_preview", server.download_dataset_preview(id=p.dataset_id, rows=2)
    )
    if isinstance(preview, dict) and "error" not in preview:
        p.row_sample_ok = bool(preview.get("rows"))

    agg = await timed(
        p,
        "aggregate_dataset",
        server.aggregate_dataset(
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
                server.filter_dataset(id=p.dataset_id, columns=[col], limit=2),
            )
    return p


# ─── Bogotá (CKAN) ───────────────────────────────────────────────────────────


async def probe_bogota(pkg: dict) -> Probe:
    p = Probe(
        portal="datosabiertos.bogota.gov.co",
        dataset_id=pkg.get("name") or pkg.get("id", ""),
        title=(pkg.get("title") or "")[:90],
    )

    meta = await timed(p, "bogota_get_dataset", server.bogota_get_dataset(id=p.dataset_id))
    if not isinstance(meta, dict) or "error" in meta:
        return p

    resources = meta.get("resources") or []
    p.n_resources = len(resources)
    p.n_queryable = sum(1 for r in resources if r.get("queryable"))
    p.formats = [r.get("format") or "?" for r in resources]

    target = next((r for r in resources if r.get("queryable")), None)
    if target is None:
        # Not a failure of the server — the portal never pushed this one into
        # the DataStore. Recorded distinctly so it does not pollute error rates.
        p.steps["bogota_resource_preview"] = "no-datastore"
        return p

    prev = await timed(
        p,
        "bogota_resource_preview",
        server.bogota_resource_preview(resource_id=target["id"], rows=2),
    )
    if isinstance(prev, dict) and "error" not in prev:
        p.row_sample_ok = bool(prev.get("rows"))
        p.rows_available = prev.get("total_rows_matching")
        p.columns = len(prev.get("fields") or [])

        fields = [f["name"] for f in (prev.get("fields") or []) if f.get("name") != "_id"]
        if fields:
            await timed(
                p,
                "bogota_filter_resource",
                server.bogota_filter_resource(
                    resource_id=target["id"], columns=fields[:2], limit=2
                ),
            )
    return p


# ─── Sampling ────────────────────────────────────────────────────────────────


async def catalogue_size_national() -> int:
    try:
        raw = await server._client.catalog_search(limit=1)
        return int(raw.get("resultSetSize") or FALLBACK_NATIONAL)
    except Exception:
        return FALLBACK_NATIONAL


async def catalogue_size_bogota() -> int:
    try:
        body = await server._bogota.package_search(rows=1)
        return int(body.get("count") or FALLBACK_BOGOTA)
    except Exception:
        return FALLBACK_BOGOTA


async def sample_national(n: int, rng: random.Random) -> list[dict]:
    """Draw n datasets from random offsets across the whole catalogue.

    Page size is 25 rather than 1 so a sample of 150 costs 6 requests, not 150.
    Offsets are drawn without replacement at page granularity.
    """
    size = await catalogue_size_national()
    page = 25
    pages = max(1, size // page)
    offsets = rng.sample(range(pages), k=min(pages, (n // page) + 2))
    out: list[dict] = []
    for off in offsets:
        try:
            raw = await server._client.catalog_search(limit=page, offset=off * page)
        except socrata.SocrataError:
            continue
        out.extend(raw.get("results") or [])
        await asyncio.sleep(0.25)
        if len(out) >= n:
            break
    rng.shuffle(out)
    return out[:n]


async def sample_bogota(n: int, rng: random.Random) -> list[dict]:
    size = await catalogue_size_bogota()
    page = 25
    pages = max(1, size // page)
    offsets = rng.sample(range(pages), k=min(pages, (n // page) + 2))
    out: list[dict] = []
    for off in offsets:
        try:
            body = await server._bogota.package_search(rows=page, start=off * page)
        except ckan.CkanError:
            continue
        out.extend(body.get("results") or [])
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


def build_report(nat: list[Probe], bog: list[Probe], meta: dict) -> str:
    all_probes = nat + bog
    L: list[str] = []
    A = L.append

    A("# Informe de prueba de fuerza — dos portales colombianos")
    A("")
    A(f"**Ejecutado:** {meta['started']}  ")
    A(f"**Duración:** {meta['elapsed_s']:.0f} s  ")
    A(f"**Semilla:** `{meta['seed']}` (reproducible con `--seed {meta['seed']}`)  ")
    A(f"**Concurrencia:** {meta['concurrency']}  ")
    A(f"**Versión del servidor:** {meta['version']}")
    A("")
    A(
        f"Se sometieron **{len(all_probes)} conjuntos de datos** elegidos al azar "
        f"({len(nat)} de `datos.gov.co`, {len(bog)} de Bogotá) a las herramientas "
        "reales del MCP — no a los clientes HTTP internos, sino a las mismas "
        "funciones que invoca un modelo. Todo lo que sigue es lo que un asistente "
        "recibiría de verdad."
    )
    A("")
    A(
        "La muestra se toma en desplazamientos aleatorios a lo largo de todo el "
        "catálogo, no de los primeros N resultados. La distinción no es cosmética: "
        "los primeros 100 datasets de Bogotá son casi todos geoespaciales, y "
        "medir ahí daba una cobertura de DataStore del 9% en vez del 43% real."
    )
    A("")

    # ── Headline
    nat_ok = sum(1 for p in nat if p.row_sample_ok)
    bog_ok = sum(1 for p in bog if p.row_sample_ok)
    A("## Resumen")
    A("")
    A("| Portal | Muestra | Devolvió filas reales | Tasa |")
    A("|---|---|---|---|")
    if nat:
        A(f"| `datos.gov.co` (Socrata) | {len(nat)} | {nat_ok} | **{pct(nat_ok, len(nat))}** |")
    if bog:
        A(f"| Bogotá (CKAN) | {len(bog)} | {bog_ok} | **{pct(bog_ok, len(bog))}** |")
    A("")
    A(
        "«Devolvió filas reales» es la prueba dura: el MCP entregó datos que el "
        "modelo puede leer, no solo metadatos."
    )
    A("")

    # ── Per-tool
    if nat:
        A("## Portal nacional — `datos.gov.co`")
        A("")
        A(
            step_table(
                nat,
                ["get_dataset", "download_dataset_preview", "aggregate_dataset", "filter_dataset"],
            )
        )
        A("")
        sizes = [p.rows_available for p in nat if p.rows_available is not None]
        if sizes:
            sizes.sort()
            A(
                f"Tamaño de los datasets consultados: mediana **{statistics.median(sizes):,.0f} filas**, "
                f"máximo **{sizes[-1]:,} filas**. Ninguna de esas filas viajó por la red: "
                "`count(*)` lo resolvió Socrata en su servidor."
            )
            A("")

    if bog:
        A("## Bogotá — `datosabiertos.bogota.gov.co`")
        A("")
        A(
            step_table(
                bog, ["bogota_get_dataset", "bogota_resource_preview", "bogota_filter_resource"]
            )
        )
        A("")

        with_ds = sum(1 for p in bog if p.n_queryable > 0)
        res_total = sum(p.n_resources for p in bog)
        res_query = sum(p.n_queryable for p in bog)
        A("### Cobertura de DataStore")
        A("")
        A(
            "Qué porción del catálogo fue efectivamente volcada a la base de datos "
            "de CKAN, y por lo tanto se puede leer fila por fila:"
        )
        A("")
        A("| Medida | Valor |")
        A("|---|---|")
        A(
            f"| Datasets con al menos un recurso consultable | {with_ds} / {len(bog)} — **{pct(with_ds, len(bog))}** |"
        )
        A(
            f"| Recursos individuales consultables | {res_query} / {res_total} — **{pct(res_query, res_total)}** |"
        )
        A("")

        fmt = Counter(f for p in bog for f in p.formats)
        if fmt:
            tab = sum(v for k, v in fmt.items() if k in TABULAR)
            svc = sum(v for k, v in fmt.items() if k in SERVICE)
            other = sum(fmt.values()) - tab - svc
            A("### De qué está hecho el resto")
            A("")
            A("| Formato | Recursos | Naturaleza |")
            A("|---|---|---|")
            for k, v in fmt.most_common(14):
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
                f"De {sum(fmt.values())} recursos: {tab} tabulares, **{svc} son servicios "
                f"consultables** (ESRI REST / WFS / WMS — tienen `?query=`, no hay que "
                f"descargarlos), y {other} son archivos geoespaciales o estáticos."
            )
            A("")
            A(
                "Ese número de servicios es la vía de ampliación con mejor relación "
                "esfuerzo/beneficio: son APIs, y consultarlas no reintroduce ni "
                "descarga de archivos ni superficie de SSRF."
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
        for k, v in errs.most_common(20):
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
    A("## Garantía de que ninguna herramienta lanza excepción")
    A("")
    if raised:
        A(
            f"**{len(raised)} llamadas lanzaron una excepción en vez de devolver un sobre de error.** Esto es un defecto:"
        )
        for p, s in raised[:10]:
            A(f"- `{s}` sobre `{p.dataset_id}`: {p.errors.get(s, '')[:200]}")
    else:
        A(
            f"Ninguna de las {sum(len(p.steps) for p in all_probes)} llamadas lanzó "
            'excepción. Cada fallo llegó como `{"error": …, "hint": …}`, que es '
            "lo que permite al modelo reaccionar en vez de quedarse con un error "
            "opaco de protocolo."
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
        f"Generado por `sweep/stress_test.py`. Reproducir: `uv run python sweep/stress_test.py --seed {meta['seed']} --total {meta['total']}`."
    )
    return "\n".join(L)


# ─── Main ────────────────────────────────────────────────────────────────────


async def run(args) -> int:
    rng = random.Random(args.seed)
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    want_nat = 0 if args.only == "bogota" else args.total // (1 if args.only == "national" else 2)
    want_bog = 0 if args.only == "national" else args.total - want_nat

    print(f"Muestreando {want_nat} de datos.gov.co y {want_bog} de Bogotá (semilla {args.seed})…")
    nat_items = await sample_national(want_nat, rng) if want_nat else []
    bog_items = await sample_bogota(want_bog, rng) if want_bog else []
    print(f"Muestra obtenida: {len(nat_items)} + {len(bog_items)}. Ejecutando herramientas…")

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    total = len(nat_items) + len(bog_items)

    async def guarded(fn, item):
        nonlocal done
        async with sem:
            p = await fn(item)
            await asyncio.sleep(args.delay)
            done += 1
            if done % 25 == 0 or done == total:
                print(f"  {done}/{total}…", flush=True)
            return p

    probes = await asyncio.gather(
        *[guarded(probe_national, i) for i in nat_items],
        *[guarded(probe_bogota, i) for i in bog_items],
    )
    nat = [p for p in probes if p.portal == "datos.gov.co"]
    bog = [p for p in probes if p.portal != "datos.gov.co"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"stress_{started:%Y-%m-%d_%H%M}"
    raw = OUT_DIR / f"{stem}.jsonl"
    with raw.open("w", encoding="utf-8") as fh:
        for p in probes:
            fh.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")

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
    report.write_text(build_report(nat, bog, meta), encoding="utf-8")

    await server._close_clients()
    print(f"\nCrudo:   {raw}")
    print(f"Informe: {report}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--total", type=int, default=300, help="datasets to probe (default 300)")
    ap.add_argument("--seed", type=int, default=None, help="random seed (default: time-based)")
    ap.add_argument(
        "--concurrency", type=int, default=4, help="parallel probes (default 4; be kind)"
    )
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between probes per worker")
    ap.add_argument("--only", choices=["national", "bogota"], default=None)
    args = ap.parse_args()
    if args.seed is None:
        args.seed = int(time.time())
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
