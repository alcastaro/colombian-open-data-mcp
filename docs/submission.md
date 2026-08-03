# Submission dossier

Everything a directory reviewer, or the person filling in a submission portal, needs
in one place. Written against the requirements published at
<https://claude.com/docs/connectors/building/submission>,
<https://claude.com/docs/connectors/building/review-criteria>,
<https://developers.openai.com/plugins/app-guidelines> and
<https://developers.openai.com/plugins/deploy/submission>, all read 2026-08-29.

Nothing here is aspirational: every claim is either measured or checkable from this
repository.

---

## What this server is

**colombian-open-data-mcp** connects an AI assistant to Colombia's open government
data — the national portal `datos.gov.co` plus the territorial portals of Bogotá,
Cali, Valle del Cauca and Cartagena. It exposes 24 read-only tools that search
catalogues, describe datasets and return rows, with filtering and aggregation
executed by the portals themselves.

- **Read-only.** There is no tool that writes, creates, updates or deletes anything,
  anywhere. All 24 declare `readOnlyHint: true`.
- **Authless.** No accounts, no credentials, no user data. The data it reads is
  published by Colombian public institutions for anyone to read.
- **Stateless.** Nothing is cached and nothing is written to disk.

## Publisher

Observatorio Latinoamericano de Desarrollo Sostenible (OLDS) — <https://olds2030.org>

The connector's purpose and the publisher's purpose are the same one: OLDS monitors
sustainable-development indicators using public data, and this is the software that
reads them.

## Data handling — stated plainly

The underlying APIs are **third-party APIs the publisher does not control**: they are
operated by Colombian government institutions. This is declared rather than glossed,
because a reviewer will ask.

What makes the intermediation legitimate:

1. The data is **published for public reuse by law** — Colombia's Ley 1712 de 2014 on
   transparency and access to public information — and each portal exposes a
   documented public API for exactly that purpose.
2. **Nothing is redistributed.** The server reads live and returns the answer. It
   stores no copy, keeps no cache, writes nothing to disk. Persisting these datasets
   would make the operator a *responsable del tratamiento* under Ley 1581 de 2012 for
   any of them containing identifiable people, and that is precisely why there is no
   cache.
3. **No credentials of ours are involved.** The optional `SOCRATA_APP_TOKEN` only
   raises an anonymous rate limit and is never required.

No personal health data. No sponsored content. No financial transactions. No AI
generation of images, video or audio.

## Privacy policy

- English: [`docs/PRIVACY.md`](PRIVACY.md)
- Español: [`docs/PRIVACIDAD.md`](PRIVACIDAD.md)

These must be served from a live HTTPS URL by the publish date. A missing or
incomplete privacy policy is an immediate rejection.

## Documentation

- [`README.md`](../README.md) — English
- [`README.es.md`](../README.es.md) — Español
- [`SECURITY.md`](../SECURITY.md) — the security model, including what the server
  reaches over the network and what constrains it

---

## Example prompts

Each exercises a different tool family, and each has been run against the live
portals. They are written the way a Colombian user would actually ask.

> ¿Cuáles son las diez entidades que más conjuntos de datos publican en
> datos.gov.co?

*Exercises `list_owners` on the national Socrata portal. Returns publishers with
dataset counts — a catalogue question answered without touching any data.*

> Agrupa el dataset de contratación pública por departamento y suma el valor total
> de los contratos.

*Exercises `aggregate_dataset`. The GROUP BY and the SUM run inside Socrata; only the
rolled-up rows travel. This is the tool that shows the server is not downloading and
summing in the model's context.*

> Cuenta cuántos parques hay por localidad en la capa de espacio público de Bogotá,
> y que lo agrupe el servidor.

*Exercises `city_esri_service_info` then `city_esri_aggregate`. Bogotá's CKAN
DataStore cannot GROUP BY at all, so this is the one territorial rollup computed
remotely — on the ArcGIS service the dataset is published as.*

> Ese conjunto de Bogotá no aparece en el DataStore. Lee el archivo publicado y
> muéstrame las primeras filas y sus columnas.

*Exercises `city_read_resource_file`. Roughly half of Bogotá's catalogue has no
queryable table; this reads the published CSV/XLSX/JSON directly, under a byte cap,
and discards the bytes after answering.*

> Compara qué entidades publican más datos abiertos en Cali y en Cartagena.

*Exercises `city_list_organizations` across two portals through the `city` parameter
— the one tool family that covers four catalogues.*

---

## Installing it today, without any directory

These paths work now and depend on nobody's approval. They are the primary
distribution channel and cost the publisher nothing, because there is no server of
ours in the middle: each user runs the package on their own machine against the
public portals.

**Claude Code**

```bash
claude mcp add colombia -- uvx colombian-open-data-mcp
```

**Claude Desktop, Cursor, VS Code and other stdio clients** — see
[`docs/clients.md`](clients.md).

**Gemini CLI and Google Antigravity.** Antigravity's MCP Store is curated by Google
with no public self-submission route, but manual configuration needs no permission
from anyone. Add to `~/.gemini/config/mcp_config.json` for every workspace, or
`.agents/mcp_config.json` for one:

```json
{
  "mcpServers": {
    "colombia": {
      "command": "uvx",
      "args": ["colombian-open-data-mcp"]
    }
  }
}
```

---

## For a remote deployment

Required only by the Claude Connectors Directory and ChatGPT Apps; the local channel
above needs none of it.

- **Transport:** streamable HTTP. The MCP Python SDK v2 supports it directly —
  `mcp.run(transport="streamable-http")` — so this is a deployment change, not a code
  change.
- **Authentication:** `none`. Anthropic lists authless servers as a supported type,
  and this server has nothing to authenticate: no accounts, no per-user data, no
  credentials.
- **Anthropic's outbound traffic** originates from `160.79.104.0/21`, if a firewall
  needs to allow it.
- **Testing before submission:** exercise every tool through the MCP Inspector and as
  a custom connector in Claude. There is no test account to provide, because there is
  nothing to log in to — that is worth stating in the submission rather than leaving
  blank.

## Notes specific to the OpenAI plugin directory

OpenAI's guidelines are stricter than Anthropic's on the one question that matters
here, and the difference is worth stating up front rather than discovering in review.
They ask developers to avoid *"unofficial connectors to third-party services,
including pass-through intermediary software layers"* and not to *"integrate with
third-party APIs without proper authorization"* — and, unlike Anthropic, they publish
no exception for public or government open data.

The authorization argument for this server is that Colombian portals publish
documented public APIs precisely so that anyone may query them, under a law that
mandates reuse. That is a real argument, not a loophole, but it is ours to make
rather than a written carve-out.

Two consequences:

- **Submit to Claude first.** Anthropic's criteria contemplate this case in writing
  ("APIs you legitimately proxy", and a form field for third-party APIs you do not
  control). An accepted Claude listing is the strongest available evidence for an
  OpenAI submission. The reverse does not work.
- **Verify the organisation identity first.** *"Every public submission must use a
  verified developer or business identity in the OpenAI Platform."* The submitter also
  needs "Apps Management" write access in the organisation's role. That is
  paperwork, and it gates everything else.

Also required by OpenAI and already satisfied or not applicable: a production HTTPS
MCP server URL that a reviewer connects to live; human-readable, specific tool names;
no personal data, secrets or internal identifiers in tool responses; a published
privacy policy covering data categories, purposes, recipients, retention and user
controls; and country availability. Test credentials are required only *"if your MCP
server requires authentication"*, which this one does not.

## Verification anyone can repeat

```bash
uv sync --group dev --extra dev
uv run pytest                                    # 536 hermetic tests, 85% coverage floor
RUN_LIVE_TESTS=1 uv run pytest tests/test_live.py -v   # 34 live tests, five portals
uv run python sweep/stress_test.py --total 600 --seed 60606
```

The last one runs the real tools against a random sample of all five catalogues and
writes a report. The most recent run: **zero of 1,428 calls raised an exception** —
every failure arrived as an error envelope the model can act on, which is the
functional-quality bar the review criteria set.
