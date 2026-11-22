from typing import Optional
from fastmcp import FastMCP
from ssa_server.core.field_offices import (
    get_field_office_details as query_field_office_details,
    search_field_offices,
)
from ssa_server.core.visit_planning import find_offices_near_address as query_offices_near_address
from ssa_server.config import settings
from ssa_server.utils import agent_errors
from ssa_server.models import (
    AddressString,
    NearbyLimit,
    OfficeCode,
    OfficeZip,
    PageLimit,
    PageOffset,
    SearchText,
    StateCode,
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="find_field_offices",
        annotations={
            "title": "Search SSA Field Offices",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def find_field_offices(
        state: Optional[StateCode] = None,
        city: Optional[SearchText] = None,
        zip_code: Optional[OfficeZip] = None,
        name: Optional[SearchText] = None,
        limit: PageLimit = 20,
        offset: PageOffset = 0,
    ) -> dict:
        """Find SSA offices by location or name and return codes, addresses, and phone numbers.

        Filters are combined with AND. With no filters, browse the directory in state/city/name/code order.
        Use get_field_office_details with a returned office_code for fax and published weekday hours.
        ZIP matching is by office address, not the ZIP codes served or distance from a visitor.

        Args:
            state: Two-letter state or territory abbreviation, e.g. 'NV' or 'PR'. Case-insensitive.
            city: Exact city name, case-insensitive, e.g. 'Henderson'.
            zip_code: Five-digit office-location ZIP string. Preserve leading zeros, e.g. '06108'.
            name: Case-insensitive substring of the office name, e.g. 'Dallas'.
            limit: Maximum number of offices to return (1-50, default 20).
            offset: Number of matches to skip; use next_offset from a previous response.

        Returns:
            Matching office summaries, total match count, pagination, source filename, and data limitations.
        """
        return search_field_offices(
            state=state,
            city=city,
            zip_code=zip_code,
            name=name,
            limit=limit,
            offset=offset,
            csv_path=settings.FIELD_OFFICES_CSV_PATH,
        )

    @mcp.tool(
        name="get_field_office_details",
        annotations={
            "title": "Get SSA Field Office Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def get_field_office_details(office_code: OfficeCode) -> dict:
        """Retrieve an SSA office's address, phone, fax, and published Monday-Friday hours.

        Args:
            office_code: Exact office code from find_field_offices, e.g. 'A00'. Case-insensitive; preserve leading zeros.

        Returns:
            found, office details (or null when not found), source filename, and data limitations.
            Missing values are null. Weekend hours and current open/closed status are not supplied.
            CLOSED markers and unusual hour values are preserved from the CSV, not corrected or interpreted.
        """
        return query_field_office_details(
            office_code=office_code,
            csv_path=settings.FIELD_OFFICES_CSV_PATH,
        )

    @mcp.tool(
        name="find_offices_near_address",
        annotations={
            "title": "Find SSA Offices for a Resident's Address",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @agent_errors
    def find_offices_near_address(address: AddressString, limit: NearbyLimit = 5) -> dict:
        """Find SSA field offices for someone who gives their home or street address.

        Use when the user says where they live ("I live at ..., which office do I go to?").
        Verifies and normalizes the address with the live U.S. Census Geocoder, reports the
        resident's county and congressional district, then lists offices sharing the address's
        ZIP code, else city, else state. Results are matched by office address only - not
        distance or SSA's official service-area assignment - so relay the returned caveat.

        Args:
            address: Single-line street address with city, state, and ZIP (e.g. '10416 S Eastern Ave, Henderson, NV 89052').
            limit: Maximum offices to return (1-20, default 5).

        Returns:
            address_matched, matched_address, resident_jurisdiction, match_basis ('same ZIP code',
            'same city', 'same state (not ranked by distance)', or 'none'), offices, and caveat.
        """
        return query_offices_near_address(
            address=address,
            limit=limit,
            csv_path=settings.FIELD_OFFICES_CSV_PATH,
            benchmark=settings.CENSUS_BENCHMARK,
            vintage=settings.CENSUS_VINTAGE,
        )
