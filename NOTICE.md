# Notice on data and licensing

## The code

The source code of this MCP server is licensed under the MIT License. See
[LICENSE](LICENSE) for the full text.

## The data

**The MIT License covers this software only. It does not cover, and cannot
grant any rights over, the data this software retrieves.**

Datasets reachable through this server are published independently by Colombian
government institutions on their own portals:

| Portal | Institution |
|---|---|
| [datos.gov.co](https://www.datos.gov.co) | MinTIC — national open data portal |
| [datosabiertos.bogota.gov.co](https://datosabiertos.bogota.gov.co) | Alcaldía Mayor de Bogotá |
| [datos.cali.gov.co](https://datos.cali.gov.co) | Alcaldía de Santiago de Cali |
| [datosabiertos.valledelcauca.gov.co](https://datosabiertos.valledelcauca.gov.co) | Gobernación del Valle del Cauca |
| [datosabiertos.cartagena.gov.co](https://datosabiertos.cartagena.gov.co) | Alcaldía de Cartagena de Indias |

Each dataset carries terms set by its publisher, commonly a Creative Commons
licence or a jurisdiction-specific open data licence. Those terms travel with
the data, not with this code. Before redistributing anything obtained through
this server, or citing it in a publication, check the licence on the dataset's
own catalogue page — `get_dataset` and `city_get_dataset` both return it.

## Personal data

This server stores nothing. It holds no cache, writes no files and keeps no
logs of the data it moves. That is a deliberate design constraint rather than
an omission: retaining portal data would make whoever runs the server a data
controller under Colombia's **Ley 1581 de 2012** on the protection of personal
data, with the obligations that follow.

Colombian open data catalogues do publish datasets that contain or can be
combined into personal data. Responsibility for how such data is used passes to
whoever queries it. See [docs/PRIVACY.md](docs/PRIVACY.md) and
[docs/PRIVACIDAD.md](docs/PRIVACIDAD.md) for what the server does and does not
transmit.

## Attribution

When citing figures obtained through this server, cite the publishing
institution and the dataset, not this software. To cite the software itself,
see [CITATION.cff](CITATION.cff).
