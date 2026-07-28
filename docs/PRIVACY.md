# Privacy note

**[English](PRIVACY.md) · [Español](PRIVACIDAD.md)**

Short version: this server collects nothing, stores nothing, and sends nothing
anywhere except to the two Colombian government portals it queries.

## What runs where

`colombian-open-data-mcp` is a local stdio process. Your MCP client starts it on
your machine, talks to it over standard input and output, and stops it when the
client exits. There is no service of ours in the path and no account to create.

## What leaves your machine

Only the query itself, and only to one of three fixed hosts compiled into the
source:

| Host | Why |
|---|---|
| `api.us.socrata.com` | Socrata's cross-portal catalogue, scoped to `www.datos.gov.co` for dataset discovery |
| `www.datos.gov.co` | Dataset metadata and SoQL data queries |
| `datosabiertos.bogota.gov.co` | Bogotá's CKAN catalogue and DataStore queries |

No tool takes a URL, so the server cannot be directed to a fourth host.

Those requests carry what any HTTP client sends: your IP address, a
`User-Agent` identifying this software and its version, and the query
parameters. The portals' own logging and privacy practices are theirs, not
ours — they are Colombian government infrastructure and their terms govern.

## What is stored

Nothing. There is no cache directory, no database, no downloaded file, no
history. Results exist only in the reply to the tool call, which lives wherever
your MCP client keeps the conversation.

## Logging

The server logs to standard error only — a startup line, the tool count, and
any fatal error. It never logs query contents or results. Standard error goes
wherever your MCP client sends it, typically its own log file on your machine.

## Credentials

The one optional secret is `SOCRATA_APP_TOKEN`, which raises `datos.gov.co`'s
anonymous rate limit. If set, it is read once at startup, sent as an
`X-App-Token` header to Socrata only, and never written to a log or a file. It
is never sent to the Bogotá portal. The server works without it.

## The data itself

Everything this server returns is public open government data published by
Colombian institutions under their own licences. It does not access anything
that requires authentication, and it cannot: every call is an unauthenticated
GET.

That said, some open datasets contain information about identifiable people.
That is a decision made by the publishing institution, not by this software.
If you process such data, the obligations under Colombia's Ley 1581 de 2012 on
personal data protection are yours, and they apply regardless of the data
having been published openly.

## Changes

Any change to the hosts contacted or the data retained will appear in
[CHANGELOG.md](../CHANGELOG.md) under the release that makes it.
