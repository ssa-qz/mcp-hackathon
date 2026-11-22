import json
from fastmcp import FastMCP
from ssa_server.core.sandbox_appointments import get_appointments_metadata
from ssa_server.config import settings


def register(mcp: FastMCP) -> None:
    # Individual appointments are intentionally NOT exposed as resources: a URI template keyed
    # only by confirmation code would bypass the phone verification required by the tools.
    @mcp.resource("ssa://field-offices/appointments/metadata", mime_type="application/json")
    def appointments_metadata_resource() -> str:
        """Booking rules, supported service types, and aggregate counts for the sandbox appointment service."""
        return json.dumps(get_appointments_metadata(db_path=settings.APPOINTMENTS_DB_PATH), indent=2)
