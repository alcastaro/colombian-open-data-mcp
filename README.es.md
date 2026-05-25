<!-- mcp-name: io.github.alcastaro/colombian-open-data-mcp -->

**[English](README.md) · [Español](README.es.md)**

---

# colombian-open-data-mcp

**Servidor MCP para [datos.gov.co](https://www.datos.gov.co) — el portal de datos abiertos del Gobierno de Colombia (10,000+ datasets, Socrata SODA).**

Primer servidor MCP en el [registro oficial](https://registry.modelcontextprotocol.io) para los datos abiertos colombianos. Conecta cualquier asistente de IA compatible con MCP (Claude Desktop, Claude Code, Cursor, Gemini CLI, ChatGPT Desktop) directamente al catálogo y a los datos en vivo — incluyendo queries SoQL server-side, agregaciones y filtros que se ejecutan en la base de Socrata, no en el contexto del LLM.

---

## Por qué importa

`datos.gov.co` corre **Socrata**, no CKAN. Eso lo hace distinto de la mayoría de portales LatAm (Argentina, Chile, México, Uruguay, RD todos corren CKAN). Socrata tiene un lenguaje de queries real — SoQL — que permite hacer `WHERE`, `GROUP BY`, `count()`, `sum()` directo contra los datos sin descargarlos. Este MCP expone ese poder como tools tipadas para que el LLM no necesite saber SQL.

## Tools (12)

| Tool | Qué hace |
|---|---|
| `search_datasets` | Búsqueda en catálogo por keyword, categoría, tag. |
| `get_dataset` | Metadatos completos: columnas, tipos, owner, licencia, URL. |
| `list_recent_datasets` | Datasets actualizados más recientemente. |
| `list_categories` | Categorías top-level del portal. |
| `list_tags` | Todos los tags del portal. |
| `list_owners` | Entidades publicadoras con conteo de datasets. |
| `autocomplete` | Resuelve nombres parciales de datasets / tags / categorías / owners. |
| `get_site_stats` | Totales del portal. |
| `download_dataset_preview` | Primeras N filas de un dataset (directo desde Socrata, sin cache). |
| `filter_dataset` | WHERE / SELECT / ORDER BY tipado contra cualquier dataset. |
| `aggregate_dataset` | GROUP BY + count / sum / avg / median / min / max / stddev tipado. |
| `query_dataset_soql` | Escape hatch para power-users: SoQL crudo contra cualquier dataset. |

## Instalación

```bash
uvx --from git+https://github.com/alcastaro/colombian-open-data-mcp.git colombian-open-data-mcp
```

(Publicación a PyPI después del release v0.1.0.)

## Config Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) o `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "colombian-open-data": {
      "command": "/Users/TU_USUARIO/.local/bin/uvx",
      "args": [
        "--from",
        "git+https://github.com/alcastaro/colombian-open-data-mcp.git",
        "colombian-open-data-mcp"
      ]
    }
  }
}
```

Cmd+Q (macOS) / Quit desde bandeja sistema (Windows) y reabrir.

## Opcional — Socrata App Token

La API de Socrata es abierta sin auth pero con rate limits. Para límites más altos, registrate en https://dev.socrata.com/register y exportá:

```bash
export SOCRATA_APP_TOKEN="tu-token"
```

El MCP lee `SOCRATA_APP_TOKEN` automáticamente al arrancar.

## Arquitectura

```
src/colombian_open_data_mcp/
├── server.py        Entry FastMCP + definiciones de tools
├── socrata.py       Cliente Socrata async (catalog + view + resource APIs)
└── soql.py          SoQL builder seguro + validador de queries crudas
```

### Defensa en profundidad

- **Allowlist de identificadores** + denylist de substrings de comentarios / quiebres de statement en cada field name pasado a SoQL.
- **Escapado de literales** que duplica comillas simples correctamente.
- **Validador de SoQL crudo** rechaza keywords DDL/DML y queries multi-statement antes de enviar.
- **System trust store** vía `truststore` para portales con chains TLS incompletos.
- **Logging solo a stderr** per spec MCP (stdout es el stream del protocolo).

## Roadmap

Este MCP es **single-country, Socrata-especializado**. Para queries unificadas contra múltiples portales LatAm ver [`opendata-latam-mcp`](https://github.com/alcastaro/opendata-latam-mcp) (cuando se publique) que integrará este mismo cliente Socrata como adapter de Colombia.

## Créditos

Construido por [@alcastaro](https://github.com/alcastaro). Companion de [`dominican-open-data-mcp`](https://github.com/alcastaro/datos.gob.do-MCP-server) (CKAN-based, single-country) y del futuro [`opendata-latam-mcp`](https://github.com/alcastaro/opendata-latam-mcp) (multi-país).

Datos de datos.gov.co publicados por instituciones del Gobierno de Colombia y administrados por MinTIC.

## Licencia

MIT para el código fuente. Datos accedidos a través de este MCP están sujetos a los términos de cada publicador. Ver [LICENSE](LICENSE).
