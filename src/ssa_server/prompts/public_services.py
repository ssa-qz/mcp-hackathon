from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.prompt
    def assess_office_jurisdiction_and_cola(address: str) -> str:
        """Agent prompt template to resolve an office's geographic boundaries and review the latest Social Security COLA indicators."""
        return f"""Please conduct a combined jurisdiction and economic briefing for '{address}':
1. Consult public-services://metadata to understand the capabilities and limitations of the services.
2. Call geocode_address with address='{address}' to identify its County, Congressional District, and coordinates.
3. Call estimate_social_security_cola to compute the COLA from live CPI-W, and find_offices_near_address with the same address to list the SSA offices serving that area.
4. Summarize the verified location and jurisdiction (County, Congressional District), the matching offices with their match basis and caveat, and the COLA percentage with the Q3 averages used. State that SSA's announcement is the official COLA figure."""
