import csv
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from ssa_server.core import DATA_DIR

DEFAULT_CSV_PATH = DATA_DIR / "FO-Address-Open-Close-Times.csv"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
DATA_NOTICE = (
    "Undated local CSV snapshot, not live office status. Confirm hours before visiting; "
    "holiday closures, weekend hours, time zones, and appointment availability are not supplied. "
    "ZIP searches match office addresses, not service-area assignments or nearest offices."
)
REQUIRED_COLUMNS = (
    "OFFICE CODE", "OFFICE NAME", "ADDRESS LINE 1", "ADDRESS LINE 2", "ADDRESS LINE 3",
    "CITY", "STATE", "ZIP CODE", "PHONE", "FAX",
    *(f"{day.upper()} {kind} TIME" for day in WEEKDAYS for kind in ("OPEN", "CLOSE")),
)


def _csv_path(csv_path: Optional[Path]) -> Path:
    return Path(csv_path) if csv_path is not None else DEFAULT_CSV_PATH


def _search_text(value: Optional[str], parameter: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise ValueError(f"Parameter '{parameter}' must contain 1 to 100 characters.")
    return value.strip()


def load_field_offices(csv_path: Optional[Path] = None) -> list[dict]:
    path = _csv_path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Field office CSV not found: {path}")

    offices = []
    office_codes = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Field office CSV is missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Invalid field count at CSV line {reader.line_num}.")
            row = {key: value.replace("\x00", "").strip() for key, value in row.items()}
            code = row["OFFICE CODE"].upper()
            if not code:
                raise ValueError(f"Missing office code at CSV line {reader.line_num}.")
            if code in office_codes:
                raise ValueError(f"Duplicate office code '{code}' at CSV line {reader.line_num}.")
            office_codes.add(code)
            offices.append({
                "office_code": code,
                "office_name": row["OFFICE NAME"],
                "address": {
                    "lines": [row[f"ADDRESS LINE {i}"] for i in (1, 2, 3) if row[f"ADDRESS LINE {i}"]],
                    "city": row["CITY"],
                    "state": row["STATE"].upper(),
                    "zip_code": row["ZIP CODE"],
                },
                "phone": row["PHONE"] or None,
                "fax": row["FAX"] or None,
                "hours": {
                    day: {
                        "open": row[f"{day.upper()} OPEN TIME"] or None,
                        "close": row[f"{day.upper()} CLOSE TIME"] or None,
                    }
                    for day in WEEKDAYS
                },
            })
    return sorted(offices, key=lambda office: (
        office["address"]["state"], office["address"]["city"],
        office["office_name"], office["office_code"],
    ))


def search_field_offices(
    state: Optional[str] = None,
    city: Optional[str] = None,
    zip_code: Optional[str] = None,
    name: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    csv_path: Optional[Path] = None,
) -> dict:
    state = _search_text(state, "state")
    city = _search_text(city, "city")
    zip_code = _search_text(zip_code, "zip_code")
    name = _search_text(name, "name")
    if state is not None:
        state = state.upper()
        if not re.fullmatch(r"[A-Z]{2}", state):
            raise ValueError("Parameter 'state' must be a two-letter state or territory abbreviation.")
    if zip_code is not None and not re.fullmatch(r"[0-9]{5}", zip_code):
        raise ValueError("Parameter 'zip_code' must be a five-digit string, including leading zeros.")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Parameter 'limit' must be an integer between 1 and 50.")
    if type(offset) is not int or offset < 0:
        raise ValueError("Parameter 'offset' must be a nonnegative integer.")

    matches = []
    for office in load_field_offices(csv_path):
        address = office["address"]
        if state is not None and address["state"] != state:
            continue
        if city is not None and address["city"].casefold() != city.casefold():
            continue
        if zip_code is not None and address["zip_code"][:5] != zip_code:
            continue
        if name is not None and name.casefold() not in office["office_name"].casefold():
            continue
        matches.append(office)

    page = matches[offset:offset + limit]
    has_more = offset + len(page) < len(matches)
    return {
        "filters": {"state": state, "city": city, "zip_code": zip_code, "name": name},
        "total_matches": len(matches),
        "returned_count": len(page),
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + len(page) if has_more else None,
        "offices": [
            {key: office[key] for key in ("office_code", "office_name", "address", "phone")}
            for office in page
        ],
        "source_file": _csv_path(csv_path).name,
        "notice": DATA_NOTICE,
    }


def get_field_office_details(office_code: str, csv_path: Optional[Path] = None) -> dict:
    code = _search_text(office_code, "office_code")
    if code is None or len(code) > 10:
        raise ValueError("Parameter 'office_code' must contain 1 to 10 characters.")
    code = code.upper()
    office = next((item for item in load_field_offices(csv_path) if item["office_code"] == code), None)
    return {
        "office_code": code,
        "found": office is not None,
        "office": office,
        "source_file": _csv_path(csv_path).name,
        "notice": DATA_NOTICE,
    }


def get_field_office_metadata(csv_path: Optional[Path] = None) -> dict:
    offices = load_field_offices(csv_path)
    counts = Counter(office["address"]["state"] for office in offices)
    return {
        "dataset_name": "SSA Field Office Addresses and Opening/Closing Times",
        "source_file": _csv_path(csv_path).name,
        "source_last_updated": None,
        "total_offices": len(offices),
        "offices_by_state": dict(sorted(counts.items())),
        "hours_days": list(WEEKDAYS),
        "fields": ["office_code", "office_name", "address", "phone", "fax", "hours"],
        "limitations": [
            "The supplied CSV has no publication date or source URL; freshness is unknown.",
            "Published weekday hours are not live status; holiday and temporary closures are not included.",
            "Weekend hours, time zones, and appointment availability are not supplied.",
            "Missing phone, fax, or hour values are null, not an assertion that an office is closed.",
            "Hour strings are reproduced as supplied, including CLOSED and possible data errors.",
            "Office ZIP codes describe addresses, not service-area assignments or proximity.",
        ],
        "normalization": "Whitespace and NUL padding are removed; office codes and ZIP codes remain strings.",
    }
