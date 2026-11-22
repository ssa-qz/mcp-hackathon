import asyncio

from fastmcp import Client
from starlette.testclient import TestClient

from ssa_server.app import app, create_app, create_server, mcp


def test_unified_server_registration():
    server = create_server()
    assert server.name == "SSA MCP Server"
    assert "open data" in server.instructions.lower()
    assert "confirm before writing" in server.instructions.lower()


def test_http_health_and_version_routes():
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        health_resp = client.get("/health")
        assert health_resp.status_code == 200
        health_data = health_resp.json()
        assert health_data["status"] == "ok"
        assert health_data["service"] == "ssa-mcp-server"

        version_resp = client.get("/version")
        assert version_resp.status_code == 200
        version_data = version_resp.json()
        assert "SSA MCP Server" in version_data["service"]
        assert version_data["tier_support"]["tier_1_dataset_access"] is True
        assert version_data["tier_support"]["tier_2_service_read"] is True
        assert version_data["tier_support"]["tier_3_service_write_sandbox"] is True


def test_unlisted_host_is_rejected_on_mcp_but_probes_still_answer():
    with TestClient(create_app(), base_url="http://evil.example.com") as client:
        assert client.post("/mcp", json={}).status_code == 400
        assert client.get("/health").status_code == 200


def test_configured_deployment_host_reaches_mcp_endpoint():
    deployed = create_app(allowed_hosts=["ssa-mcp-server.app.cloud.gov"])
    with TestClient(deployed, base_url="https://ssa-mcp-server.app.cloud.gov") as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "host-test", "version": "1"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert response.status_code == 200
        assert '"protocolVersion"' in response.text


def test_tool_schemas_publish_and_enforce_pydantic_limits():
    async def _test():
        async with Client(mcp) as client:
            tools = {t.name: t.input_schema for t in await client.list_tools()}
            assert tools["get_all_time_popular_names"]["properties"]["top_n"]["maximum"] == 50
            assert tools["compare_names"]["properties"]["names"]["maxItems"] == 20
            assert tools["get_top_names_by_year"]["properties"]["year"]["minimum"] == 1880

            oversized = [
                ("get_all_time_popular_names", {"sex": "F", "top_n": 100000}),
                ("compare_names", {"names": ["Emma"] * 21, "sex": "F"}),
                ("get_baby_name_popularity", {"name": "Emma", "sex": "F", "start_year": 1700}),
                ("get_cpi_cola_indicators", {"recent_months": 99}),
            ]
            for tool, args in oversized:
                result = await client.call_tool(tool, args, raise_on_error=False)
                assert result.is_error, f"{tool} accepted out-of-range arguments {args}"

    asyncio.run(_test())


def test_unified_mcp_discovery_all_14_tools():
    async def _test():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            expected = {
                # Tier 1
                "get_baby_name_popularity",
                "get_top_names_by_year",
                "get_name_trend",
                "compare_names",
                "get_all_time_popular_names",
                # Tier 2
                "find_field_offices",
                "get_field_office_details",
                "geocode_address",
                "get_cpi_cola_indicators",
                # Tier 3
                "book_field_office_appointment",
                "lookup_field_office_appointment",
                "cancel_field_office_appointment",
                "estimate_social_security_cola",
                "find_offices_near_address",
            }
            assert tool_names == expected

            resources = await client.list_resources()
            uris = {str(r.uri) for r in resources}
            assert "ssa://datasets/baby-names/metadata" in uris
            assert "ssa://field-offices/metadata" in uris
            assert "public-services://metadata" in uris

            prompts = await client.list_prompts()
            prompt_names = {p.name for p in prompts}
            assert "demographic_name_analysis" in prompt_names
            assert "plan_field_office_visit" in prompt_names
            assert "schedule_appointment_intake" in prompt_names
            assert "assess_office_jurisdiction_and_cola" in prompt_names

    asyncio.run(_test())
