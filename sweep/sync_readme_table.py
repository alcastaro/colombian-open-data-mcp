#!/usr/bin/env python3
"""Copy the stress report's Resumen table into both READMEs.

This exists because the numbers in this repo have been wrong before, and every
time it was the same mechanism: a figure measured once, retyped by hand into
prose, and left behind when the measurement was redone. A 43% coverage claim, a
"+7% unlock", a 13% in a table — all transcription, none of them lies anyone
told on purpose.

So the READMEs carry a marker instead of a table, and this script fills it from
`internal/reportes/PRUEBA_FUERZA_<fecha>.md`. Run it after a stress run. The
numbers a reader sees are then the numbers the harness printed, by construction
rather than by diligence.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- COVERAGE TABLE -->"


def latest_report() -> Path:
    reports = sorted((ROOT / "internal" / "reportes").glob("PRUEBA_FUERZA_*.md"))
    if not reports:
        raise SystemExit("no stress report found — run sweep/stress_test.py first")
    return reports[-1]


def resumen_table(report: str) -> str:
    """The first Markdown table under `## Resumen`, and nothing else.

    "Every line starting with a pipe" was the obvious reading and the wrong
    one: it swept up the per-portal tool tables further down the report and
    pasted them into the README under the wrong heading. Stop at the first line
    that is not a table row.
    """
    block = report.split("## Resumen", 1)[1]
    rows: list[str] = []
    for line in block.splitlines():
        if line.startswith("|"):
            rows.append(line)
        elif rows:
            break
    if not rows:
        raise SystemExit("the report has no Resumen table")
    return "\n".join(rows)


# The report is written in Spanish, and the English README should not inherit
# its column headers. Only the header row is translated — every figure below it
# is copied verbatim, which is the whole point of this script.
HEADERS_EN = "| Portal | Platform | Sample | Returned real rows | Rate | DataStore | ESRI | File |"


def main() -> int:
    report_path = latest_report()
    table = resumen_table(report_path.read_text(encoding="utf-8"))
    english = HEADERS_EN + table[table.index("\n") :]
    for name, body in (("README.md", english), ("README.es.md", table)):
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        if MARKER in text:
            text = text.replace(MARKER, body, 1)
        else:
            # Already filled: replace whatever table sits where this one went.
            text = re.sub(
                r"\| Portal \| (?:Plataforma|Platform) \|.*?(?=\n\n)",
                body.replace("\\", "\\\\"),
                text,
                count=1,
                flags=re.DOTALL,
            )
        path.write_text(text, encoding="utf-8")
        print(f"updated {name}")
    print(f"source: {report_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
