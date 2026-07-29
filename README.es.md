<!-- mcp-name: io.github.alcastaro/colombian-open-data-mcp -->

**[English](README.md) · [Español](README.es.md)**

---

# colombian-open-data-mcp

**Servidor MCP para los datos abiertos del Estado colombiano — tres portales,
dos plataformas: el nacional [datos.gov.co](https://www.datos.gov.co) (Socrata,
8.391 conjuntos), [Bogotá](https://datosabiertos.bogota.gov.co) (CKAN, ~1.900) y
[Cali](https://datos.cali.gov.co) (CKAN, 657).**

El primer servidor MCP de datos abiertos de Colombia que se instala y se ejecuta
**en su propia máquina** — sin pasarela, sin intermediario, sin cuenta. Conecta
cualquier asistente compatible con MCP (Claude Desktop, Claude Code, Cursor, VS
Code Copilot, Gemini CLI) directamente con los catálogos y con los datos vivos:
el filtrado y la agregación los ejecutan los portales, no el modelo.

**28 herramientas · 281 pruebas herméticas · 18 pruebas en vivo · MIT**

---

## Por qué dos portales, y por qué no son intercambiables

El portal nacional corre sobre **Socrata**, lo que lo hace atípico en América
Latina — Argentina, Chile, México, Uruguay y República Dominicana corren todos
sobre CKAN. Socrata trae un lenguaje de consulta de verdad, **SoQL**, de modo
que `WHERE`, `GROUP BY`, `count()` y `sum()` se ejecutan en el servidor y solo
viajan las filas ya agregadas.

Los dos portales distritales corren sobre **CKAN 2.10.4**. Su DataStore admite
filtrado tipado, pero ninguno expone `datastore_search_sql` — verificado contra
ambas APIs reales: Bogotá responde 400 (la acción no está registrada, y además
un WAF bloquea la forma GET) y Cali responde 403.

Esa diferencia es real, así que este servidor la expone en lugar de disimularla.
Hay un `aggregate_dataset` para el portal nacional y **ningún equivalente
distrital**, porque ofrecerlo sería prometer algo que esos portales no pueden
hacer. Una prueba en vivo verifica esa ausencia en cada uno; si alguno llegara a
habilitar SQL, la prueba falla y avisa.

Por la misma razón las familias son herramientas separadas y no una sola con un
parámetro `portal`: los identificadores de Socrata son códigos 4x4
(`abcd-1234`), los de CKAN son UUID o slugs, y un parámetro compartido tendría
que ramificar su validación — y la validación de identificadores es justamente
la defensa contra la inyección en la URL.

## Herramientas

### Portal nacional — `datos.gov.co` (12)

| Herramienta | Qué hace |
|---|---|
| `search_datasets` | Búsqueda por palabra clave, categoría, etiqueta y tipo de activo. |
| `get_dataset` | Metadatos completos: columnas, tipos, entidad, licencia, URL. |
| `list_recent_datasets` | Conjuntos actualizados más recientemente. |
| `list_categories` | Categorías de primer nivel del portal. |
| `list_tags` | Todas las etiquetas del portal. |
| `list_owners` | Entidades publicadoras con su número de conjuntos. |
| `autocomplete` | Resuelve un nombre parcial a un valor real (dataset, etiqueta, categoría, entidad). |
| `get_site_stats` | Totales del portal y qué tipos de activo son consultables. |
| `download_dataset_preview` | Primeras N filas, directo desde Socrata. |
| `filter_dataset` | WHERE / SELECT / ORDER BY tipados. |
| `aggregate_dataset` | GROUP BY tipado + count / sum / avg / median / min / max / stddev. |
| `query_dataset_soql` | Escotilla para usuarios avanzados: SoQL crudo, solo lectura, validado. |

### Portales distritales — Bogotá y Cali (8 cada uno)

| Herramienta (por ciudad) | Qué hace |
|---|---|
| `<ciudad>_search_datasets` | Búsqueda en el catálogo, filtrable por entidad, grupo o etiqueta. |
| `<ciudad>_get_dataset` | Metadatos completos y todos los recursos, cada uno marcado `queryable`. |
| `<ciudad>_list_organizations` | Entidades distritales que publican, con su número de conjuntos. |
| `<ciudad>_list_groups` | Grupos temáticos. |
| `<ciudad>_list_tags` | Etiquetas del portal. |
| `<ciudad>_get_site_stats` | Totales del portal, y qué puede y qué no puede hacer su DataStore. |
| `<ciudad>_resource_preview` | Primeras N filas de un recurso en DataStore, con los tipos de columna. |
| `<ciudad>_filter_resource` | Filtrado, proyección y ordenamiento tipados del lado del servidor. |

`<ciudad>` es `bogota` o `cali`. Ambos portales corren CKAN 2.10.4 y las ocho
herramientas se generan a partir de una sola definición, así que su forma es
idéntica — hay una prueba que lo verifica. Ninguno tiene herramienta de
agregación, porque ninguno expone `datastore_search_sql`.

## Qué puede y qué no puede responder cada portal

Estas cifras salen de ejecutar las herramientas de verdad contra conjuntos
elegidos al azar (`sweep/stress_test.py`), no de leer documentación:

| | datos.gov.co | Bogotá | Cali |
|---|---|---|---|
| Plataforma | Socrata | CKAN 2.10.4 | CKAN 2.10.4 |
| Filtrar del lado del servidor | sí | sí | sí |
| **Agregar del lado del servidor** | **sí** | no | no |
| Conjuntos que devolvieron filas reales | ~100% | ~13% | ver informe |

En los portales CKAN esa última fila es una propiedad del portal, no de este
servidor. La causan dos cosas. La mayor parte de cada catálogo se publica como
archivos y no a través del DataStore — muy geoespacial en el caso de Bogotá
(SHP, GPKG, GEOJSON, DXF, KML, las capas de la IDECA). Y la propia bandera
`datastore_active` del catálogo no es fiable: de 80 recursos medidos que la
llevaban, 27 respondieron HTTP 404 porque no existe tabla. El servidor reescribe
ese 404 como una explicación que señala los metadatos del portal como causa,
para que el modelo entienda que se equivocó el catálogo y no él.

`<ciudad>_get_dataset` marca cada recurso con `queryable: true/false`. Conviene
tratarlo como una pista, no como una promesa.

Alrededor del 9% de los recursos de Bogotá son **servicios** consultables y no
archivos — endpoints ESRI REST, WFS y WMS que aceptan `?query=`, paginan y, en
el caso de ESRI, calculan estadísticas. Leerlos no requiere descargar nada, y es
el trabajo de ampliación de cobertura con mejor retorno que queda pendiente.

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

> Compara qué entidades publican más datos abiertos en Bogotá y en Cali.

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
no puede actuar. Una prueba parametrizada lo verifica para las 28, y el arnés de
fuerza lo confirma contra los catálogos vivos: cero excepciones en miles de
llamadas reales.

**Los fallos transitorios se reintentan; los definitivos no.** Una conexión
caída obtiene tres intentos con retroceso real. Un `ReadTimeout` obtiene uno,
porque el servidor aceptó la petición y sigue trabajando — reintentar tres veces
una consulta de 20 segundos solo hace que el usuario espere un minuto para
recibir la misma respuesta.

**Toda entrada se valida antes de construir una URL.** Los códigos 4x4 de
Socrata contra una expresión regular exacta; los identificadores CKAN como UUID
o slug; cada identificador SoQL contra una lista de permitidos *y* una lista de
prohibidos con las secuencias de comentario y corte de sentencia; cada literal
escapado. La escotilla de SoQL crudo rechaza palabras clave de escritura y
consultas multisentencia. Vea **[SECURITY.md](SECURITY.md)**.

## Desarrollo

```bash
uv sync --group dev --extra dev
uv run pytest                                  # 281 pruebas herméticas, piso de cobertura 85%
uv run ruff check src/ tests/
uv run mypy src/colombian_open_data_mcp/
RUN_LIVE_TESTS=1 uv run pytest tests/test_live.py -v   # 18 pruebas en vivo, opcionales
uv run python sweep/stress_test.py             # 450 conjuntos al azar, los tres portales
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
