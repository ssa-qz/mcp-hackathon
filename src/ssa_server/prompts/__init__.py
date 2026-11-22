from fastmcp import FastMCP
from ssa_server.prompts import (
    appointments,
    baby_names,
    field_offices,
    public_services,
)


def register_prompts(mcp: FastMCP) -> None:
    """Register all modular MCP prompt templates with the server instance."""
    baby_names.register(mcp)
    field_offices.register(mcp)
    appointments.register(mcp)
    public_services.register(mcp)
