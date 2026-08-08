# Nota de privacidad

**[English](PRIVACY.md) · [Español](PRIVACIDAD.md)**

Versión corta: este servidor no recoge nada, no guarda nada y no envía nada a
ningún sitio salvo a los portales del Estado colombiano que consulta y, para
los archivos publicados y los servicios de mapas, a las direcciones que esos
mismos portales entregan.

## Dónde se ejecuta

`colombian-open-data-mcp` es un proceso local que habla por stdio. Su cliente
MCP lo arranca en su máquina, se comunica con él por entrada y salida estándar,
y lo detiene al cerrarse. No hay ningún servicio nuestro en el camino ni cuenta
que crear.

## Qué sale de su máquina

Solo la consulta. La mayor parte va a un conjunto fijo de hosts escritos en el
código fuente:

| Host | Para qué |
|---|---|
| `api.us.socrata.com` | Catálogo cruzado de Socrata, acotado a `www.datos.gov.co`, para descubrir datasets |
| `www.datos.gov.co` | Metadatos de datasets nacionales y consultas SoQL sobre los datos |
| `datosabiertos.bogota.gov.co` | Catálogo CKAN de Bogotá y consultas al DataStore |
| `datos.cali.gov.co` | Catálogo CKAN de Cali y consultas al DataStore |
| `datosabiertos.valledelcauca.gov.co` | Catálogo CKAN del Valle del Cauca y consultas al DataStore |
| `datosabiertos.cartagena.gov.co` | Catálogo CKAN de Cartagena y consultas al DataStore |

**Dos familias de herramientas llegan más lejos, y esto cambió en la 0.4.** Los
portales colombianos publican con frecuencia un conjunto de datos no como tabla
consultable sino como servicio de mapas ArcGIS o como archivo —un CSV, un libro
de Excel, un documento JSON— alojado en otra dirección. Leer eso es la
diferencia entre cubrir una cuarta parte del catálogo de Bogotá y cubrirlo casi
entero, así que `city_esri_*` y `city_read_resource_file` siguen la dirección
que les da el catálogo: `mapas.bogota.gov.co`, `services*.arcgis.com`, el
dominio propio de una entidad o, en ocasiones, un bucket de almacenamiento.

Dos cosas acotan ese alcance, y ambas son verificables en el código:

- **Ninguna herramienta acepta una URL**, ni suya ni del modelo. Todas reciben
  un identificador de recurso y resuelven la dirección a través del propio
  `resource_show` del portal. El conjunto de hosts alcanzables es aquel al que
  apuntan los catálogos colombianos, no aquel que pida una petición.
- **`netguard.py` aplica una política de red en cada petición, incluida cada
  redirección.** Por omisión el esquema debe ser `http` o `https` y toda
  dirección a la que resuelva el nombre debe ser enrutable en la internet
  pública, lo que rechaza loopback, los rangos privados y los metadatos de
  instancia en la nube en `169.254.169.254`. Con `CO_MCP_NETGUARD=strict` se
  acota todavía más, a los cinco portales más `*.gov.co` y `*.arcgis.com`, a
  cambio de perder alrededor del 12% de los archivos alojados fuera.
  [`SECURITY.md`](../SECURITY.md) documenta ambos modos y el único límite
  residual, una ventana de *DNS rebinding*.

Esas peticiones llevan lo que lleva cualquier cliente HTTP: su dirección IP, un
`User-Agent` que identifica este software y su versión, y los parámetros de la
consulta. Las prácticas de registro y privacidad de los portales son suyas, no
nuestras — son infraestructura del Estado colombiano y rigen sus términos. Lo
mismo vale para cualquier host de terceros donde un portal haya decidido
publicar un archivo.

## Qué se almacena

Nada. No hay directorio de caché, ni base de datos, ni archivo descargado, ni
historial. Los archivos que lee `city_read_resource_file` se procesan en memoria
bajo un tope de tamaño y se descartan una vez devuelta la respuesta. Los
resultados existen únicamente en la respuesta a la llamada de herramienta, que
vive donde su cliente MCP guarde la conversación.

## Registros

El servidor escribe solo a la salida de error estándar: una línea de arranque,
el número de herramientas registradas y cualquier error fatal. Nunca registra
el contenido de una consulta ni sus resultados. Esa salida va a donde su
cliente MCP la dirija, normalmente a su propio archivo de log, en su máquina.

## Credenciales

El único secreto opcional es `SOCRATA_APP_TOKEN`, que eleva el límite de tasa
anónimo de `datos.gov.co`. Si está definido, se lee una sola vez al arrancar, lo
envía como cabecera `X-App-Token` únicamente el cliente de Socrata, y nunca se
escribe en un log ni en un archivo. Los clientes de CKAN, de ArcGIS y de lectura
de archivos son objetos distintos que nunca lo ven, de modo que jamás se envía a
los cuatro portales territoriales, ni a un servicio de mapas, ni al host de un
archivo. El servidor funciona sin él.

## Sobre los datos mismos

Todo lo que devuelve este servidor son datos abiertos públicos publicados por
instituciones colombianas bajo sus propias licencias. No accede a nada que
requiera autenticación, y no podría: cada llamada es un GET sin autenticar.

Dicho eso, algunos conjuntos de datos abiertos contienen información sobre
personas identificables. Esa es una decisión de la institución que publica, no
de este software. Si usted procesa esos datos, las obligaciones de la Ley 1581
de 2012 de protección de datos personales son suyas, y aplican con independencia
de que el dato se haya publicado en abierto. No conservar copia de nada es lo
que evita que este servidor asuma él mismo esas obligaciones, y es una decisión
de diseño deliberada, no una funcionalidad que falte.

## Cambios

Cualquier cambio en los hosts contactados o en los datos retenidos aparecerá en
[CHANGELOG.md](../CHANGELOG.md) bajo la versión que lo introduzca.
