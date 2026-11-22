"""Host, Origin/CORS, and probe behavior of the unified HTTP app, for both MCP transports."""

import json

import pytest
from starlette.testclient import TestClient

from ssa_server.app import create_app

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "transport-test", "version": "1.0"},
    },
}


@pytest.fixture(params=["http", "sse"])
def protected_transport(request):
    with TestClient(create_app(transport=request.param), base_url="http://127.0.0.1:8080") as client:
        yield client, "/mcp" if request.param == "http" else "/sse"


@pytest.mark.parametrize(
    "origin",
    [
        "https://untrusted.invalid",
        "http://localhost.untrusted.invalid:3000",
        "http://127.0.0.1.untrusted.invalid",
        "http://localhost:3000/path",
        "null",
        "",
    ],
)
def test_transport_rejects_nonlocal_preflight(protected_transport, origin):
    client, path = protected_transport
    response = client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "http://localhost:3000",
        "https://localhost:5173",
        "http://127.0.0.1",
        "http://127.0.0.1:6274",
        "https://127.0.0.1:8443",
    ],
)
def test_transport_allows_local_preflight(protected_transport, origin):
    client, path = protected_transport
    response = client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,mcp-protocol-version,mcp-session-id,last-event-id",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers
    assert "origin" in response.headers["vary"].lower()


@pytest.mark.parametrize("origin", [None, "http://localhost:3000", "http://127.0.0.1:6274"])
def test_transport_allows_native_and_local_clients(protected_transport, origin):
    client, path = protected_transport
    headers = {"Accept": "application/json, text/event-stream"}
    if origin is not None:
        headers["Origin"] = origin
    response = client.post(path, headers=headers, json=INITIALIZE)
    assert response.status_code == (200 if path == "/mcp" else 405)
    if path == "/mcp":
        assert '"serverInfo"' in response.text
    if origin is not None:
        assert response.headers["access-control-allow-origin"] == origin
        assert "mcp-session-id" in response.headers["access-control-expose-headers"]
    else:
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", [None, "http://localhost:3000"])
def test_transport_rejects_untrusted_host(protected_transport, origin):
    client, path = protected_transport
    headers = {"Host": "untrusted.invalid"}
    if origin is not None:
        headers["Origin"] = origin
    response = client.post(path, headers=headers)
    assert response.status_code == 400
    assert response.text == "Invalid host header"


def test_nonlocal_origin_gets_no_cors_headers_on_actual_request():
    with TestClient(create_app(), base_url="http://127.0.0.1:8080") as client:
        response = client.post(
            "/mcp",
            headers={"Origin": "https://untrusted.invalid", "Accept": "application/json, text/event-stream"},
            json=INITIALIZE,
        )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_http_initialize_reports_identity_and_instructions():
    with TestClient(create_app(), base_url="http://127.0.0.1:8080") as client:
        response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"}, json=INITIALIZE)
    assert response.status_code == 200
    message = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
    assert message["result"]["serverInfo"]["name"] == "SSA MCP Server"
    assert "confirm" in message["result"]["instructions"].lower()
