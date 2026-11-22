from fastmcp import FastMCP
from ssa_server.resources import (
    appointments,
    baby_names,
    field_offices,
    public_services,
)


def register_resources(mcp: FastMCP) -> None:
    """Register all modular MCP resources with the server instance."""
    baby_names.register(mcp)
    field_offices.register(mcp)
    appointments.register(mcp)
    public_services.register(mcp)
