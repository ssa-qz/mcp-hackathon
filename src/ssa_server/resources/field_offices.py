import json
from fastmcp import FastMCP
from ssa_server.core.field_offices import (
    get_field_office_details,
    get_field_office_metadata,
)
from ssa_server.config import settings


def register(mcp: FastMCP) -> None:
    @mcp.resource("ssa://field-offices/metadata", mime_type="application/json")
    def field_office_metadata() -> str:
        """Dataset coverage, office counts by state/territory, available fields, and snapshot limitations."""
        return json.dumps(get_field_office_metadata(csv_path=settings.FIELD_OFFICES_CSV_PATH), indent=2)

    @mcp.resource("ssa://field-offices/offices/{office_code}", mime_type="application/json")
    def field_office_resource(office_code: str) -> str:
        """Published contact details and weekday hours for an office code returned by find_field_offices."""
        return json.dumps(get_field_office_details(office_code, csv_path=settings.FIELD_OFFICES_CSV_PATH), indent=2)
