"""Tests for the ArcGIS REST client.

Three of these encode bugs that were found against the live services rather
than reasoned about, and they are the ones worth keeping if the file ever has
to shrink:

* ``test_a_query_goes_to_the_query_endpoint`` — the client originally queried
  the layer root, which answers HTTP 200 with the layer's own description and
  silently ignores every parameter. The tool reported zero rows for a layer
  holding eleven and nothing looked wrong.
* ``test_an_error_body_under_http_200_is_raised`` — ArcGIS puts query errors in
  the body and still returns 200.
* ``test_a_zipped_shapefile_labelled_esri_rest_is_refused`` — the first Bogotá
  resource labelled ``ESRI REST`` is a ``shape.zip``.
"""

from __future__ import annotations

import json
import re

import pytest

from colombian_open_data_mcp import esri
from colombian_open_data_mcp.esri import EsriError

LAYER = "https://serviciosgis.example.gov.co/arcgis/rest/services/movilidad/metro/MapServer/4"
ROOT = "https://serviciosgis.example.gov.co/arcgis/rest/services/movilidad/metro/MapServer"
FEATURE_LAYER = "https://portalgis.example.gov.co/arcgis/rest/services/x/y/FeatureServer/3"
ANY = re.compile(r"https://.*example\.gov\.co/.*")

LAYER_INFO = {
    "name": "Estaciónes Segunda Línea Metro",
    "type": "Feature Layer",
    "description": "Infraestructura de acceso al sistema metro.",
    "geometryType": "esriGeometryPoint",
    "maxRecordCount": 2000,
    "supportsStatistics": True,
    "advancedQueryCapabilities": {"supportsPagination": True},
    "drawingInfo": {"renderer": {"symbol": {"a lot of": "cartography"}}},
    "fields": [
        {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
        {"name": "NOMBRE", "type": "esriFieldTypeString", "alias": "Nombre"},
        {"name": "TIPO", "type": "esriFieldTypeString", "alias": "TIPO"},
    ],
}

FEATURES = {
    "fields": [
        {"name": "OBJECTID", "type": "esriFieldTypeOID"},
        {"name": "NOMBRE", "type": "esriFieldTypeString"},
    ],
    "exceededTransferLimit": False,
    "features": [
        {"attributes": {"OBJECTID": 1, "NOMBRE": "ESTACIÓN 1"}, "geometry": {"x": 1, "y": 2}},
        {"attributes": {"OBJECTID": 2, "NOMBRE": "ESTACIÓN 2"}, "geometry": {"x": 3, "y": 4}},
    ],
}


@pytest.fixture
async def client():
    c = esri.EsriClient()
    yield c
    await c.close()


# ─── URL shapes ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        LAYER,
        ROOT,
        FEATURE_LAYER,
        "https://services9.arcgis.com/abc/arcgis/rest/services/y/FeatureServer/0",
        "https://h.gov.co/server/rest/services/folder/svc/MapServer/12/",
    ],
)
def test_service_urls_are_recognised(url):
    assert esri.is_service_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://datosabiertos.bogota.gov.co/dataset/a/resource/b/download/shape.zip",
        "https://example.gov.co/geoserver/wfs?service=WFS",
        "https://example.gov.co/arcgis/rest/services/x/y/MapServer/4/query",
        "",
        "not a url",
    ],
)
def test_non_service_urls_are_rejected(url):
    assert esri.is_service_url(url) is False


def test_a_zipped_shapefile_labelled_esri_rest_is_refused():
    """Measured: the catalogue's `format` field is not a promise.

    The first Bogotá resource labelled ESRI REST points at `.../shape.zip`.
    Firing a query at it would produce an error no model could act on; refusing
    here produces one it can, naming the tool that *does* read files.
    """
    with pytest.raises(EsriError, match="city_read_resource_file"):
        esri.layer_url("https://datosabiertos.bogota.gov.co/d/a/resource/b/download/shape.zip")


def test_a_service_root_without_a_layer_is_refused():
    """There is no defensible default layer.

    Picking layer 0 would answer a question about a different dataset than the
    one asked about, and the answer would look completely normal.
    """
    with pytest.raises(EsriError, match="service root"):
        esri.layer_url(ROOT)


def test_a_service_root_with_a_layer_resolves():
    assert esri.layer_url(ROOT, 4) == LAYER


def test_a_layer_url_ignores_a_matching_layer_argument():
    assert esri.layer_url(LAYER, 4) == LAYER


def test_a_layer_url_refuses_a_contradicting_layer_argument():
    with pytest.raises(EsriError, match="already names layer 4"):
        esri.layer_url(LAYER, 7)


@pytest.mark.parametrize("layer", [-1, 10000, "4", 1.5])
def test_an_implausible_layer_index_is_refused(layer):
    with pytest.raises(EsriError):
        esri.layer_url(ROOT, layer)


def test_query_url_appends_the_query_endpoint():
    assert esri.query_url(LAYER) == LAYER + "/query"
    assert esri.query_url(LAYER + "/") == LAYER + "/query"
    assert esri.query_url(LAYER + "/query") == LAYER + "/query"


def test_service_host_extracts_the_hostname():
    assert esri.service_host(LAYER) == "serviciosgis.example.gov.co"


# ─── WHERE validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "where",
    [
        "LOCALIDAD = 'SUBA'",
        "AREA > 1000 AND TIPO <> 'X'",
        "NOMBRE LIKE '%PARQUE%'",
        "ANIO BETWEEN 2020 AND 2024",
        "COD IN (1, 2, 3)",
        "UPZ IS NULL OR UPZ = ''",
        "NOMBRE = 'BOGOTA D''C'",
    ],
)
def test_real_filter_clauses_are_accepted(where):
    """The comparison language a caller actually needs must survive the guard.

    A validator that refused LIKE or IN would be secure and useless; the tool
    exists to filter.
    """
    assert esri.validate_where(where) == where


@pytest.mark.parametrize(
    "where",
    [
        "1=1; DROP TABLE usuarios",
        "1=1 -- comment",
        "1=1 /* comment */",
        "1=1 OR 1=1 UNION SELECT * FROM secrets",
        "1=1; EXEC xp_cmdshell 'dir'",
        "DELETE FROM capa",
        "1=1 WAITFOR DELAY '0:0:20'",
    ],
)
def test_statement_breaking_clauses_are_refused(where):
    with pytest.raises(EsriError):
        esri.validate_where(where)


def test_an_unbalanced_quote_is_refused():
    """An odd number of quotes leaves a literal open, which is how an injected
    fragment reaches the parser as code rather than as text."""
    with pytest.raises(EsriError, match="odd number of single quotes"):
        esri.validate_where("NOMBRE = 'SUBA")


def test_an_empty_where_becomes_match_everything():
    assert esri.validate_where("") == "1=1"
    assert esri.validate_where(None) == "1=1"


def test_an_overlong_where_is_refused():
    with pytest.raises(EsriError, match="too long"):
        esri.validate_where("A = 1 AND " * 400)


# ─── Field validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["OBJECTID", "nombre_localidad", "Área_m2", "AÑO"])
def test_real_field_names_are_accepted(name):
    assert esri.validate_field(name) == name


@pytest.mark.parametrize("name", ["", "A;B", "A--B", "A'B", 'A"B', "A/*B", "A\x00B", "x" * 200])
def test_dangerous_field_names_are_refused(name):
    with pytest.raises(EsriError):
        esri.validate_field(name)


# ─── Aggregation specs ───────────────────────────────────────────────────────


def test_statistics_json_matches_what_arcgis_expects():
    out = json.loads(esri.build_statistics([{"fn": "count", "col": "OBJECTID", "alias": "n"}]))
    assert out == [
        {"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "n"}
    ]


def test_an_aggregation_without_a_column_is_refused():
    """ArcGIS has no count(*) form, so a missing column cannot be defaulted."""
    with pytest.raises(EsriError, match="no count"):
        esri.build_statistics([{"fn": "count"}])


@pytest.mark.parametrize(
    "spec", [[], [{"fn": "median", "col": "A"}], [{"fn": "count", "col": "A"}] * 11]
)
def test_bad_aggregation_specs_are_refused(spec):
    with pytest.raises(EsriError):
        esri.build_statistics(spec)


def test_an_aggregation_alias_is_validated_like_a_field():
    with pytest.raises(EsriError):
        esri.build_statistics([{"fn": "count", "col": "A", "alias": "n; DROP"}])


# ─── Requests ────────────────────────────────────────────────────────────────


async def test_a_query_goes_to_the_query_endpoint(client, httpx_mock):
    """The bug this whole module nearly shipped with.

    A layer root answers a GET with its own description and ignores every
    parameter: HTTP 200, a plausible `fields` list, and no `features`. The tool
    reported zero rows for a layer holding eleven, and nothing in the response
    indicated a problem. Asserting the path is the only cheap way to catch it.
    """
    httpx_mock.add_response(url=ANY, json=FEATURES)
    await client.query(LAYER, limit=2)
    assert httpx_mock.get_requests()[-1].url.path.endswith("/query")


async def test_service_info_does_not_append_query(client, httpx_mock):
    httpx_mock.add_response(url=ANY, json=LAYER_INFO)
    await client.service_info(LAYER)
    assert not httpx_mock.get_requests()[-1].url.path.endswith("/query")


async def test_service_info_refuses_a_non_service_address(client):
    with pytest.raises(EsriError, match="city_read_resource_file"):
        await client.service_info("https://x.gov.co/a/b/download/shape.zip")


async def test_an_error_body_under_http_200_is_raised(client, httpx_mock):
    """ArcGIS reports query failures with status 200 and an `error` object.

    A client trusting the status code would report success and hand back a dict
    with no rows in it — indistinguishable, to a model, from an empty dataset.
    """
    httpx_mock.add_response(
        url=ANY,
        status_code=200,
        json={"error": {"code": 400, "message": "Failed to execute query.", "details": []}},
    )
    with pytest.raises(EsriError, match="ArcGIS error 400"):
        await client.query(LAYER, where="NOSUCH = 1")


async def test_error_details_are_carried_into_the_message(client, httpx_mock):
    httpx_mock.add_response(
        url=ANY,
        json={
            "error": {
                "code": 400,
                "message": "Unable to complete operation.",
                "details": ["Unable to perform query operation."],
            }
        },
    )
    with pytest.raises(EsriError, match="Unable to perform query operation"):
        await client.query(LAYER)


async def test_a_real_http_error_is_raised(client, httpx_mock):
    httpx_mock.add_response(url=ANY, status_code=404, text="not found")
    with pytest.raises(EsriError, match="HTTP 404"):
        await client.service_info(LAYER)


async def test_an_html_response_says_so(client, httpx_mock):
    """An ArcGIS host answering HTML is a login page or a gateway, and saying
    that is more useful than a JSON parse error."""
    httpx_mock.add_response(url=ANY, text="<html>Sign in</html>")
    with pytest.raises(EsriError, match="authentication"):
        await client.service_info(LAYER)


async def test_a_non_object_response_is_refused(client, httpx_mock):
    httpx_mock.add_response(url=ANY, json=[1, 2, 3])
    with pytest.raises(EsriError, match="non-object"):
        await client.service_info(LAYER)


async def test_geometry_is_never_requested(client, httpx_mock):
    """A polygon layer answering with its coordinates would fill the model's
    context with vertices that answer nothing."""
    httpx_mock.add_response(url=ANY, json=FEATURES)
    await client.query(LAYER)
    assert httpx_mock.get_requests()[-1].url.params["returnGeometry"] == "false"


async def test_query_parameters_reach_the_service(client, httpx_mock):
    httpx_mock.add_response(url=ANY, json=FEATURES)
    await client.query(
        LAYER, where="TIPO = 'A'", out_fields=["OBJECTID", "NOMBRE"], limit=7, offset=14
    )
    params = httpx_mock.get_requests()[-1].url.params
    assert params["where"] == "TIPO = 'A'"
    assert params["outFields"] == "OBJECTID,NOMBRE"
    assert params["resultRecordCount"] == "7"
    assert params["resultOffset"] == "14"


async def test_the_row_ceiling_is_enforced(client, httpx_mock):
    httpx_mock.add_response(url=ANY, json=FEATURES)
    await client.query(LAYER, limit=99999)
    assert httpx_mock.get_requests()[-1].url.params["resultRecordCount"] == str(esri.MAX_RECORDS)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("AREA desc", "AREA DESC"), ("NOMBRE asc", "NOMBRE ASC"), ("NOMBRE", "NOMBRE")],
)
async def test_order_by_is_translated(client, httpx_mock, spec, expected):
    httpx_mock.add_response(url=ANY, json=FEATURES)
    await client.query(LAYER, order_by=spec)
    assert httpx_mock.get_requests()[-1].url.params["orderByFields"] == expected


async def test_count_returns_an_integer(client, httpx_mock):
    httpx_mock.add_response(url=ANY, json={"count": 11})
    assert await client.count(LAYER) == 11


async def test_an_unreadable_count_is_refused(client, httpx_mock):
    httpx_mock.add_response(url=ANY, json={"count": "muchos"})
    with pytest.raises(EsriError, match="Unreadable count"):
        await client.count(LAYER)


async def test_aggregate_sends_group_by_and_statistics(client, httpx_mock):
    httpx_mock.add_response(
        url=ANY,
        json={
            "fields": [{"name": "TIPO", "type": "esriFieldTypeString"}],
            "features": [{"attributes": {"TIPO": "No Definida", "N": 11}}],
        },
    )
    body = await client.aggregate(
        LAYER,
        aggregations=[{"fn": "count", "col": "OBJECTID", "alias": "n"}],
        group_by=["TIPO"],
        where="1=1",
    )
    params = httpx_mock.get_requests()[-1].url.params
    assert params["groupByFieldsForStatistics"] == "TIPO"
    assert json.loads(params["outStatistics"])[0]["statisticType"] == "count"
    assert body["features"][0]["attributes"]["N"] == 11


async def test_a_timeout_becomes_an_esri_error(client, httpx_mock):
    import httpx

    httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=ANY)
    with pytest.raises(EsriError, match="timeout"):
        await client.service_info(LAYER)


async def test_a_transport_error_becomes_an_esri_error(client, httpx_mock):
    import httpx

    httpx_mock.add_exception(httpx.ConnectError("dns"), url=ANY, is_reusable=True)
    with pytest.raises(EsriError, match="network error"):
        await client.service_info(LAYER)


async def test_the_client_can_be_closed_twice(client):
    await client.close()
    await client.close()


# ─── Formatters ──────────────────────────────────────────────────────────────


def test_service_info_drops_the_cartography():
    """ArcGIS metadata carries the full symbology, which no model needs and
    every model would pay for in context."""
    out = esri.format_service_info(LAYER_INFO, LAYER)
    assert "drawingInfo" not in out
    assert out["name"] == "Estaciónes Segunda Línea Metro"
    assert out["geometry_type"] == "Point"
    assert out["supports_statistics"] is True
    assert out["supports_pagination"] is True
    assert out["n_fields"] == 3
    assert out["fields"][1] == {"name": "NOMBRE", "type": "String", "alias": "Nombre"}
    # An alias identical to the name carries nothing, so it is dropped.
    assert out["fields"][2]["alias"] is None


def test_service_info_on_a_root_lists_layers_and_says_what_to_do():
    out = esri.format_service_info(
        {"layers": [{"id": 0, "name": "Uno"}, {"id": 4, "name": "Cuatro"}]}, ROOT
    )
    assert out["layers"] == [{"id": 0, "name": "Uno"}, {"id": 4, "name": "Cuatro"}]
    assert "layer" in out["note"]


def test_features_are_flattened_and_geometry_is_gone():
    out = esri.format_features(FEATURES, LAYER)
    assert out["rows_returned"] == 2
    assert out["rows"][0] == {"OBJECTID": 1, "NOMBRE": "ESTACIÓN 1"}
    assert "geometry" not in json.dumps(out)
    assert out["fields"][0]["type"] == "OID"


def test_an_empty_feature_collection_formats_cleanly():
    out = esri.format_features({}, LAYER)
    assert out["rows_returned"] == 0
    assert out["rows"] == []
