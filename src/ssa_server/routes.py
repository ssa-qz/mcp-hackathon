from starlette.responses import JSONResponse
from starlette.routing import Route
from ssa_server import __version__


async def health_endpoint(request):
    """Health check endpoint for container orchestrators and cloud.gov."""
    return JSONResponse(
        {
            "status": "ok",
            "service": "ssa-mcp-server",
            "version": __version__,
        }
    )


async def version_endpoint(request):
    """Version metadata endpoint."""
    return JSONResponse(
        {
            "service": "SSA MCP Server (GSA Hackathon)",
            "version": __version__,
            "mcp_protocol_version": "2025-11-25",
            "tier_support": {
                "tier_1_dataset_access": True,
                "tier_2_service_read": True,
                "tier_3_service_write_sandbox": True,
            },
        }
    )


http_routes = [
    Route("/health", health_endpoint, methods=["GET"]),
    Route("/version", version_endpoint, methods=["GET"]),
]
