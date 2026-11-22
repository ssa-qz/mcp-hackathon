"""Sandbox "Service Write" integration: SSA field-office appointment intake.

Business rules enforced before anything is written:
* the office exists in the directory and publishes valid hours for the requested weekday;
* the date is a weekday between tomorrow and MAX_DAYS_AHEAD days out;
* the slot starts on a SLOT_MINUTES boundary and the whole slot fits inside published hours;
* the office/date/slot is not already held by another confirmed appointment.

Privacy: confirmation codes are unguessable (``secrets``), and every lookup or cancellation
also requires the last four digits of the phone number used to book. A wrong code and a
wrong phone produce the same "no match" response, so codes cannot be enumerated.
"""

import datetime
import hmac
import re
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from ssa_server.core import DATA_DIR
from ssa_server.core.field_offices import WEEKDAYS, get_field_office_details

DEFAULT_APPOINTMENTS_DB = (
    DATA_DIR / "sandbox_appointments.db"
)

MAX_DAYS_AHEAD = 90
SLOT_MINUTES = 30
MAX_NOTES_LENGTH = 500
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L look-alikes
CODE_LENGTH = 8  # 31**8 ~ 8.5e11 possible codes per year

ServiceType = Literal[
    "CARD_REPLACEMENT",
    "RETIREMENT_BENEFITS",
    "DISABILITY_APPLICATION",
    "MEDICARE_ENROLLMENT",
    "GENERAL_INQUIRY",
]

SERVICE_CATALOG: Dict[str, Dict[str, Any]] = {
    "CARD_REPLACEMENT": {
        "title": "Replacement Social Security Card",
        "description": "Application or interview for a replacement Social Security number card.",
        "documents_required": [
            "Original or certified proof of U.S. citizenship (e.g. U.S. birth certificate or U.S. passport).",
            "Unexpired government-issued photo identity document (e.g. driver's license or state ID card).",
            "Evidence of legal name change if applicable (e.g. marriage certificate or court order).",
        ],
    },
    "RETIREMENT_BENEFITS": {
        "title": "Social Security Retirement Benefits",
        "description": "Consultation or intake for filing for retirement or spousal benefits.",
        "documents_required": [
            "Official proof of age / birth date (original birth certificate or passport).",
            "W-2 forms or self-employment tax returns for the most recent tax year.",
            "Direct deposit routing number and account number for electronic benefit payments.",
        ],
    },
    "DISABILITY_APPLICATION": {
        "title": "Social Security Disability Insurance (SSDI / SSI)",
        "description": "Intake interview for disability benefit application or medical documentation review.",
        "documents_required": [
            "Medical records, treatment history, and names/addresses of treating doctors and hospitals.",
            "Names and dosages of current prescribed medications.",
            "Detailed summary of employment history and job duties for the past 5 years.",
        ],
    },
    "MEDICARE_ENROLLMENT": {
        "title": "Medicare Part A / Part B Enrollment",
        "description": "Application to enroll in Medicare health insurance or submit employer coverage forms.",
        "documents_required": [
            "Current health insurance documentation (if applying under Special Enrollment Period).",
            "Completed CMS-L564 (Employer Information) form if applicable.",
            "Government photo identification.",
        ],
    },
    "GENERAL_INQUIRY": {
        "title": "General In-Person Consultation",
        "description": "In-person assistance with earnings statements, benefit verification, or general questions.",
        "documents_required": [
            "Unexpired government-issued photo identification.",
            "Relevant Social Security correspondence, letters, or benefit statements.",
        ],
    },
}

SANDBOX_NOTICE = (
    "DEMONSTRATION SANDBOX TRANSACTION: This appointment is recorded in a prototype sandbox database "
    "for hackathon demonstration purposes only. It is not transmitted to production SSA scheduling systems."
)
NO_MATCH_MESSAGE = (
    "No sandbox appointment matches that confirmation code and phone number. "
    "Check both values; the phone digits must be the last four of the number used to book."
)


# ---------------------------------------------------------------------------- storage
def get_appointment_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Connect to the sandbox appointments SQLite database and ensure schema exists."""
    target = Path(db_path) if db_path else DEFAULT_APPOINTMENTS_DB
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sandbox_appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                confirmation_code TEXT UNIQUE NOT NULL,
                office_code TEXT NOT NULL,
                office_name TEXT NOT NULL,
                service_type TEXT NOT NULL,
                appointment_date TEXT NOT NULL,
                time_slot TEXT NOT NULL,
                applicant_name TEXT NOT NULL,
                contact_phone TEXT NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'CONFIRMED_SANDBOX',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apt_code ON sandbox_appointments (confirmation_code);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apt_office ON sandbox_appointments (office_code, appointment_date);")
    return conn


def _generate_confirmation_code(year: int) -> str:
    """Unguessable, human-readable code, e.g. 'SSA-APT-2026-K7Q2M9XD'."""
    return f"SSA-APT-{year}-{''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))}"


# ------------------------------------------------------------------------- validation
def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _normalize_phone(phone: str) -> str:
    digits = _phone_digits(phone)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    raise ValueError("Parameter 'contact_phone' must be a 10-digit US number, or 11 digits starting with 1.")


def _mask_phone(phone: str) -> str:
    return f"***-***-{_phone_digits(phone)[-4:]}"


def _parse_clock(value: Optional[str]) -> Optional[datetime.time]:
    """Parse '9:00 AM' / '09:00AM' style strings; None for blanks, CLOSED, or garbage."""
    if not value:
        return None
    match = re.fullmatch(r"(0?[1-9]|1[0-2]):([0-5][0-9])\s*([AP]M)", value.strip().upper())
    if not match:
        return None
    hour, minute, meridiem = int(match[1]) % 12, int(match[2]), match[3]
    return datetime.time(hour + (12 if meridiem == "PM" else 0), minute)


def _validate_date(appointment_date: str, today: datetime.date) -> datetime.date:
    try:
        parsed = datetime.date.fromisoformat(appointment_date.strip())
    except ValueError as exc:
        raise ValueError("Parameter 'appointment_date' must be a real date in ISO format 'YYYY-MM-DD'.") from exc
    if parsed.weekday() >= 5:
        raise ValueError(
            f"Requested date {parsed} is a weekend ({parsed.strftime('%A')}). "
            "SSA field offices only schedule appointments Monday through Friday."
        )
    latest = today + datetime.timedelta(days=MAX_DAYS_AHEAD)
    if not today < parsed <= latest:
        raise ValueError(
            f"Appointments can be booked from {today + datetime.timedelta(days=1)} through {latest} "
            f"(tomorrow up to {MAX_DAYS_AHEAD} days ahead); {parsed} is outside that window."
        )
    return parsed


def _validate_slot(time_slot: str, office: dict, day_name: str) -> tuple[str, dict]:
    slot = _parse_clock(time_slot)
    if slot is None:
        raise ValueError("Parameter 'time_slot' must be formatted like '10:00 AM' or '02:30 PM'.")
    if slot.minute % SLOT_MINUTES:
        raise ValueError(f"Appointments start on {SLOT_MINUTES}-minute boundaries (e.g. 10:00 AM or 10:30 AM).")

    hours = office.get("hours", {}).get(day_name, {})
    opens, closes = _parse_clock(hours.get("open")), _parse_clock(hours.get("close"))
    if opens is None or closes is None:
        raise ValueError(
            f"Office {office['office_name']} has no published hours on {day_name.capitalize()}s "
            f"(listed as open={hours.get('open')!r}, close={hours.get('close')!r}). Please choose another weekday."
        )
    if closes <= opens:
        raise ValueError(
            f"Published {day_name.capitalize()} hours for {office['office_name']} look invalid "
            f"({hours['open']} to {hours['close']}). Confirm hours with the office or choose another weekday."
        )
    slot_end = (datetime.datetime.combine(datetime.date.min, slot) + datetime.timedelta(minutes=SLOT_MINUTES)).time()
    if slot < opens or slot_end > closes:
        raise ValueError(
            f"{time_slot.strip()} is outside {office['office_name']}'s published {day_name.capitalize()} hours "
            f"({hours['open']} to {hours['close']}); the last bookable slot starts {SLOT_MINUTES} minutes before closing."
        )
    return slot.strftime("%I:%M %p"), hours


# ---------------------------------------------------------------------------- actions
def book_appointment(
    office_code: str,
    service_type: ServiceType,
    appointment_date: str,
    time_slot: str,
    applicant_name: str,
    contact_phone: str,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
    csv_path: Optional[Path] = None,
    today: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Book a new field office appointment intake in the sandbox database."""
    clean_code = office_code.strip().upper()
    if not clean_code:
        raise ValueError("Parameter 'office_code' must not be empty.")

    clean_service = service_type.strip().upper()
    if clean_service not in SERVICE_CATALOG:
        raise ValueError(f"Invalid service_type '{service_type}'. Must be one of: {', '.join(SERVICE_CATALOG)}.")

    clean_name = applicant_name.strip()
    if not 2 <= len(clean_name) <= 100:
        raise ValueError("Parameter 'applicant_name' must contain 2 to 100 characters.")

    clean_phone = _normalize_phone(contact_phone)

    clean_notes = notes.strip() if notes and notes.strip() else None
    if clean_notes and len(clean_notes) > MAX_NOTES_LENGTH:
        raise ValueError(f"Parameter 'notes' must be at most {MAX_NOTES_LENGTH} characters.")

    parsed_date = _validate_date(appointment_date, today or datetime.date.today())

    office_resp = get_field_office_details(clean_code, csv_path=csv_path)
    if not office_resp.get("found"):
        raise ValueError(f"Field office with code '{clean_code}' not found in official directory.")
    office = office_resp["office"]
    day_name = WEEKDAYS[parsed_date.weekday()]
    clean_slot, day_hours = _validate_slot(time_slot, office, day_name)

    with closing(get_appointment_db(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")  # check-then-insert must be atomic across processes
        taken = conn.execute(
            "SELECT 1 FROM sandbox_appointments WHERE office_code = ? AND appointment_date = ? "
            "AND time_slot = ? AND status = 'CONFIRMED_SANDBOX'",
            (clean_code, str(parsed_date), clean_slot),
        ).fetchone()
        if taken:
            raise ValueError(
                f"The {clean_slot} slot on {parsed_date} at office {clean_code} is already booked. "
                f"Please choose another time (slots are every {SLOT_MINUTES} minutes)."
            )
        for _ in range(5):
            confirmation_code = _generate_confirmation_code(parsed_date.year)
            try:
                conn.execute(
                    """
                    INSERT INTO sandbox_appointments (
                        confirmation_code, office_code, office_name, service_type,
                        appointment_date, time_slot, applicant_name, contact_phone, notes, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED_SANDBOX')
                    """,
                    (confirmation_code, clean_code, office["office_name"], clean_service, str(parsed_date),
                     clean_slot, clean_name, clean_phone, clean_notes),
                )
                break
            except sqlite3.IntegrityError:
                continue  # astronomically unlikely code collision: draw again
        else:
            raise RuntimeError("Could not allocate a unique confirmation code.")

    service_info = SERVICE_CATALOG[clean_service]
    return {
        "status": "CONFIRMED_SANDBOX",
        "confirmation_code": confirmation_code,
        "applicant_name": clean_name,
        "contact_phone": clean_phone,
        "service_type": clean_service,
        "service_title": service_info["title"],
        "appointment_date": str(parsed_date),
        "day_of_week": parsed_date.strftime("%A"),
        "time_slot": clean_slot,
        "office": {
            "office_code": clean_code,
            "office_name": office["office_name"],
            "address": office["address"],
            "phone": office["phone"],
            "hours_on_day": day_hours,
        },
        "required_documents_checklist": service_info["documents_required"],
        "notes": clean_notes,
        "how_to_manage": (
            "To look up or cancel, provide the confirmation code and the last 4 digits of the contact phone."
        ),
        "notice": SANDBOX_NOTICE,
    }


def _find_verified(conn: sqlite3.Connection, confirmation_code: str, phone_last4: str) -> Optional[sqlite3.Row]:
    """Return the row only when both the code and the phone's last four digits match."""
    row = conn.execute(
        "SELECT * FROM sandbox_appointments WHERE confirmation_code = ?", (confirmation_code,)
    ).fetchone()
    if row is None:
        return None
    return row if hmac.compare_digest(_phone_digits(row["contact_phone"])[-4:], phone_last4) else None


def _clean_lookup_inputs(confirmation_code: str, phone_last4: str) -> tuple[str, str]:
    clean_code = confirmation_code.strip().upper()
    if not clean_code:
        raise ValueError("Parameter 'confirmation_code' must not be empty.")
    clean_last4 = phone_last4.strip()
    if not re.fullmatch(r"[0-9]{4}", clean_last4):
        raise ValueError("Parameter 'phone_last4' must be exactly four digits.")
    return clean_code, clean_last4


def lookup_appointment(
    confirmation_code: str,
    phone_last4: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retrieve a sandbox appointment after verifying the code and phone's last four digits."""
    clean_code, clean_last4 = _clean_lookup_inputs(confirmation_code, phone_last4)
    with closing(get_appointment_db(db_path)) as conn:
        row = _find_verified(conn, clean_code, clean_last4)

    if not row:
        return {"confirmation_code": clean_code, "found": False, "message": NO_MATCH_MESSAGE,
                "notice": SANDBOX_NOTICE}

    service_info = SERVICE_CATALOG.get(row["service_type"], {})
    return {
        "confirmation_code": row["confirmation_code"],
        "found": True,
        "status": row["status"],
        "applicant_name": row["applicant_name"],
        "contact_phone": _mask_phone(row["contact_phone"]),
        "service_type": row["service_type"],
        "service_title": service_info.get("title", row["service_type"]),
        "appointment_date": row["appointment_date"],
        "time_slot": row["time_slot"],
        "office_code": row["office_code"],
        "office_name": row["office_name"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "required_documents_checklist": service_info.get("documents_required", []),
        "notice": SANDBOX_NOTICE,
    }


def cancel_appointment(
    confirmation_code: str,
    phone_last4: str,
    reason: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Cancel a sandbox appointment after verifying the code and phone's last four digits."""
    clean_code, clean_last4 = _clean_lookup_inputs(confirmation_code, phone_last4)
    with closing(get_appointment_db(db_path)) as conn, conn:
        row = _find_verified(conn, clean_code, clean_last4)
        if not row:
            return {"confirmation_code": clean_code, "found": False, "cancelled": False,
                    "message": NO_MATCH_MESSAGE, "notice": SANDBOX_NOTICE}
        if row["status"] == "CANCELLED_SANDBOX":
            return {"confirmation_code": clean_code, "found": True, "cancelled": False,
                    "status": "CANCELLED_SANDBOX", "message": f"Appointment '{clean_code}' is already cancelled.",
                    "notice": SANDBOX_NOTICE}
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            "UPDATE sandbox_appointments SET status = 'CANCELLED_SANDBOX', updated_at = ? WHERE confirmation_code = ?",
            (now_iso, clean_code),
        )

    return {
        "confirmation_code": clean_code,
        "found": True,
        "cancelled": True,
        "status": "CANCELLED_SANDBOX",
        "reason": reason.strip() if reason and reason.strip() else "Requested by applicant",
        "cancelled_at": now_iso,
        "message": f"Sandbox appointment '{clean_code}' has been successfully cancelled.",
        "notice": SANDBOX_NOTICE,
    }


def get_appointments_metadata(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Catalog metadata describing the sandbox appointment intake service."""
    with closing(get_appointment_db(db_path)) as conn:
        counts = conn.execute(
            "SELECT COUNT(*) as total, COUNT(CASE WHEN status = 'CONFIRMED_SANDBOX' THEN 1 END) as confirmed "
            "FROM sandbox_appointments"
        ).fetchone()

    return {
        "service_name": "SSA Field Office Appointment Intake (Service Write Sandbox)",
        "purpose": (
            "Enables AI applications to schedule, verify, and cancel in-person appointment intakes "
            "at verified SSA Field Offices within an isolated SQLite sandbox environment."
        ),
        "supported_service_types": [
            {"code": k, "title": v["title"], "description": v["description"]}
            for k, v in SERVICE_CATALOG.items()
        ],
        "statistics": {
            "total_intakes_recorded": counts["total"] if counts else 0,
            "active_confirmed_appointments": counts["confirmed"] if counts else 0,
        },
        "operating_constraints": [
            f"Weekdays only, from tomorrow up to {MAX_DAYS_AHEAD} days ahead.",
            f"Slots start every {SLOT_MINUTES} minutes and must fit entirely within the office's published hours.",
            "Each office/date/slot can hold one confirmed appointment.",
            "Lookups and cancellations require the confirmation code plus the last 4 digits of the booking phone.",
            "The user must explicitly confirm details before an agent books or cancels.",
            "All writes are stored in a persistent local sandbox database with an audit trail.",
        ],
        "notice": SANDBOX_NOTICE,
    }
