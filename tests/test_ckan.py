"""Hermetic tests for the CKAN client (Bogotá). No network.

Every response body here is shaped like a real one from
``datosabiertos.bogota.gov.co``, including the details that bite: CKAN answers
200 with ``success: false`` for some errors, its column names carry spaces and
accents, and ``datastore_active`` is the field that decides whether a resource
can be queried at all.
"""

from __future__ import annotations

import json
import re

import pytest

from colombian_open_data_mcp import ckan

API = "https://datosabiertos.bogota.gov.co/api/3/action"
RID = "dc751251-95ef-48bb-9785-329a3a5e0bdf"


@pytest.fixture
async def client():
    c = ckan.CkanClient(ckan.BOGOTA)
    yield c
    await c.close()


def ok(result):
    """A successful CKAN envelope."""
    return {"help": "...", "success": True, "result": result}


# ─── Identifier validation ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "dc751251-95ef-48bb-9785-329a3a5e0bdf",
        "DC751251-95EF-48BB-9785-329A3A5E0BDF",
    ],
)
def test_valid_uuids_accepted(value):
    assert ckan.is_valid_uuid(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-uuid",
        "dc751251-95ef-48bb-9785",
        "dc751251-95ef-48bb-9785-329a3a5e0bdf/../../etc/passwd",
        "dc751251-95ef-48bb-9785-329a3a5e0bdf?foo=bar",
        "dc751251 95ef 48bb 9785 329a3a5e0bdf",
    ],
)
def test_invalid_uuids_rejected(value):
    assert not ckan.is_valid_uuid(value)


@pytest.mark.parametrize(
    "value",
    ["hurtos-bogota", "sivigila_2023", "a1", "dc751251-95ef-48bb-9785-329a3a5e0bdf"],
)
def test_valid_dataset_ids_accepted(value):
    assert ckan.is_valid_dataset_id(value)


@pytest.mark.parametrize(
    "value",
    ["", "a", "UPPERCASE", "has spaces", "slash/injection", "query?param", "semi;colon"],
)
def test_invalid_dataset_ids_rejected(value):
    assert not ckan.is_valid_dataset_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "Localidad",
        "Año",
        "INCLUIDOS EN VIGILANCIA CENTINELA",
        "tasa_x_100.000",
        "área (m2)",
        # Each of these broke a previous version of the rule, in this order.
        "MES:",
        "Nombre:",
        "Fecha & Hora",
        "VR. SUBSIDIO $",
        "Otro idioma Cual?",
        # The portal mangling its own encoding. Still a column somebody selects.
        "Correo electr¢nico",
        "PISCINA NI¥OS",
    ],
)
def test_real_column_names_accepted(value):
    """Taken from 1,308 column names surveyed across the live Bogotá and Cali
    DataStores, not invented.

    The character allowlist this replaced was wrong twice — once on a colon,
    once on an ampersand — which is why the rule is now a denylist. A public
    catalogue will keep publishing headers no enumeration anticipates.
    """
    assert ckan.is_valid_column(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "col; DROP TABLE x",
        "col--comment",
        "col/*x*/",
        "a" * 200,
        "col\nInjected",
        "col\tTabbed",
        "col\x00nul",
        # A whole malformed CSV row the portal exposes as one header name. Real,
        # and correctly refused — the semicolons are not decorative.
        "111006;Cuenta de ahorro;69600000;BANCO AGRARIO DE COLOMBIA;129.",
    ],
)
def test_dangerous_column_names_rejected(value):
    """Only four sequences plus control characters are refused, and each earns
    it: statement/comment breaks, and raw control characters in a request
    parameter. Refusing a column costs projection, never access."""
    assert not ckan.is_valid_column(value)


def test_a_sql_shaped_name_is_harmless_here_but_not_in_soql():
    """The two clients have genuinely different threat models, and this is the
    clearest illustration of it.

    On the CKAN path no SQL is ever built: column names go into a query-string
    parameter that httpx percent-encodes, CKAN matches them against its own
    schema and answers "no such field". So a quote-and-OR string is not a
    threat, only a typo, and refusing it would cost real column names for a
    defence against nothing.

    On the SoQL path the same string is composed into a query language the
    portal executes, so it is refused there. Asserting both together keeps a
    future reader from "fixing" the CKAN rule to match the SoQL one.
    """
    from colombian_open_data_mcp import soql

    probe = 'col" OR "1"="1'
    assert ckan.is_valid_column(probe)
    with pytest.raises(soql.SoqlError):
        soql.quote_ident(probe)


def test_the_rejected_semicolon_names_are_portal_garbage():
    """Every ';' name observed across 1,308 real ones was a whole malformed CSV
    row exposed as a single header, so refusing them costs nothing real."""
    assert not ckan.is_valid_column("111006;Cuenta de ahorro;69600000;BANCO AGRARIO;129.")
    assert not ckan.is_valid_column("99;0;")


async def test_rejection_message_says_what_to_do_instead(client):
    """A refusal the caller cannot act on is only half a message."""
    with pytest.raises(ckan.CkanError, match="Omit `columns`"):
        await client.datastore_search(RID, fields=["ok", "bad;col"])


async def test_404_on_a_flagged_resource_explains_the_portal_is_wrong(client, httpx_mock):
    """Measured: 20 of 47 resources the catalogue flagged as DataStore-backed
    answer 404 because no table exists. The bare CKAN error is unreadable to a
    model that was told the resource was queryable."""
    httpx_mock.add_response(
        url=re.compile(r".*/datastore_search.*"),
        status_code=404,
        json={"success": False, "error": {"__type": "Not Found Error"}},
    )
    with pytest.raises(ckan.CkanError, match="catalogue flags resource"):
        await client.datastore_search(RID)


# ─── Transport and error handling ────────────────────────────────────────────


async def test_action_returns_result(client, httpx_mock):
    httpx_mock.add_response(url=f"{API}/tag_list", json=ok(["salud", "movilidad"]))
    assert await client.action("tag_list") == ["salud", "movilidad"]


async def test_success_false_raises_even_on_http_200(client, httpx_mock):
    """CKAN's own failure mode: a 200 whose body says it failed."""
    httpx_mock.add_response(
        url=f"{API}/package_show?id=nope",
        json={"success": False, "error": {"message": "Not found"}},
    )
    with pytest.raises(ckan.CkanError, match="reported failure"):
        await client.package_show("nope")


async def test_http_error_raises_ckan_error(client, httpx_mock):
    """503 is retried before it is reported, so the stub answers every attempt."""
    httpx_mock.add_response(
        url=f"{API}/tag_list", status_code=503, text="upstream down", is_reusable=True
    )
    with pytest.raises(ckan.CkanError, match="HTTP 503"):
        await client.action("tag_list")
    assert len(httpx_mock.get_requests()) == 3


async def test_non_json_body_raises_ckan_error(client, httpx_mock):
    """The WAF answers HTML with a 200 sometimes; that must not crash the tool."""
    httpx_mock.add_response(url=f"{API}/tag_list", text="<html>blocked</html>")
    with pytest.raises(ckan.CkanError, match="non-JSON"):
        await client.action("tag_list")


# ─── Catalog ─────────────────────────────────────────────────────────────────

SAMPLE_DATASET = {
    "id": "11111111-2222-3333-4444-555555555555",
    "name": "hurtos-bogota",
    "title": "Hurtos en Bogotá",
    "notes": "N" * 500,
    "organization": {"name": "secretaria-seguridad", "title": "Secretaría de Seguridad"},
    "groups": [{"name": "seguridad", "title": "Seguridad y convivencia"}],
    "tags": [{"name": "hurto"}, {"name": "seguridad"}],
    "license_title": "Creative Commons Attribution",
    "metadata_created": "2024-01-05T10:00:00",
    "metadata_modified": "2026-05-02T08:30:00",
    "resources": [
        {
            "id": RID,
            "name": "Hurtos 2024 CSV",
            "format": "csv",
            "url": "https://datosabiertos.bogota.gov.co/dataset/x/resource/y/download/h.csv",
            "size": 12345,
            "datastore_active": True,
        },
        {
            "id": "99999999-8888-7777-6666-555555555555",
            "name": "Capa geográfica",
            "format": "SHP",
            "url": "https://example.org/capa.zip",
            "datastore_active": False,
        },
    ],
}


async def test_package_search_formats_and_counts_queryable_resources(client, httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/package_search.*"),
        json=ok({"count": 1917, "results": [SAMPLE_DATASET]}),
    )
    body = await client.package_search(query="hurtos", rows=1)
    out = ckan.format_search_response(body, client.portal)

    assert out["country"] == "CO"
    assert out["portal"] == "datosabiertos.bogota.gov.co"
    assert out["total"] == 1917
    d = out["datasets"][0]
    assert d["num_resources"] == 2
    # The whole point of the flag: one of these two can be queried, one cannot.
    assert d["queryable_resources"] == 1
    assert d["url"] == "https://datosabiertos.bogota.gov.co/dataset/hurtos-bogota"


async def test_long_notes_are_truncated(client, httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/package_search.*"), json=ok({"count": 1, "results": [SAMPLE_DATASET]})
    )
    body = await client.package_search(rows=1)
    notes = ckan.format_search_response(body, client.portal)["datasets"][0]["notes"]
    assert len(notes) <= ckan.NOTES_TRUNC + 1  # +1 for the ellipsis
    assert notes.endswith("…")


async def test_package_search_builds_fq_for_facets(client, httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/package_search.*"), json=ok({"count": 0, "results": []})
    )
    await client.package_search(organization="secretaria-seguridad", group="seguridad", tag="hurto")
    request = httpx_mock.get_requests()[-1]
    fq = request.url.params["fq"]
    assert "organization:secretaria-seguridad" in fq
    assert "groups:seguridad" in fq
    assert 'tags:"hurto"' in fq


async def test_package_search_rejects_injected_organization(client):
    with pytest.raises(ckan.CkanError, match="organization"):
        await client.package_search(organization="x OR *:*")


async def test_package_show_rejects_bad_id(client):
    with pytest.raises(ckan.CkanError, match="dataset id"):
        await client.package_show("../../admin")


async def test_site_stats_reports_no_sql(client, httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r".*/package_search.*"), json=ok({"count": 1917, "results": []})
    )
    httpx_mock.add_response(url=f"{API}/group_list", json=ok(["a"] * 28))
    httpx_mock.add_response(url=f"{API}/organization_list", json=ok(["a"] * 60))
    httpx_mock.add_response(url=f"{API}/tag_list", json=ok(["a"] * 3181))

    stats = await client.site_stats()
    assert stats["platform"] == "ckan"
    assert stats["total_datasets"] == 1917
    assert stats["total_groups"] == 28
    assert stats["total_organizations"] == 60
    assert stats["total_tags"] == 3181
    # Stated, not implied: this is why there is no bogota aggregation tool.
    assert stats["datastore_sql_available"] is False


# ─── DataStore ───────────────────────────────────────────────────────────────

DATASTORE_BODY = {
    "total": 464,
    "fields": [
        {"id": "_id", "type": "int"},
        {"id": "Localidad", "type": "text"},
        {"id": "SEROPOSITIVOS", "type": "text"},
    ],
    "records": [
        {"_id": 1, "Localidad": "Bosa", "SEROPOSITIVOS": "1"},
        {"_id": 2, "Localidad": "Santa Fe", "SEROPOSITIVOS": "1"},
    ],
}


async def test_datastore_search_formats_rows_and_fields(client, httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*/datastore_search.*"), json=ok(DATASTORE_BODY))
    body = await client.datastore_search(RID, limit=2)
    out = ckan.format_datastore_result(body, RID, client.portal)

    assert out["resource_id"] == RID
    assert out["total_rows_matching"] == 464
    assert out["rows_returned"] == 2
    assert {"name": "Localidad", "type": "text"} in out["fields"]


async def test_datastore_search_rejects_non_uuid_resource(client):
    with pytest.raises(ckan.CkanError, match="resource UUID"):
        await client.datastore_search("hurtos-bogota")


async def test_datastore_filters_are_sent_as_json(client, httpx_mock):
    """CKAN matches filters as a JSON object, never as interpolated SQL."""
    httpx_mock.add_response(url=re.compile(r".*/datastore_search.*"), json=ok(DATASTORE_BODY))
    await client.datastore_search(RID, filters={"Localidad": "Bosa"})
    sent = httpx_mock.get_requests()[-1].url.params["filters"]
    assert json.loads(sent) == {"Localidad": "Bosa"}


async def test_datastore_rejects_injected_filter_column(client):
    with pytest.raises(ckan.CkanError, match="filter column"):
        await client.datastore_search(RID, filters={"x; DROP TABLE y": 1})


async def test_datastore_rejects_injected_field(client):
    with pytest.raises(ckan.CkanError, match="column name"):
        await client.datastore_search(RID, fields=["Localidad", "a--b"])


async def test_datastore_accepts_accented_columns(client, httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*/datastore_search.*"), json=ok(DATASTORE_BODY))
    await client.datastore_search(RID, fields=["Año", "INCLUIDOS EN VIGILANCIA CENTINELA"])
    assert "fields" in httpx_mock.get_requests()[-1].url.params


async def test_datastore_sort_direction_validated(client):
    with pytest.raises(ckan.CkanError, match="asc or desc"):
        await client.datastore_search(RID, sort="Localidad sideways")


async def test_datastore_sort_column_validated(client):
    with pytest.raises(ckan.CkanError, match="sort column"):
        await client.datastore_search(RID, sort="col; DROP TABLE x desc")


async def test_datastore_sort_defaults_to_asc(client, httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*/datastore_search.*"), json=ok(DATASTORE_BODY))
    await client.datastore_search(RID, sort="Localidad")
    assert httpx_mock.get_requests()[-1].url.params["sort"] == "Localidad asc"


async def test_datastore_limit_is_capped(client, httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*/datastore_search.*"), json=ok(DATASTORE_BODY))
    await client.datastore_search(RID, limit=999999)
    assert httpx_mock.get_requests()[-1].url.params["limit"] == "1000"


# ─── Portal descriptor ───────────────────────────────────────────────────────


def test_portal_urls_are_built_from_the_host():
    """The Dominican client hardcoded its host in every permalink; this one
    derives them, which is what makes a second city possible."""
    p = ckan.CkanPortal(
        key="x",
        host="ejemplo.gov.co",
        name="X",
        city="X",
        ckan_version="2.10",
        approx_datasets=10,
        datastore_coverage="all of it",
    )
    assert p.base_url == "https://ejemplo.gov.co"
    assert p.api_url == "https://ejemplo.gov.co/api/3/action"
    assert p.dataset_url("d") == "https://ejemplo.gov.co/dataset/d"
    assert p.organization_url("o") == "https://ejemplo.gov.co/organization/o"
    assert p.group_url("g") == "https://ejemplo.gov.co/group/g"


def test_format_resource_marks_geospatial_as_not_queryable():
    r = ckan.format_resource(
        {"id": RID, "format": "shp", "url": "https://x/y.zip", "datastore_active": False}
    )
    assert r["format"] == "SHP"
    assert r["queryable"] is False


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("Salud Pública", 'tags:"Salud Pública"'),
        ('hurto" OR organization:"otra', 'tags:"hurto\\" OR organization:\\"otra"'),
        ("a\\b", 'tags:"a\\\\b"'),
    ],
)
async def test_package_search_escapes_the_tag_phrase(client, httpx_mock, tag, expected):
    """A quote inside the tag must not close the Solr phrase. Tags are free
    text on every portal, so they cannot go through the slug check that
    organizations and groups use; escaping is the only option left."""
    httpx_mock.add_response(
        url=re.compile(r".*/package_search.*"), json=ok({"count": 0, "results": []})
    )
    await client.package_search(tag=tag)
    fq = httpx_mock.get_requests()[-1].url.params["fq"]
    assert fq == expected


async def test_the_ckan_client_installs_the_netguard_hook(client):
    """The privacy notes promise every redirect hop is checked. That is only
    true if every client carries the hook, not just the two that reach hosts a
    catalogue names."""
    from colombian_open_data_mcp.netguard import guard_request_hook

    http = await client._get_client()
    assert guard_request_hook in http.event_hooks["request"]
