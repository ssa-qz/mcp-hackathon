from fastmcp import FastMCP
from ssa_server.core.public_services import (
    estimate_cola,
    geocode_address as query_geocode_address,
    get_cpi_cola_indicators as query_cpi_cola_indicators,
)
from ssa_server.config import settings
from ssa_server.utils import agent_errors
from ssa_server.models import AddressString, InflationSeries, RecentMonths


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="geocode_address",
        annotations={
            "title": "Geocode Address & Resolve Boundaries",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @agent_errors
    def geocode_address(
        address: AddressString,
    ) -> dict:
        """Geocode a street address and resolve its administrative boundaries via U.S. Census Bureau.

        Translates a street address into coordinates (latitude and longitude) and resolves its
        County FIPS, Congressional District, State Legislative Districts, and Census Tract.

        Args:
            address: Full single-line street address (e.g. '10416 S Eastern Ave, Henderson, NV 89052').

        Returns:
            Matched address, coordinates (latitude/longitude), county, congressional district,
            census tract, and boundary details.
        """
        return query_geocode_address(
            address=address,
            benchmark=settings.CENSUS_BENCHMARK,
            vintage=settings.CENSUS_VINTAGE,
        )

    @mcp.tool(
        name="get_cpi_cola_indicators",
        annotations={
            "title": "Get BLS CPI / COLA Indicators",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @agent_errors
    def get_cpi_cola_indicators(
        series: InflationSeries = "CPI-W",
        recent_months: RecentMonths = 12,
    ) -> dict:
        """Retrieve live monthly Consumer Price Index readings (CPI-W or CPI-U) from the U.S. Bureau of Labor Statistics.

        Use for inflation questions (latest index value, 12-month change, recent trend). For the
        Social Security cost-of-living adjustment percentage itself, use estimate_social_security_cola.

        Args:
            series: 'CPI-W' (statutory index for Social Security COLA) or 'CPI-U' (broad headline inflation). Default 'CPI-W'.
            recent_months: Number of recent monthly observations to return (1 to 24, default 12).

        Returns:
            Latest index value, 12-month change percentage, recent monthly readings, and SSA COLA calculation notes.
        """
        return query_cpi_cola_indicators(
            series=series,
            recent_months=recent_months,
        )

    @mcp.tool(
        name="estimate_social_security_cola",
        annotations={
            "title": "Compute Social Security COLA from Live CPI-W",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @agent_errors
    def estimate_social_security_cola() -> dict:
        """Compute the Social Security cost-of-living adjustment (COLA) from live BLS CPI-W data.

        Applies the statutory formula: average CPI-W for July-September of the latest complete year
        divided by the same quarter one year earlier, minus 1, rounded to the nearest 0.1% (never
        negative). Answers "What is the COLA for next year?" or "How much will benefits rise?".
        When newer monthly data exists, also returns a clearly-labeled provisional projection for the
        following year. SSA's own announcement remains the official figure.

        Returns:
            determined_cola (percent, benefits_payable_from, both Q3 averages and the six monthly
            values used, formula), optional provisional_projection, and caveats.
        """
        return estimate_cola()
