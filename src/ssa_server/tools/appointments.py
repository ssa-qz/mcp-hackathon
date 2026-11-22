from typing import Optional
from fastmcp import FastMCP
from ssa_server.core.sandbox_appointments import (
    book_appointment as query_book_appointment,
    cancel_appointment as query_cancel_appointment,
    lookup_appointment as query_lookup_appointment,
)
from ssa_server.config import settings
from ssa_server.models import (
    ApplicantName,
    ConfirmationCode,
    DateString,
    NotesText,
    OfficeCode,
    PhoneLast4,
    PhoneString,
    ReasonText,
    ServiceType,
    TimeSlotString,
)
from ssa_server.utils import agent_errors


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="book_field_office_appointment",
        annotations={
            "title": "Book Sandbox Appointment Intake",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def book_field_office_appointment(
        office_code: OfficeCode,
        service_type: ServiceType,
        appointment_date: DateString,
        time_slot: TimeSlotString,
        applicant_name: ApplicantName,
        contact_phone: PhoneString,
        notes: Optional[NotesText] = None,
    ) -> dict:
        """Book an in-person SSA field-office appointment (sandbox) and return the documents to bring.

        WRITE ACTION - only call after you have shown the user the office, date, time, service,
        applicant name, and phone, and the user has explicitly confirmed in a later message. A
        request to book is not a confirmation; summarize and ask first.

        Rules: weekday from tomorrow up to 90 days ahead; slots start on :00 or :30 and must fit
        inside the office's published hours for that day; one booking per office/date/slot. Errors
        explain which rule failed so you can offer the user an alternative.

        Args:
            office_code: Office code from find_field_offices (e.g. 'A00').
            service_type: 'CARD_REPLACEMENT', 'RETIREMENT_BENEFITS', 'DISABILITY_APPLICATION', 'MEDICARE_ENROLLMENT', or 'GENERAL_INQUIRY'.
            appointment_date: YYYY-MM-DD, Monday-Friday, tomorrow up to 90 days ahead.
            time_slot: Start time like '10:00 AM' or '02:30 PM', on a 30-minute boundary.
            applicant_name: Full name of the person attending (2-100 characters).
            contact_phone: US phone with area code: 10 digits, or 11 digits starting with 1. Stored as '(XXX) XXX-XXXX' or '+1 (XXX) XXX-XXXX'; its last 4 digits are needed later to look up or cancel.
            notes: Optional accommodation or service notes (max 500 characters).

        Returns:
            Confirmation code (e.g. 'SSA-APT-2026-K7Q2M9XD'), date, time, office address and phone,
            and the checklist of documents to bring. Always show the user the code and checklist.
        """
        return query_book_appointment(
            office_code=office_code,
            service_type=service_type,
            appointment_date=appointment_date,
            time_slot=time_slot,
            applicant_name=applicant_name,
            contact_phone=contact_phone,
            notes=notes,
            db_path=settings.APPOINTMENTS_DB_PATH,
            csv_path=settings.FIELD_OFFICES_CSV_PATH,
        )

    @mcp.tool(
        name="lookup_field_office_appointment",
        annotations={
            "title": "Lookup Sandbox Appointment by Code",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def lookup_field_office_appointment(
        confirmation_code: ConfirmationCode,
        phone_last4: PhoneLast4,
    ) -> dict:
        """Check the status, time, and required documents of an existing sandbox appointment.

        Both values must come from the user; never guess them. A wrong code and a wrong phone
        return the same found=false result.

        Args:
            confirmation_code: Code received when booking (e.g. 'SSA-APT-2026-K7Q2M9XD'). Case-insensitive.
            phone_last4: Last four digits of the phone number used to book (e.g. '5678').

        Returns:
            found, status (CONFIRMED_SANDBOX or CANCELLED_SANDBOX), date, time, office, service,
            masked phone, and the document checklist.
        """
        return query_lookup_appointment(
            confirmation_code=confirmation_code,
            phone_last4=phone_last4,
            db_path=settings.APPOINTMENTS_DB_PATH,
        )

    @mcp.tool(
        name="cancel_field_office_appointment",
        annotations={
            "title": "Cancel Sandbox Appointment",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def cancel_field_office_appointment(
        confirmation_code: ConfirmationCode,
        phone_last4: PhoneLast4,
        reason: Optional[ReasonText] = None,
    ) -> dict:
        """Cancel an existing sandbox appointment.

        WRITE ACTION - only call after the user has explicitly confirmed they want this specific
        appointment cancelled. Cancelling an already-cancelled appointment is a harmless no-op.

        Args:
            confirmation_code: Code received when booking (e.g. 'SSA-APT-2026-K7Q2M9XD').
            phone_last4: Last four digits of the phone number used to book.
            reason: Optional cancellation reason (max 200 characters).

        Returns:
            cancelled (true only if this call cancelled it), status, and cancellation timestamp.
        """
        return query_cancel_appointment(
            confirmation_code=confirmation_code,
            phone_last4=phone_last4,
            reason=reason,
            db_path=settings.APPOINTMENTS_DB_PATH,
        )
