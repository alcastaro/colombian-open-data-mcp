<!-- mcp-name: io.github.alcastaro/colombian-open-data-mcp -->

**[English](README.md) · [Español](README.es.md)**

---

# colombian-open-data-mcp

**Servidor MCP para los datos abiertos del Estado colombiano — cinco portales y
tres fuentes de datos: el nacional [datos.gov.co](https://www.datos.gov.co)
(Socrata, 8.391 conjuntos), [Bogotá](https://datosabiertos.bogota.gov.co)
(CKAN, ~1.900), [Cali](https://datos.cali.gov.co) (657),
[Valle del Cauca](https://datosabiertos.valledelcauca.gov.co) (50) y
[Cartagena](https://datosabiertos.cartagena.gov.co) (38).**

El primer servidor MCP de datos abiertos de Colombia que se instala y se ejecuta
**en su propia máquina** — sin pasarela, sin intermediario, sin cuenta. Conecta
cualquier asistente compatible con MCP (Claude Desktop, Claude Code, Cursor, VS
Code Copilot, Gemini CLI) directamente con los catálogos y con los datos vivos:
el filtrado y la agregación los ejecutan los portales, no el modelo.

**24 herramientas · 569 pruebas herméticas · 36 en vivo · 92% de cobertura · MIT**

---

## Por qué las plataformas no son intercambiables

El portal nacional corre sobre **Socrata**, lo que lo hace atípico en América
Latina — Argentina, Chile, México, Uruguay y República Dominicana corren todos
sobre CKAN. Socrata trae un lenguaje de consulta de verdad, **SoQL**, de modo
que `WHERE`, `GROUP BY`, `count()` y `sum()` se ejecutan en el servidor y solo
viajan las filas ya agregadas.

Los cuatro portales territoriales corren sobre **CKAN**. Su DataStore admite
filtrado tipado, pero **ninguno de los cuatro expone `datastore_search_sql`** —
verificado contra las cuatro APIs reales: Bogotá responde 400 (la acción no está
registrada, y además un WAF bloquea la forma GET), Cali responde 403, y Valle y
Cartagena responden 400.

Esa diferencia es real, así que este servidor la expone en lugar de disimularla.
Hay un `aggregate_dataset` para el portal nacional y **ningún equivalente
territorial sobre el DataStore**, porque ofrecerlo sería prometer algo que esos
portales no pueden hacer. Una prueba en vivo verifica esa ausencia en cada uno;
si alguno llegara a habilitar SQL, la prueba falla y avisa.

Las familias de Socrata y de CKAN siguen separadas y no se funden en una sola
herramienta con un parámetro `portal`: los identificadores de Socrata son
códigos 4x4 (`abcd-1234`), los de CKAN son UUID o slugs, y un parámetro
compartido tendría que ramificar su validación — y la validación de
identificadores es justamente la defensa contra la inyección en la URL. Entre
los cuatro portales CKAN nada de eso aplica, y por eso esos sí se colapsaron en
un único parámetro `city` en la versión 0.4.

## Tres caminos hacia los datos

Un catálogo territorial colombiano publica el mismo conjunto de datos de varias
formas, y solo una de ellas es una tabla de base de datos. Este servidor lee las
tres, en el orden en que un modelo debería intentarlas:

1. **El DataStore de CKAN** — una consulta tipada contra una tabla que el portal
   ya construyó. No viaja nada más que la respuesta.
2. **Servicios ArcGIS REST** — 334 de los 1.917 conjuntos de Bogotá se publican
   como capas ESRI. Son APIs, no archivos: filtran, proyectan, paginan y
   **calculan `GROUP BY` en el servidor**. `city_esri_aggregate` es la única
   agregación territorial de este servidor que no se suma sobre filas dentro del
   contexto del modelo.
3. **El archivo publicado** — un CSV, XLSX, JSON o GeoJSON en una URL de
   descarga, para los conjuntos que no tienen tabla ni servicio. Se descarga con
   un tope de 12 MB, se parsea, se responde y se descarta. No hay caché ni nada
   que toque el disco.

Ese tercer camino es lo que el servidor dominicano hermano hace para *todo* su
catálogo, porque `datos.gob.do` corre CKAN sin la extensión DataStore. Aquí es
el último recurso, y por eso cuesta una fracción del código.

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

### Portales territoriales — una familia, cuatro catálogos (12)

Todas las herramientas de abajo reciben un parámetro `city`: `bogota`, `cali`,
`valle` o `cartagena`.

| Herramienta | Qué hace |
|---|---|
| `city_search_datasets` | Búsqueda en el catálogo, filtrable por entidad, grupo o etiqueta. |
| `city_get_dataset` | Metadatos completos y todos los recursos, cada uno marcado `queryable`. |
| `city_list_organizations` | Entidades que publican, con número de conjuntos. |
| `city_list_groups` | Grupos temáticos. |
| `city_list_tags` | Etiquetas del portal. |
| `city_get_site_stats` | Totales del portal y qué puede y qué no puede su DataStore. |
| `city_resource_preview` | Primeras N filas de un recurso en el DataStore, con tipos de columna. |
| `city_filter_resource` | Filtro tipado, proyección y orden, ejecutados en el servidor. |
| `city_esri_service_info` | Campos y capacidades de una capa ArcGIS REST. |
| `city_esri_query` | Filas de una capa ArcGIS, filtradas y paginadas en el servidor. |
| `city_esri_aggregate` | **`GROUP BY` en el servidor** sobre una capa ArcGIS. |
| `city_read_resource_file` | Descarga y parsea un recurso CSV / XLSX / JSON publicado. |

Hasta la versión 0.3 cada portal tenía su propia familia de ocho herramientas
con prefijo. Cuatro portales de esa forma serían treinta y dos esquemas casi
idénticos, así que la 0.4 los colapsó: doce herramientas que cubren el doble de
terreno, y un quinto portal pasa a ser un descriptor y nada más.

## Qué puede y qué no puede responder cada portal

Estas cifras salen de ejecutar las herramientas reales contra una muestra
aleatoria — `sweep/stress_test.py --total 600 --seed 60606` — no de leer
documentación. Valle del Cauca y Cartagena son lo bastante pequeños como para
haber recorrido sus catálogos enteros en vez de muestrearlos.

| Portal | Plataforma | Muestra | Devolvió filas reales | Tasa | DataStore | ESRI | Archivo |
|---|---|---|---|---|---|---|---|
| `datos.gov.co` | Socrata | 120 | 120 | **100.0%** | — | — | — |
| `datos.cali.gov.co` | CKAN | 120 | 74 | **61.7%** | 74 | — | — |
| `datosabiertos.bogota.gov.co` | CKAN | 120 | 107 | **89.2%** | 20 | 21 | 66 |
| `datosabiertos.cartagena.gov.co` | CKAN | 38 | 38 | **100.0%** | 37 | — | 1 |
| `datosabiertos.valledelcauca.gov.co` | CKAN | 50 | 50 | **100.0%** | 50 | — | — |

Las tres columnas de la derecha dicen **por qué vía** llegaron las filas. Esa
desagregación es deliberada: una cifra de cobertura que sube cuando se añaden
herramientas, sin decir cuál hizo el trabajo, no es un número que nadie pueda
comprobar.

Bogotá es el portal al que apuntaba esta versión, y la forma de su catálogo
explica por qué. La mitad de sus conjuntos trae algún recurso que el catálogo
*marca* como consultable, y esa marca se equivoca más veces de las que acierta:
en esta corrida, **37 de esos 59 conjuntos respondieron HTTP 404** porque no
existe tabla detrás. Así que el DataStore por sí solo alcanzó 20 de 120. El
servidor reescribe ese 404 en una explicación que señala los metadatos del
portal como la causa, para que el modelo sepa que se equivocó el catálogo y no
asuma que se equivocó él. `city_get_dataset` marca cada recurso
`queryable: true/false`; tómelo como una pista, no como una promesa.

Lo que cierra la brecha es que el resto del catálogo no falta: está publicado de
otra manera, como servicios ArcGIS y como archivos planos. Leer ambos es lo que
llevó a Bogotá del 27% al 89%.

En todas las llamadas de esa corrida, **ninguna lanzó una excepción** — cada
fallo llegó como un sobre de error sobre el que el modelo puede actuar.

### Lo que sigue fuera de alcance

Los archivos genuinamente geoespaciales — SHP, GPKG, DXF, KML, DWG — se rechazan
con una explicación en vez de parsearse mal. Leerlos exigiría una pila SIG que
este servidor no tiene por qué cargar, y cuando un conjunto publica un servicio
ESRI junto al shapefile, el servicio ya responde la pregunta.

## Instalación

```bash
uvx colombian-open-data-mcp
```

Luego agréguelo a su cliente. Claude Code:

```bash
claude mcp add colombia -- uvx colombian-open-data-mcp
```

Claude Desktop, Cursor, VS Code y otros clientes stdio:
vea **[docs/clients.md](docs/clients.md)**.

### Gemini CLI y Google Antigravity

La MCP Store de Antigravity la cura Google y no tiene vía pública de autopostulación,
pero añadir el servidor a mano no depende del permiso de nadie y funciona hoy. Ponga
esto en `~/.gemini/config/mcp_config.json` para tenerlo en todas partes, o en
`.agents/mcp_config.json` dentro de un espacio de trabajo para tenerlo solo ahí:

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

No hace falta ninguna clave de API. `SOCRATA_APP_TOKEN` es opcional y solo eleva
el límite de tasa anónimo del portal nacional.

Otras dos variables de entorno ajustan el comportamiento y ninguna es
obligatoria: `CO_MCP_TIMEOUT` fija cuántos segundos esperar la respuesta del
portal (20 por omisión, 300 como máximo) — conviene subirla para agregaciones
sobre los datasets nacionales más grandes, donde una mediana sobre seis
millones de filas tarda más de un minuto — y `CO_MCP_NETGUARD` selecciona la
política de red, documentada en [`SECURITY.md`](SECURITY.md).

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

> Cuenta cuántos parques hay por localidad en la capa ESRI de Bogotá — que lo
> agrupe el servidor, no tú.

> Ese dataset de Bogotá no está en el DataStore. Lee el archivo publicado y
> muéstrame las columnas.

> ¿Qué publica el portal de Cartagena y qué de eso se puede consultar?

## Notas de diseño

**Los portales son el motor de consulta, siempre que se pueda.** Socrata ejecuta
SoQL, el DataStore de CKAN ejecuta filtros tipados y ArcGIS calcula estadísticas
— de modo que tres de los cuatro caminos de datos transfieren solo la respuesta.
El proyecto hermano
[`dominican-open-data-mcp`](https://github.com/alcastaro/datos.gob.do-MCP-server)
carga unas 4.500 líneas de caché, análisis de archivos y reparación de enlaces
porque `datos.gob.do` corre CKAN **sin DataStore alguno** y cada fila que sirve
tiene que salir de un archivo descargado. Este servidor necesita una descarga
solo para el último recurso, y no necesita caché para ninguno.

**No se almacena nada.** `city_read_resource_file` descarga con un tope, parsea
las primeras filas, responde y descarta los bytes. Eso es un límite deliberado
tanto como una decisión de diseño: conservar estos conjuntos convertiría a este
servidor en **responsable del tratamiento** bajo la Ley 1581 de 2012 para
cualquiera de ellos que contenga personas identificables. Esa es una decisión
que se toma explícita y separadamente, no algo que se adquiere como efecto
secundario de una optimización de rendimiento.

**Ninguna herramienta acepta una URL.** Las de ESRI y la de archivos reciben un
UUID de recurso y consultan la dirección en el catálogo del propio portal, de
modo que el conjunto de hosts que este servidor puede alcanzar está acotado por
lo que publica un catálogo del Estado colombiano. Encima de eso, `netguard.py`
exige que toda dirección resuelta sea globalmente enrutable — rechazando
loopback, RFC-1918, ULA de IPv6 y el endpoint de metadatos de nube en
`169.254.169.254` — y está instalado como un *hook* de petición de httpx, así
que también se verifican los saltos de redirección. Vea
**[SECURITY.md](SECURITY.md)**, cuya sección «No SSRF surface» **se eliminó en
la 0.4 porque dejó de ser cierta**.

**Toda herramienta retorna, ninguna lanza excepción.** Una caída del portal
llega como `{"error": ..., "hint": ...}` — una excepción escapando de una
herramienta le llegaría al modelo como un error opaco de protocolo, sobre el que
no puede actuar. Una prueba parametrizada lo verifica para las 24, y el arnés de
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
uv run pytest                                  # 569 pruebas herméticas, piso de cobertura 85%
uv run ruff check src/ tests/ sweep/
uv run mypy src/colombian_open_data_mcp/
RUN_LIVE_TESTS=1 uv run pytest tests/test_live.py -v   # 36 pruebas en vivo, opcionales
uv run python sweep/stress_test.py --total 600 --seed 60606   # los cinco portales
```

Las pruebas en vivo nunca corren en CI. Los cinco portales son infraestructura
de terceros y el de Bogotá está detrás de un WAF; una compilación que se pone en
rojo porque el limitador de tasa de otro tuvo un mal minuto es una compilación
que la gente aprende a ignorar.

El arnés de fuerza no es una prueba: es una medición ocasional contra catálogos
vivos, y es deliberadamente cortés — concurrencia 4, una pausa entre sondeos, y
un orden de vías que intenta primero la petición más barata. No suba la
concurrencia para ir más rápido.

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
