from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.prompt
    def schedule_appointment_intake(office_code: str, service_type: str) -> str:
        """Guide an agent through booking an in-person intake appointment with document preparation."""
        return f"""Assist with scheduling an in-person appointment intake at office '{office_code}' for service '{service_type}':
1. Verify the office details and weekday hours using get_field_office_details for '{office_code}'.
2. Confirm the citizen's preferred date (Monday-Friday) and time slot within operating hours.
3. Verify the applicant's name and contact phone number.
4. Explicitly summarize the appointment details and ask the user for confirmation before invoking book_field_office_appointment.
5. Once confirmed, present the confirmation code and emphasize the required documents checklist they must bring to the appointment.
6. Clarify that this is recorded in a demonstration sandbox database."""
