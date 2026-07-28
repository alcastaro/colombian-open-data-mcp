<!-- mcp-name: io.github.alcastaro/colombian-open-data-mcp -->

**[English](README.md) · [Español](README.es.md)**

---

# colombian-open-data-mcp

**Servidor MCP para los datos abiertos del Estado colombiano — el portal
nacional [datos.gov.co](https://www.datos.gov.co) (Socrata, más de 10.000
conjuntos de datos) y el portal distrital de Bogotá
[datosabiertos.bogota.gov.co](https://datosabiertos.bogota.gov.co)
(CKAN, ~1.900 conjuntos).**

El primer servidor MCP de datos abiertos de Colombia que se instala y se ejecuta
**en su propia máquina** — sin pasarela, sin intermediario, sin cuenta. Conecta
cualquier asistente compatible con MCP (Claude Desktop, Claude Code, Cursor, VS
Code Copilot, Gemini CLI) directamente con los catálogos y con los datos vivos:
el filtrado y la agregación los ejecutan los portales, no el modelo.

**20 herramientas · 212 pruebas herméticas · MIT**

---

## Por qué dos portales, y por qué no son intercambiables

El portal nacional corre sobre **Socrata**, lo que lo hace atípico en América
Latina — Argentina, Chile, México, Uruguay y República Dominicana corren todos
sobre CKAN. Socrata trae un lenguaje de consulta de verdad, **SoQL**, de modo
que `WHERE`, `GROUP BY`, `count()` y `sum()` se ejecutan en el servidor y solo
viajan las filas ya agregadas.

El portal de Bogotá corre sobre **CKAN 2.10.4**. Su DataStore admite filtrado
tipado, pero **no** expone `datastore_search_sql` — verificado contra la API
real: la acción no está registrada y, además, un WAF bloquea la forma GET de esa
ruta.

Esa diferencia es real, así que este servidor la expone en lugar de disimularla.
Hay un `aggregate_dataset` para el portal nacional y **ningún equivalente para
Bogotá**, porque ofrecerlo sería prometer algo que el portal no puede hacer. Una
prueba en vivo verifica esa ausencia; si Bogotá llegara a habilitar SQL, la
prueba falla y avisa.

Por la misma razón las dos familias son herramientas separadas y no una sola con
un parámetro `portal`: los identificadores de Socrata son códigos 4x4
(`abcd-1234`), los de CKAN son UUID o slugs, y un parámetro compartido tendría
que ramificar su validación — y la validación de identificadores es justamente
la defensa contra la inyección en la URL.

## Herramientas

### Portal nacional — `datos.gov.co` (12)

| Herramienta | Qué hace |
|---|---|
| `search_datasets` | Búsqueda en el catálogo por palabra clave, categoría o etiqueta. |
| `get_dataset` | Metadatos completos: columnas, tipos, entidad, licencia, URL. |
| `list_recent_datasets` | Conjuntos actualizados más recientemente. |
| `list_categories` | Categorías de primer nivel del portal. |
| `list_tags` | Todas las etiquetas del portal. |
| `list_owners` | Entidades publicadoras con su número de conjuntos. |
| `autocomplete` | Resuelve un nombre parcial a un valor real (dataset, etiqueta, categoría, entidad). |
| `get_site_stats` | Totales del portal. |
| `download_dataset_preview` | Primeras N filas, directo desde Socrata. |
| `filter_dataset` | WHERE / SELECT / ORDER BY tipados. |
| `aggregate_dataset` | GROUP BY tipado + count / sum / avg / median / min / max / stddev. |
| `query_dataset_soql` | Escotilla para usuarios avanzados: SoQL crudo, solo lectura, validado. |

### Bogotá — `datosabiertos.bogota.gov.co` (8)

| Herramienta | Qué hace |
|---|---|
| `bogota_search_datasets` | Búsqueda en el catálogo, filtrable por entidad, grupo o etiqueta. |
| `bogota_get_dataset` | Metadatos completos y todos los recursos, cada uno marcado `queryable`. |
| `bogota_list_organizations` | Entidades distritales que publican, con su número de conjuntos. |
| `bogota_list_groups` | Grupos temáticos. |
| `bogota_list_tags` | Etiquetas del portal (~3.200). |
| `bogota_get_site_stats` | Totales del portal, y qué puede y qué no puede hacer su DataStore. |
| `bogota_resource_preview` | Primeras N filas de un recurso en DataStore, con los tipos de columna. |
| `bogota_filter_resource` | Filtrado, proyección y ordenamiento tipados del lado del servidor. |

## Qué puede y qué no puede responder Bogotá

Conviene saberlo antes de pedirle algo que no puede hacer. Sobre una muestra de
300 conjuntos tomada en seis puntos del catálogo: **128 (43%) tienen al menos un
recurso respaldado por el DataStore**, y esos sí se pueden leer fila por fila. El
57% restante es mayoritariamente geoespacial — SHP, GPKG, GEOJSON, DXF, KML,
WMS/WFS, las capas de la IDECA — publicado como archivos y no a través del
DataStore.

Esos conjuntos siguen siendo plenamente descubribles: se obtienen los metadatos,
la lista de recursos y las URL de descarga. Simplemente no se pueden consultar
desde aquí. `bogota_get_dataset` marca cada recurso con `queryable: true/false`
para que el modelo lo sepa antes de intentarlo.

Este servidor **no** descarga archivos de recursos, deliberadamente. Hacerlo
supondría unas 750 líneas más y una guardia contra SSRF, y los analizadores de
CSV/XLSX que traería no podrían leer SHP ni GPKG de todos modos — así que el
hueco quedaría casi igual de abierto. El razonamiento completo está en
`CHANGELOG.md`.

## Instalación

```bash
uvx colombian-open-data-mcp
```

Luego agréguelo a su cliente. Claude Code:

```bash
claude mcp add colombia -- uvx colombian-open-data-mcp
```

Claude Desktop, Cursor, VS Code, Gemini CLI y otros:
vea **[docs/clients.md](docs/clients.md)**.

No hace falta ninguna clave de API. `SOCRATA_APP_TOKEN` es opcional y solo eleva
el límite de tasa anónimo del portal nacional.

## Ejemplos

Pregúntele a su asistente, en español o en inglés:

> ¿Cuáles son las diez entidades que más datasets publican en datos.gov.co?

> Busca datasets de presupuesto en datos.gov.co y muéstrame las columnas del
> primero.

> En datos.gov.co, agrupa el dataset de contratación por departamento y suma el
> valor total.

> ¿Qué datasets de movilidad publica Bogotá, y cuáles se pueden consultar fila
> por fila?

> Filtra el recurso de casos de Bogotá por localidad Bosa y muéstrame las
> primeras 20 filas.

## Notas de diseño

**Socrata y CKAN son el motor de consulta.** Ninguno de los dos portales
necesita caché local, capa DuckDB ni paso de descarga, porque ambos ejecutan la
consulta por su cuenta. El proyecto hermano
[`dominican-open-data-mcp`](https://github.com/alcastaro/datos.gob.do-MCP-server)
carga unas 4.500 líneas de caché, análisis de archivos y reparación de enlaces
que le impone un portal CKAN sin DataStore. Este no las necesita, y por eso
cubre 20 herramientas con mucho menos código. Menos líneas aquí es consecuencia
de un mejor sustrato, no de un producto más pobre.

**Toda herramienta retorna, ninguna lanza excepción.** Una caída del portal
llega como `{"error": ..., "hint": ...}` — una excepción escapando de una
herramienta le llegaría al modelo como un error opaco de protocolo, sobre el que
no puede actuar. Una prueba parametrizada lo verifica para las 20.

**Toda entrada se valida antes de construir una URL.** Los códigos 4x4 de
Socrata contra una expresión regular exacta; los identificadores CKAN como UUID
o slug; cada identificador SoQL contra una lista de permitidos *y* una lista de
prohibidos con las secuencias de comentario y corte de sentencia; cada literal
escapado. La escotilla de SoQL crudo rechaza palabras clave de escritura y
consultas multisentencia. Vea **[SECURITY.md](SECURITY.md)**.

## Desarrollo

```bash
uv sync --group dev --extra dev
uv run pytest                                  # 212 pruebas herméticas, piso de cobertura 85%
uv run ruff check src/ tests/
uv run mypy src/colombian_open_data_mcp/
RUN_LIVE_TESTS=1 uv run pytest tests/test_live.py -v   # 11 pruebas en vivo, opcionales
```

Las pruebas en vivo nunca corren en CI. Ambos portales son infraestructura de
terceros y el de Bogotá está detrás de un WAF; una compilación que se pone en
rojo porque el limitador de tasa de otro tuvo un mal minuto es una compilación
que la gente aprende a ignorar.

Vea **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## Proyectos relacionados

- **[dominican-open-data-mcp](https://github.com/alcastaro/datos.gob.do-MCP-server)**
  — la misma idea para `datos.gob.do` (CKAN 2.11), ya en PyPI y en el registro
  oficial de MCP.
- **opendata-latam-mcp** — adaptador multipaís. El trabajo de abstracción de
  portales pertenece allí, no aquí.

## Trabajo previo

Existen otros servidores MCP que tocan datos abiertos colombianos, y conviene
ser preciso sobre qué es cada uno:

- **`io.github.pipeworx-io/datos-co`** llegó primero al registro oficial, el
  2026-06-02. Ofrece 3 herramientas y es **solo remoto** — accesible a través de
  una pasarela comercial, sin paquete en npm ni en PyPI que se pueda instalar.
- **`io.github.cyanheads/socrata-mcp-server`** es un cliente Socrata
  **genérico** y bien construido, para cualquier portal, con
  `data.seattle.gov` por defecto. Se le puede apuntar a Colombia; no conoce
  Colombia.
- Los **servidores de SECOP** (`juandavidsernav`, `pipeworx-io`) y
  **`matematicsolutions/co-eli-mcp`** son verticales sobre contratación pública
  y sentencias de la Corte Constitucional respectivamente. Complementan a este
  servidor en lugar de solaparse con él.

Este es la opción específica de Colombia, de portal completo e instalable en
local, y hasta donde sabemos la única que cubre el catálogo distrital de Bogotá.

## Licencia

MIT. Vea [LICENSE](LICENSE).

Los datos pertenecen a las instituciones colombianas que los publican y se rigen
por sus propias licencias. Vea **[docs/PRIVACIDAD.md](docs/PRIVACIDAD.md)**.
