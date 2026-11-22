import json
from fastmcp import FastMCP
from ssa_server.core.public_services import get_public_services_metadata


def register(mcp: FastMCP) -> None:
    @mcp.resource("public-services://metadata", mime_type="application/json")
    def public_services_metadata_resource() -> str:
        """Documentation and catalog metadata for Census Geocoder and BLS public service integrations."""
        return json.dumps(get_public_services_metadata(), indent=2)
