# Nota de privacidad

**[English](PRIVACY.md) · [Español](PRIVACIDAD.md)**

Versión corta: este servidor no recoge nada, no guarda nada y no envía nada a
ningún sitio salvo a los dos portales del Estado colombiano que consulta.

## Dónde se ejecuta

`colombian-open-data-mcp` es un proceso local que habla por stdio. Su cliente
MCP lo arranca en su máquina, se comunica con él por entrada y salida estándar,
y lo detiene al cerrarse. No hay ningún servicio nuestro en el camino ni cuenta
que crear.

## Qué sale de su máquina

Solo la consulta, y solo hacia uno de tres hosts fijos compilados en el código
fuente:

| Host | Para qué |
|---|---|
| `api.us.socrata.com` | Catálogo cruzado de Socrata, acotado a `www.datos.gov.co`, para descubrir datasets |
| `www.datos.gov.co` | Metadatos de datasets y consultas SoQL sobre los datos |
| `datosabiertos.bogota.gov.co` | Catálogo CKAN de Bogotá y consultas al DataStore |

Ninguna herramienta acepta una URL como parámetro, así que el servidor no puede
ser dirigido a un cuarto host.

Esas peticiones llevan lo que lleva cualquier cliente HTTP: su dirección IP, un
`User-Agent` que identifica este software y su versión, y los parámetros de la
consulta. Las prácticas de registro y privacidad de los portales son suyas, no
nuestras — son infraestructura del Estado colombiano y rigen sus términos.

## Qué se almacena

Nada. No hay directorio de caché, ni base de datos, ni archivo descargado, ni
historial. Los resultados existen únicamente en la respuesta a la llamada de
herramienta, que vive donde su cliente MCP guarde la conversación.

## Registros

El servidor escribe solo a la salida de error estándar: una línea de arranque,
el número de herramientas registradas y cualquier error fatal. Nunca registra
el contenido de una consulta ni sus resultados. Esa salida va a donde su
cliente MCP la dirija, normalmente a su propio archivo de log, en su máquina.

## Credenciales

El único secreto opcional es `SOCRATA_APP_TOKEN`, que eleva el límite de tasa
anónimo de `datos.gov.co`. Si está definido, se lee una sola vez al arrancar, se
envía como cabecera `X-App-Token` únicamente a Socrata, y nunca se escribe en un
log ni en un archivo. Nunca se envía al portal de Bogotá. El servidor funciona
sin él.

## Sobre los datos mismos

Todo lo que devuelve este servidor son datos abiertos públicos publicados por
instituciones colombianas bajo sus propias licencias. No accede a nada que
requiera autenticación, y no podría: cada llamada es un GET sin autenticar.

Dicho eso, algunos conjuntos de datos abiertos contienen información sobre
personas identificables. Esa es una decisión de la institución que publica, no
de este software. Si usted procesa esos datos, las obligaciones de la Ley 1581
de 2012 de protección de datos personales son suyas, y aplican con independencia
de que el dato se haya publicado en abierto.

## Cambios

Cualquier cambio en los hosts contactados o en los datos retenidos aparecerá en
[CHANGELOG.md](../CHANGELOG.md) bajo la versión que lo introduzca.
