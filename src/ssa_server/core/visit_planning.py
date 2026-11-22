"""Goal-level workflow: "Which SSA office should I visit? I live at <address>."

Combines the live Census Geocoder (normalizes a free-form address and resolves county and
congressional district) with the local SSA field-office directory. The directory has no
coordinates or service-area assignments, so matches are explained by *basis* (same ZIP, same
city, or same state) rather than claimed to be the nearest or the assigned office.
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ssa_server.core.field_offices import search_field_offices
from ssa_server.core.public_services import geocode_address

ASSIGNMENT_CAVEAT = (
    "SSA assigns each address to a specific servicing office. These matches are based on the "
    "office's own address only (not distance or service area); confirm the assigned office with "
    "SSA's online Field Office Locator on ssa.gov before visiting."
)


def find_offices_near_address(
    address: str,
    limit: int = 5,
    csv_path: Optional[Path] = None,
    geocode: Callable[..., Dict[str, Any]] = geocode_address,
    **geocode_options: Any,
) -> Dict[str, Any]:
    """Geocode a resident's address, then list SSA offices sharing its ZIP, city, or state."""
    if not 1 <= limit <= 20:
        raise ValueError("Parameter 'limit' must be between 1 and 20.")

    geo = geocode(address, **geocode_options)
    if not geo.get("matched"):
        return {
            "query_address": address.strip(),
            "address_matched": False,
            **{k: geo[k] for k in ("error", "hint", "message") if k in geo},
            "next_step": "Ask the user for a complete street address with city, state, and ZIP code.",
        }

    components = geo.get("address_components") or {}
    state = (components.get("state") or "").upper() or None
    city = components.get("city") or None
    zip_code = (components.get("zip") or "")[:5] or None

    searches = [
        ("same ZIP code", {"zip_code": zip_code, "state": state}),
        ("same city", {"city": city, "state": state}),
        ("same state (not ranked by distance)", {"state": state}),
    ]
    match_basis, result = "none", {"offices": [], "total_matches": 0}
    for basis, filters in searches:
        if not all(filters.values()):
            continue
        result = search_field_offices(**filters, limit=limit, csv_path=csv_path)
        if result["total_matches"]:
            match_basis = basis
            break

    return {
        "query_address": address.strip(),
        "address_matched": True,
        "matched_address": geo.get("matched_address"),
        "resident_jurisdiction": {
            "county": geo.get("county"),
            "congressional_district": geo.get("congressional_district"),
        },
        "match_basis": match_basis,
        "total_matches": result["total_matches"],
        "offices": result["offices"],
        "next_step": "Use get_field_office_details with an office_code for published weekday hours.",
        "caveat": ASSIGNMENT_CAVEAT,
    }
