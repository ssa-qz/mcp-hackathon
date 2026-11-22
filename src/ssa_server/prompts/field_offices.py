from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.prompt
    def plan_field_office_visit(city: str, state: str) -> str:
        """Guide an agent through finding an SSA office and summarizing its published visiting information."""
        return f"""Help me plan a visit to an SSA field office in {city}, {state}:
1. Read ssa://field-offices/metadata to understand the dataset and its limitations.
2. Call find_field_offices with city='{city}' and state='{state}'. Follow pagination when needed.
3. If multiple offices match, present the options and ask me which office I mean. Do not guess the nearest or assigned office.
4. Use get_field_office_details for the selected office code.
5. Summarize the address, phone, fax if available, and the published weekday hours. Cite the source filename and office code. Flag unusual hour values for confirmation rather than silently correcting them or inferring overnight service.
6. Explain that the snapshot date is unknown and hours should be confirmed before visiting. Do not invent weekend hours, holiday status, time zones, or appointment requirements, and do not claim to book an appointment without explicit instruction.
7. If there is no match, say so and ask whether to broaden the search to the state or an office-name fragment."""
