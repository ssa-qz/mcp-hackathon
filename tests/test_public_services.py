import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

from ssa_server.core.public_services import (
    estimate_cola as get_estimate_cola,
    geocode_address,
    get_cpi_cola_indicators,
    get_public_services_metadata,
)
from ssa_server.app import mcp


# ============================================================================
# Core Integration Unit Tests (Deterministic with Mock HTTP Responses)
# ============================================================================

def test_geocode_address_geographies_success():
    fake_payload = {
        "result": {
            "addressMatches": [
                {
                    "matchedAddress": "10416 S EASTERN AVE, HENDERSON, NV, 89052",
                    "coordinates": {"x": -115.1115, "y": 35.9982},
                    "tigerLine": {"tigerLineId": "12345678", "side": "L"},
                    "addressComponents": {
                        "city": "HENDERSON",
                        "state": "NV",
                        "zip": "89052",
                    },
                    "geographies": {
                        "Counties": [
                            {
                                "NAME": "Clark County",
                                "GEOID": "32003",
                                "STATE": "32",
                                "COUNTY": "003",
                            }
                        ],
                        "120th Congressional Districts": [
                            {
                                "NAME": "Congressional District 1",
                                "GEOID": "3201",
                            }
                        ],
                        "Census Tracts": [
                            {
                                "NAME": "Census Tract 53.49",
                                "GEOID": "32003005349",
                                "TRACT": "005349",
                            }
                        ],
                    },
                }
            ]
        }
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = fake_payload

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = mock_resp

    res = geocode_address("10416 S Eastern Ave, Henderson, NV 89052", client=mock_client)
    assert res["matched"] is True
    assert res["latitude"] == 35.9982
    assert res["longitude"] == -115.1115
    assert res["matched_address"] == "10416 S EASTERN AVE, HENDERSON, NV, 89052"
    assert res["county"]["name"] == "Clark County"
    assert res["county"]["geoid"] == "32003"
    assert res["congressional_district"]["name"] == "Congressional District 1"
    assert res["census_tract"]["name"] == "Census Tract 53.49"
    assert "Census" in res["notice"]


def test_geocode_address_no_match():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"result": {"addressMatches": []}}

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = mock_resp

    res = geocode_address("Nonexistent Place 99999", client=mock_client)
    assert res["matched"] is False
    assert "No matching address" in res["message"]


def test_geocode_address_empty_input():
    with pytest.raises(ValueError, match="must not be empty"):
        geocode_address("   ")


def test_geocode_address_network_error():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = httpx.ConnectError("Connection refused")

    res = geocode_address("10416 S Eastern Ave, Henderson, NV 89052", client=mock_client)
    assert res["matched"] is False
    assert res["error"] == "Census Geocoder request could not be reached."
    assert res["hint"]


def test_upstream_errors_never_echo_the_request_url():
    """httpx error text embeds the full URL (the user's address / an API key) - it must not leak."""
    request = httpx.Request("GET", "https://geocoding.geo.census.gov/x?address=10416+S+Eastern+Ave&registrationkey=SECRET")
    failures = [
        httpx.ConnectTimeout("timed out", request=request),
        httpx.HTTPStatusError("boom " + str(request.url), request=request, response=httpx.Response(503, request=request)),
        httpx.ConnectError(f"cannot connect to {request.url}", request=request),
    ]
    for exc in failures:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.side_effect = exc
        for res in (geocode_address("10416 S Eastern Ave", client=mock_client),
                    get_cpi_cola_indicators(client=mock_client)):
            text = str(res)
            assert "error" in res and "hint" in res
            assert "Eastern" not in res["error"] and "SECRET" not in text and "http" not in res["error"]


def test_rate_limit_is_reported_as_actionable():
    request = httpx.Request("GET", "https://api.bls.gov/publicAPI/v1/timeseries/data/CWUR0000SA0")
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request))
    res = get_cpi_cola_indicators(client=mock_client)
    assert "HTTP 429" in res["error"] and "Rate limit" in res["hint"]


def test_get_cpi_cola_indicators_success():
    fake_items = [
        {"year": "2026", "period": f"M{12-i:02d}", "periodName": "Month", "value": str(300.0 + i), "latest": "true" if i == 0 else "false"}
        for i in range(14)
    ]
    # Set known values: month 0 is 300, month 12 is 250 -> +20%
    fake_items[0]["value"] = "300.0"
    fake_items[12]["value"] = "250.0"

    fake_payload = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": "CWUR0000SA0",
                    "data": fake_items,
                }
            ]
        },
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = fake_payload

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = mock_resp

    res = get_cpi_cola_indicators(series="CPI-W", recent_months=6, client=mock_client)
    assert res["series"] == "CPI-W"
    assert res["series_id"] == "CWUR0000SA0"
    assert res["recent_observations_count"] == 6
    assert res["latest_reading"]["index_value"] == 300.0
    assert res["annual_12_month_inflation_rate_percent"] == 20.0
    assert "COLA" in res["ssa_cola_relevance"]


def test_get_cpi_cola_indicators_invalid_inputs():
    with pytest.raises(ValueError, match="series"):
        get_cpi_cola_indicators(series="INVALID")  # type: ignore

    with pytest.raises(ValueError, match="recent_months"):
        get_cpi_cola_indicators(recent_months=0)

    with pytest.raises(ValueError, match="recent_months"):
        get_cpi_cola_indicators(recent_months=25)


def test_get_cpi_cola_indicators_api_error():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "status": "REQUEST_FAILED",
        "message": ["Series does not exist"],
    }
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = mock_resp

    res = get_cpi_cola_indicators(series="CPI-W", client=mock_client)
    assert "BLS API error" in res["error"]


def test_public_services_metadata():
    meta = get_public_services_metadata()
    assert "Federal Public Services Read Integration" in meta["service_name"]
    assert "census_geographies" in meta["endpoints"]
    assert "bls_cpi_api" in meta["endpoints"]
    assert len(meta["capabilities"]) >= 2
    assert "COLA" in meta["mission_alignment"]


# ============================================================================
# FastMCP Server Discovery & Tool Execution Tests
# ============================================================================

def test_fastmcp_discovery_and_tools():
    from fastmcp import Client

    async def _run():
        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            assert "geocode_address" in tools
            assert "get_cpi_cola_indicators" in tools
            assert "get_weather_alerts" not in tools

            # Verify tool annotations
            for t in (tools["geocode_address"], tools["get_cpi_cola_indicators"]):
                assert t.annotations is not None
                ann = t.annotations.model_dump(by_alias=True)
                assert ann["readOnlyHint"] is True
                assert ann["idempotentHint"] is True
                assert ann["openWorldHint"] is True

            # Verify resources
            resources = await client.list_resources()
            uris = [str(r.uri) for r in resources]
            assert "public-services://metadata" in uris
            meta_res = await client.read_resource("public-services://metadata")
            assert meta_res[0].mime_type == "application/json"
            assert "Federal Public Services" in meta_res[0].text

            # Verify prompt
            prompts = await client.list_prompts()
            p_names = [p.name for p in prompts]
            assert "assess_office_jurisdiction_and_cola" in p_names
            prompt = await client.get_prompt("assess_office_jurisdiction_and_cola", {"address": "10416 S Eastern Ave"})
            assert "10416 S Eastern Ave" in prompt.messages[0].content.text
            assert "CPI-W" in prompt.messages[0].content.text

    asyncio.run(_run())



# ============================================================================
# COLA computation (statutory formula) - validated against SSA's published COLAs
# ============================================================================

def bls_client(cpi_w: dict[tuple[int, int], float]) -> MagicMock:
    """Mock BLS client returning the given {(year, month): value} newest-first, as BLS does."""
    items = [{"year": str(y), "period": f"M{m:02d}", "periodName": "x", "value": str(v)}
             for (y, m), v in sorted(cpi_w.items(), reverse=True)]
    items.insert(0, {"year": "2099", "period": "M13", "value": "999"})  # annual average: ignored
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"data": items}]}}
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = response
    return client


# Published CPI-W (1982-84=100). SSA: Q3 2023 avg 301.236, Q3 2024 avg 308.729 -> 2025 COLA 2.5%;
# Q3 2025 avg 317.265 -> 2026 COLA 2.8%.
CPI_W = {
    (2023, 7): 299.899, (2023, 8): 301.551, (2023, 9): 302.257,
    (2024, 7): 308.501, (2024, 8): 308.640, (2024, 9): 309.046,
    (2025, 7): 316.349, (2025, 8): 317.306, (2025, 9): 318.139,
}


def test_estimate_cola_reproduces_published_2025_cola():
    data = {k: v for k, v in CPI_W.items() if k[0] <= 2024}
    cola = get_estimate_cola(bls_client(data))["determined_cola"]
    assert cola["q3_average_cpi_w"] == {"2024": 308.729, "2023": 301.236}
    assert cola["cola_percent"] == 2.5
    assert cola["benefits_payable_from"] == "January 2025"


def test_estimate_cola_reproduces_published_2026_cola_and_projects_next():
    data = {**CPI_W, (2025, 10): 319.0, (2025, 11): 320.0}
    result = get_estimate_cola(bls_client(data))
    assert result["determined_cola"]["cola_percent"] == 2.8
    assert result["determined_cola"]["benefits_payable_from"] == "January 2026"
    projection = result["provisional_projection"]
    assert projection["months_used"] == ["2025-10", "2025-11"]
    assert projection["for_benefits_payable_from"] == "January 2027"
    assert "NOT an official estimate" in projection["label"]


def test_estimate_cola_never_negative_and_needs_two_quarters():
    deflation = {(2014, m): 240.0 for m in (7, 8, 9)} | {(2015, m): 238.0 for m in (7, 8, 9)}
    assert get_estimate_cola(bls_client(deflation))["determined_cola"]["cola_percent"] == 0.0
    one_quarter = {(2025, m): 317.0 for m in (7, 8, 9)}
    assert "two complete Q3" in get_estimate_cola(bls_client(one_quarter))["error"]
