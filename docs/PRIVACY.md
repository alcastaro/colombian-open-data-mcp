# Privacy note

**[English](PRIVACY.md) · [Español](PRIVACIDAD.md)**

Short version: this server collects nothing, stores nothing, and sends nothing
anywhere except to the Colombian government portals it queries — and, for
published files and map services, to the addresses those portals themselves
hand out.

## What runs where

`colombian-open-data-mcp` is a local stdio process. Your MCP client starts it on
your machine, talks to it over standard input and output, and stops it when the
client exits. There is no service of ours in the path and no account to create.

## What leaves your machine

Only the query itself. Most of it goes to a fixed set of hosts written into the
source:

| Host | Why |
|---|---|
| `api.us.socrata.com` | Socrata's cross-portal catalogue, scoped to `www.datos.gov.co` for dataset discovery |
| `www.datos.gov.co` | National dataset metadata and SoQL data queries |
| `datosabiertos.bogota.gov.co` | Bogotá's CKAN catalogue and DataStore queries |
| `datos.cali.gov.co` | Cali's CKAN catalogue and DataStore queries |
| `datosabiertos.valledelcauca.gov.co` | Valle del Cauca's CKAN catalogue and DataStore queries |
| `datosabiertos.cartagena.gov.co` | Cartagena's CKAN catalogue and DataStore queries |

**Two tool families reach further, and this changed in 0.4.** Colombian portals
routinely publish a dataset not as a queryable table but as an ArcGIS map
service or as a file — a CSV, an Excel workbook, a JSON document — sitting on
some other address. Reading those is the difference between covering a quarter
of Bogotá's catalogue and covering nearly all of it, so `city_esri_*` and
`city_read_resource_file` follow the address the catalogue gives them:
`mapas.bogota.gov.co`, `services*.arcgis.com`, a ministry's own domain, or
occasionally a storage bucket.

Two things bound that reach, and both are checkable in the source:

- **No tool accepts a URL from you or from the model.** Every one takes a
  resource identifier and looks the address up through the portal's own
  `resource_show`. The set of reachable hosts is whatever the Colombian
  catalogues point at — not whatever a request asks for.
- **`netguard.py` enforces a network policy on every request, including each
  redirect hop.** By default the scheme must be `http` or `https` and every
  address the hostname resolves to must be globally routable, which refuses
  loopback, the private ranges and cloud instance metadata at
  `169.254.169.254`. Setting `CO_MCP_NETGUARD=strict` narrows it further to the
  five portals plus `*.gov.co` and `*.arcgis.com`, at the cost of the roughly
  12% of files hosted elsewhere. [`SECURITY.md`](../SECURITY.md) documents both
  modes and the one residual limit, a DNS-rebinding window.

Those requests carry what any HTTP client sends: your IP address, a
`User-Agent` identifying this software and its version, and the query
parameters. The portals' own logging and privacy practices are theirs, not
ours — they are Colombian government infrastructure and their terms govern.
The same is true of any third-party host a portal chose to publish a file on.

## What is stored

Nothing. There is no cache directory, no database, no downloaded file, no
history. Files read by `city_read_resource_file` are parsed in memory under a
size cap and discarded once the answer is returned. Results exist only in the
reply to the tool call, which lives wherever your MCP client keeps the
conversation.

## Logging

The server logs to standard error only — a startup line, the tool count, and
any fatal error. It never logs query contents or results. Standard error goes
wherever your MCP client sends it, typically its own log file on your machine.

## Credentials

The one optional secret is `SOCRATA_APP_TOKEN`, which raises `datos.gov.co`'s
anonymous rate limit. If set, it is read once at startup, sent as an
`X-App-Token` header by the Socrata client only, and never written to a log or
a file. The CKAN, ArcGIS and file-reading clients are separate objects that
never see it, so it is never sent to the four territorial portals, to a map
service, or to a file host. The server works without it.

## The data itself

Everything this server returns is public open government data published by
Colombian institutions under their own licences. It does not access anything
that requires authentication, and it cannot: every call is an unauthenticated
GET.

That said, some open datasets contain information about identifiable people.
That is a decision made by the publishing institution, not by this software.
If you process such data, the obligations under Colombia's Ley 1581 de 2012 on
personal data protection are yours, and they apply regardless of the data
having been published openly. Keeping no copy of anything is how this server
avoids taking on those obligations itself, and it is a deliberate design
decision rather than a missing feature.

## Changes

Any change to the hosts contacted or the data retained will appear in
[CHANGELOG.md](../CHANGELOG.md) under the release that makes it.
