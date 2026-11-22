import csv
import datetime

import pytest

from ssa_server.core.field_offices import (
    WEEKDAYS,
    get_field_office_details,
    get_field_office_metadata,
    load_field_offices,
    search_field_offices,
)
from ssa_server.core.sandbox_appointments import (
    book_appointment,
    cancel_appointment,
    get_appointments_metadata,
    lookup_appointment,
)


@pytest.fixture
def office_csv(tmp_path):
    columns = [
        "OFFICE CODE", "OFFICE NAME", "ADDRESS LINE 1", "ADDRESS LINE 2",
        "ADDRESS LINE 3", "CITY", "STATE", "ZIP CODE", "PHONE", "FAX",
        *[f"{day.upper()} {kind} TIME" for day in WEEKDAYS for kind in ("OPEN", "CLOSE")],
    ]
    rows = []
    for code, name, city, state, zip_code in [
        ("001", "TEST HARTFORD", "HARTFORD", "CT", "06103"),
        ("A23", "TEST EAST HARTFORD", "EAST HARTFORD", "CT", "06108"),
        ("A17", "TEST CRESCENT CITY", "CRESCENT CITY", "CA", "95531"),
    ]:
        row = {
            "OFFICE CODE": code,
            "OFFICE NAME": f"{name}   ",
            "ADDRESS LINE 1": "SOCIAL SECURITY   ",
            "ADDRESS LINE 2": "\x00\x00   ",
            "ADDRESS LINE 3": "100 EXAMPLE STREET   ",
            "CITY": f"{city}   ",
            "STATE": state,
            "ZIP CODE": zip_code,
            "PHONE": "(800) 555-0100",
            "FAX": "",
        }
        for day in WEEKDAYS:
            row[f"{day.upper()} OPEN TIME"] = "9:00 AM"
            row[f"{day.upper()} CLOSE TIME"] = "4:00 PM"
        rows.append(row)
    rows[0]["WEDNESDAY OPEN TIME"] = ""
    rows[0]["WEDNESDAY CLOSE TIME"] = ""
    rows[0]["FRIDAY OPEN TIME"] = "CLOSED"
    rows[0]["FRIDAY CLOSE TIME"] = "CLOSED"
    rows[2]["MONDAY CLOSE TIME"] = "4:00 AM"
    path = tmp_path / "offices.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def sandbox_db(tmp_path):
    return tmp_path / "test_appointments.db"


def test_load_offices_cleans_padding_and_preserves_identifiers(office_csv):
    offices = load_field_offices(office_csv)
    office = next(item for item in offices if item["office_code"] == "001")
    assert len(offices) == 3
    assert office["office_name"] == "TEST HARTFORD"
    assert office["address"]["zip_code"] == "06103"
    assert office["address"]["lines"] == ["SOCIAL SECURITY", "100 EXAMPLE STREET"]
    assert office["fax"] is None
    assert office["hours"]["wednesday"] == {"open": None, "close": None}
    assert "saturday" not in office["hours"]
    assert office["hours"]["friday"] == {"open": "CLOSED", "close": "CLOSED"}
    unusual = next(item for item in offices if item["office_code"] == "A17")
    assert unusual["hours"]["monday"]["close"] == "4:00 AM"


def test_search_combines_filters_and_normalizes_case(office_csv):
    result = search_field_offices(
        state=" ct ", city=" east hartford ", name="test", csv_path=office_csv,
    )
    assert result["total_matches"] == 1
    assert result["offices"][0]["office_code"] == "A23"
    assert result["offices"][0]["phone"] == "(800) 555-0100"
    assert result["has_more"] is False
    assert result["next_offset"] is None


def test_search_zip_is_office_location_and_preserves_zero(office_csv):
    result = search_field_offices(zip_code="06103", csv_path=office_csv)
    assert result["total_matches"] == 1
    assert result["offices"][0]["office_code"] == "001"
    assert "service-area" in result["notice"]


def test_search_city_is_exact_not_substring(office_csv):
    result = search_field_offices(city="hartford", csv_path=office_csv)
    assert result["total_matches"] == 1
    assert result["offices"][0]["office_code"] == "001"


def test_search_pagination_and_no_matches(office_csv):
    first = search_field_offices(limit=1, csv_path=office_csv)
    second = search_field_offices(limit=1, offset=first["next_offset"], csv_path=office_csv)
    assert first["total_matches"] == 3
    assert first["has_more"] is True
    assert first["returned_count"] == 1
    assert first["offices"][0]["office_code"] != second["offices"][0]["office_code"]
    for result in [
        search_field_offices(city="Missing City", csv_path=office_csv),
        search_field_offices(offset=10, csv_path=office_csv),
    ]:
        assert result["offices"] == []
        assert result["returned_count"] == 0
        assert result["has_more"] is False
        assert result["next_offset"] is None


@pytest.mark.parametrize("arguments", [
    {"limit": 0}, {"limit": 51}, {"limit": True}, {"offset": -1},
    {"state": "Connecticut"}, {"state": "1!"}, {"city": " "},
    {"name": ""}, {"zip_code": "6103"}, {"zip_code": "abcde"},
])
def test_search_rejects_invalid_inputs(office_csv, arguments):
    with pytest.raises(ValueError):
        search_field_offices(csv_path=office_csv, **arguments)


def test_details_include_contact_and_weekday_hours(office_csv):
    result = get_field_office_details(" a23 ", csv_path=office_csv)
    assert result["found"] is True
    assert result["office_code"] == "A23"
    office = result["office"]
    assert office["address"]["city"] == "EAST HARTFORD"
    assert office["phone"] == "(800) 555-0100"
    assert office["hours"]["monday"] == {"open": "9:00 AM", "close": "4:00 PM"}
    assert result["source_file"] == "offices.csv"


def test_details_not_found_and_blank_code(office_csv):
    result = get_field_office_details("XXX", csv_path=office_csv)
    assert result["found"] is False
    assert result["office"] is None
    with pytest.raises(ValueError):
        get_field_office_details(" ", csv_path=office_csv)


def test_metadata_describes_coverage_and_limitations(office_csv):
    result = get_field_office_metadata(office_csv)
    assert result["total_offices"] == 3
    assert result["offices_by_state"] == {"CA": 1, "CT": 2}
    assert result["hours_days"] == list(WEEKDAYS)
    assert result["source_last_updated"] is None
    assert "holiday" in " ".join(result["limitations"]).lower()


def test_missing_csv_reports_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="Field office CSV"):
        load_field_offices(tmp_path / "missing.csv")


def test_invalid_csv_headers_report_error(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text("OFFICE CODE,OFFICE NAME\n001,TEST\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        load_field_offices(path)


def test_duplicate_office_codes_report_error(office_csv):
    with office_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    with office_csv.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(rows[1])
    with pytest.raises(ValueError, match="Duplicate office code"):
        load_field_offices(office_csv)


def test_actual_csv_loads_and_known_office_matches():
    result = get_field_office_details("A00")
    assert result["found"] is True
    assert result["office"]["address"]["city"] == "HENDERSON"
    assert result["office"]["address"]["zip_code"] == "89052"
    assert result["office"]["phone"] == "(855) 207-7084"


# ============================================================================
# Sandbox Appointment Service Write Integration Tests
# ============================================================================
# "Today" is pinned so booking-window rules are deterministic; 2026-10-01 is a Thursday.
TODAY = datetime.date(2026, 10, 1)


def book(office_csv, sandbox_db, **overrides):
    arguments = {
        "office_code": "A23",
        "service_type": "CARD_REPLACEMENT",
        "appointment_date": "2026-10-13",  # Tuesday
        "time_slot": "10:00 AM",
        "applicant_name": "Jane Doe",
        "contact_phone": "(555) 234-5678",
        "db_path": sandbox_db,
        "csv_path": office_csv,
        "today": TODAY,
        **overrides,
    }
    return book_appointment(**arguments)


def test_book_appointment_success(office_csv, sandbox_db):
    result = book(office_csv, sandbox_db, notes="Need replacement card due to marriage name change.")
    assert result["status"] == "CONFIRMED_SANDBOX"
    assert result["applicant_name"] == "Jane Doe"
    assert result["service_title"] == "Replacement Social Security Card"
    assert len(result["required_documents_checklist"]) >= 3
    assert result["office"]["office_name"] == "TEST EAST HARTFORD"
    assert "SANDBOX" in result["notice"]


def test_confirmation_codes_are_unguessable(office_csv, sandbox_db):
    codes = {book(office_csv, sandbox_db, time_slot=f"{h:02d}:00 {'AM' if h < 12 else 'PM'}")["confirmation_code"]
             for h in (9, 10, 11)}
    assert len(codes) == 3
    for code in codes:
        prefix, suffix = code.rsplit("-", 1)
        assert prefix == "SSA-APT-2026"
        assert len(suffix) == 8 and not set(suffix) & set("01ILO")


@pytest.mark.parametrize("date, message", [
    ("2026-10-17", "weekend"),            # Saturday
    ("2026-10-01", "outside that window"),  # today
    ("2026-09-29", "outside that window"),  # past
    ("2027-01-05", "outside that window"),  # > 90 days ahead
    ("2026-02-30", "real date"),
])
def test_book_appointment_enforces_date_window(office_csv, sandbox_db, date, message):
    with pytest.raises(ValueError, match=message):
        book(office_csv, sandbox_db, appointment_date=date)


@pytest.mark.parametrize("slot, message", [
    ("08:30 AM", "outside"),    # before 9:00 AM opening
    ("04:00 PM", "outside"),    # starts at closing time
    ("10:15 AM", "30-minute"),  # off-boundary
    ("morning", "time_slot"),
])
def test_book_appointment_enforces_office_hours(office_csv, sandbox_db, slot, message):
    with pytest.raises(ValueError, match=message):
        book(office_csv, sandbox_db, time_slot=slot)


def test_first_and_last_slots_of_the_day_are_bookable(office_csv, sandbox_db):
    # Office hours 9:00 AM - 4:00 PM: 9:00 starts at opening, 3:30-4:00 ends exactly at closing.
    assert book(office_csv, sandbox_db, time_slot="9:00 am")["time_slot"] == "09:00 AM"
    assert book(office_csv, sandbox_db, time_slot="03:30 PM")["time_slot"] == "03:30 PM"


def test_book_appointment_rejects_closed_blank_and_invalid_hours(office_csv, sandbox_db):
    with pytest.raises(ValueError, match="no published hours on Fridays"):       # CLOSED
        book(office_csv, sandbox_db, office_code="001", appointment_date="2026-10-16")
    with pytest.raises(ValueError, match="no published hours on Wednesdays"):    # blank
        book(office_csv, sandbox_db, office_code="001", appointment_date="2026-10-14")
    with pytest.raises(ValueError, match="look invalid"):                         # 9 AM - 4 AM
        book(office_csv, sandbox_db, office_code="A17", appointment_date="2026-10-12")


def test_book_appointment_prevents_double_booking_until_cancelled(office_csv, sandbox_db):
    first = book(office_csv, sandbox_db)
    with pytest.raises(ValueError, match="already booked"):
        book(office_csv, sandbox_db, time_slot="10:00am", applicant_name="Someone Else")
    cancel_appointment(first["confirmation_code"], "5678", db_path=sandbox_db)
    assert book(office_csv, sandbox_db)["status"] == "CONFIRMED_SANDBOX"


def test_book_appointment_rejects_nonexistent_office(office_csv, sandbox_db):
    with pytest.raises(ValueError, match="not found in official directory"):
        book(office_csv, sandbox_db, office_code="XXX")


def test_book_appointment_validates_phone_and_notes(office_csv, sandbox_db):
    with pytest.raises(ValueError, match="contact_phone"):
        book(office_csv, sandbox_db, contact_phone="123")
    with pytest.raises(ValueError, match="contact_phone"):
        book(office_csv, sandbox_db, contact_phone="0112345678790")
    with pytest.raises(ValueError, match="notes"):
        book(office_csv, sandbox_db, notes="x" * 501)


@pytest.mark.parametrize(("entered", "stored"), [
    ("123 456 7890", "(123) 456-7890"),
    ("123.456.7890", "(123) 456-7890"),
    ("1234567890", "(123) 456-7890"),
    ("+1 (555) 555-5555", "+1 (555) 555-5555"),
    ("15555555555", "+1 (555) 555-5555"),
])
def test_book_appointment_normalizes_phone(office_csv, sandbox_db, entered, stored):
    result = book(office_csv, sandbox_db, contact_phone=entered)
    assert result["contact_phone"] == stored


def test_lookup_and_cancel_require_phone_verification(office_csv, sandbox_db):
    code = book(office_csv, sandbox_db, service_type="RETIREMENT_BENEFITS", contact_phone="(555) 987-6543",
                applicant_name="John Smith")["confirmation_code"]

    found = lookup_appointment(code.lower(), "6543", db_path=sandbox_db)
    assert found["found"] is True
    assert found["status"] == "CONFIRMED_SANDBOX"
    assert found["applicant_name"] == "John Smith"
    assert found["contact_phone"] == "***-***-6543"

    wrong_phone = lookup_appointment(code, "0000", db_path=sandbox_db)
    unknown_code = lookup_appointment("SSA-APT-2026-NOPE2345", "6543", db_path=sandbox_db)
    assert wrong_phone["found"] is unknown_code["found"] is False
    assert wrong_phone["message"] == unknown_code["message"]  # indistinguishable: no enumeration
    assert "John Smith" not in str(wrong_phone)

    assert cancel_appointment(code, "0000", db_path=sandbox_db)["cancelled"] is False
    assert lookup_appointment(code, "6543", db_path=sandbox_db)["status"] == "CONFIRMED_SANDBOX"

    cancelled = cancel_appointment(code, "6543", reason="Change of schedule", db_path=sandbox_db)
    assert cancelled["cancelled"] is True and cancelled["status"] == "CANCELLED_SANDBOX"
    assert lookup_appointment(code, "6543", db_path=sandbox_db)["status"] == "CANCELLED_SANDBOX"

    double_cancel = cancel_appointment(code, "6543", db_path=sandbox_db)
    assert double_cancel["cancelled"] is False
    assert "already cancelled" in double_cancel["message"]

    with pytest.raises(ValueError, match="phone_last4"):
        lookup_appointment(code, "65", db_path=sandbox_db)


def test_appointments_metadata(sandbox_db, office_csv):
    book(office_csv, sandbox_db, service_type="GENERAL_INQUIRY", time_slot="11:00 AM")
    meta = get_appointments_metadata(db_path=sandbox_db)
    assert meta["statistics"]["total_intakes_recorded"] == 1
    assert meta["statistics"]["active_confirmed_appointments"] == 1
    assert len(meta["supported_service_types"]) == 5


# ============================================================================
# FastMCP Discovery & Integration Tests (unified server)
# ============================================================================

def next_weekday(weekday: int) -> str:
    day = datetime.date.today() + datetime.timedelta(days=1)
    while day.weekday() != weekday:
        day += datetime.timedelta(days=1)
    return day.isoformat()


def test_mcp_tools_resources_and_prompts(office_csv, sandbox_db, monkeypatch):
    import asyncio
    from fastmcp import Client
    from ssa_server.app import mcp
    from ssa_server.config import settings

    monkeypatch.setattr(settings, "FIELD_OFFICES_CSV_PATH", office_csv)
    monkeypatch.setattr(settings, "APPOINTMENTS_DB_PATH", sandbox_db)

    async def check():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            office_tools = {
                "find_field_offices", "get_field_office_details", "book_field_office_appointment",
                "lookup_field_office_appointment", "cancel_field_office_appointment",
            }
            assert office_tools <= set(tools)

            read_ann = tools["find_field_offices"].annotations.model_dump(by_alias=True)
            assert read_ann["readOnlyHint"] is True and read_ann["openWorldHint"] is False
            write_ann = tools["book_field_office_appointment"].annotations.model_dump(by_alias=True)
            assert write_ann["readOnlyHint"] is False and write_ann["destructiveHint"] is False
            cancel_ann = tools["cancel_field_office_appointment"].annotations.model_dump(by_alias=True)
            assert cancel_ann["readOnlyHint"] is False and cancel_ann["destructiveHint"] is True
            for write_tool in ("book_field_office_appointment", "cancel_field_office_appointment"):
                assert "explicitly confirmed" in tools[write_tool].description

            search = await client.call_tool("find_field_offices", {"state": "ct", "limit": 1})
            assert search.structured_content["total_matches"] == 2
            assert search.structured_content["has_more"] is True

            details = await client.call_tool("get_field_office_details", {"office_code": "001"})
            assert details.structured_content["office"]["address"]["zip_code"] == "06103"

            booking = {
                "office_code": "A23", "service_type": "CARD_REPLACEMENT", "appointment_date": next_weekday(2),
                "time_slot": "10:30 AM", "applicant_name": "Test Citizen", "contact_phone": "(555) 444-5555",
            }
            booked = await client.call_tool("book_field_office_appointment", booking)
            code = booked.structured_content["confirmation_code"]

            # Business-rule failures reach the agent as clean, actionable errors.
            clash = await client.call_tool("book_field_office_appointment", booking, raise_on_error=False)
            assert clash.is_error and "already booked" in clash.content[0].text

            lookup = await client.call_tool("lookup_field_office_appointment",
                                            {"confirmation_code": code, "phone_last4": "5555"})
            assert lookup.structured_content["applicant_name"] == "Test Citizen"
            cancelled = await client.call_tool("cancel_field_office_appointment",
                                               {"confirmation_code": code, "phone_last4": "5555"})
            assert cancelled.structured_content["cancelled"] is True

            uris = {str(r.uri) for r in await client.list_resources()}
            assert {"ssa://field-offices/metadata", "ssa://field-offices/appointments/metadata"} <= uris
            templates = {t.uri_template for t in await client.list_resource_templates()}
            assert not any("appointments/{" in t for t in templates), "appointments must not bypass verification"

            meta = await client.read_resource("ssa://field-offices/appointments/metadata")
            assert "Appointment Intake" in meta[0].text

            prompt_names = {p.name for p in await client.list_prompts()}
            assert {"plan_field_office_visit", "schedule_appointment_intake"} <= prompt_names

    asyncio.run(check())


# ============================================================================
# Goal-level workflow: find offices for a resident's address
# ============================================================================

def fake_geocoder(zip_code="06108", city="EAST HARTFORD", state="CT", matched=True):
    def geocode(address, **_):
        if not matched:
            return {"matched": False, "message": "No matching address found by U.S. Census Geocoder."}
        return {"matched": True, "matched_address": f"1 MAIN ST, {city}, {state}, {zip_code}",
                "county": {"name": "Hartford County"}, "congressional_district": {"name": "District 1"},
                "address_components": {"zip": zip_code, "city": city, "state": state}}
    return geocode


@pytest.mark.parametrize("geo, basis, codes", [
    ({"zip_code": "06108"}, "same ZIP code", ["A23"]),
    ({"zip_code": "06199", "city": "HARTFORD"}, "same city", ["001"]),
    ({"zip_code": "06199", "city": "NEW BRITAIN"}, "same state (not ranked by distance)", ["001", "A23"]),
    ({"zip_code": "10001", "city": "NEW YORK", "state": "NY"}, "none", []),
])
def test_find_offices_near_address_explains_match_basis(office_csv, geo, basis, codes):
    from ssa_server.core.visit_planning import find_offices_near_address

    result = find_offices_near_address("1 Main St", csv_path=office_csv, geocode=fake_geocoder(**geo))
    assert result["address_matched"] is True
    assert result["match_basis"] == basis
    assert sorted(o["office_code"] for o in result["offices"]) == codes
    assert result["resident_jurisdiction"]["county"]["name"] == "Hartford County"
    assert "not distance" in result["caveat"]


def test_find_offices_near_address_unmatched_asks_for_better_address(office_csv):
    from ssa_server.core.visit_planning import find_offices_near_address

    result = find_offices_near_address("somewhere", csv_path=office_csv, geocode=fake_geocoder(matched=False))
    assert result["address_matched"] is False
    assert "complete street address" in result["next_step"]
