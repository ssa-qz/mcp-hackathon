from fastmcp import FastMCP
from ssa_server.core.baby_name_data import (
    LAST_YEAR,
    compare_names as query_compare_names,
    get_all_time_leaders,
    query_name_popularity,
    query_name_trend,
    query_top_names,
)
from ssa_server.config import settings
from ssa_server.utils import agent_errors
from ssa_server.models import DatasetYear, NameList, NameText, SexType, TopNAllTime, TopNYear


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_baby_name_popularity",
        annotations={
            "title": "Get Baby Name Popularity",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def get_baby_name_popularity(
        name: NameText,
        sex: SexType,
        start_year: DatasetYear = 1950,
        end_year: DatasetYear = LAST_YEAR,
    ) -> dict:
        """Retrieve published SSA name counts and national ranks for a given name and recorded sex.

        Args:
            name: Given name to query (e.g. 'Emma', 'Liam', 'Mary'). Case-insensitive.
            sex: Sex of applicant: 'M' for male, 'F' for female.
            start_year: Start year between 1880 and 2025 (default: 1950).
            end_year: End year between 1880 and 2025 (default: 2025).

        Returns:
            Published count totals, peak year, best rank, latest observed rank, and year-by-year history.
        """
        return query_name_popularity(
            name=name,
            sex=sex,
            start_year=start_year,
            end_year=end_year,
            db_path=settings.BABY_NAMES_DB_PATH,
        )

    @mcp.tool(
        name="get_top_names_by_year",
        annotations={
            "title": "Get Top Names by Year",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def get_top_names_by_year(
        year: DatasetYear,
        sex: SexType = "F",
        top_n: TopNYear = 10,
    ) -> dict:
        """Get the top N most popular names in the published SSA records for a birth year and sex.

        Args:
            year: Birth year between 1880 and 2025.
            sex: Sex of applicant: 'M' for male, 'F' for female (default: 'F').
            top_n: Number of leading names to return (1 to 100, default: 10).

        Returns:
            Top names with rank, count, and percentage share of published counts for the selected sex and year.
        """
        return query_top_names(
            year=year,
            sex=sex,
            top_n=top_n,
            db_path=settings.BABY_NAMES_DB_PATH,
        )

    @mcp.tool(
        name="get_name_trend",
        annotations={
            "title": "Get Multi-Decade Name Trend",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def get_name_trend(
        name: NameText,
        sex: SexType,
    ) -> dict:
        """Summarize a name's published counts and ranks by decade.

        Aggregates the available years from the 1880s to the 2020s to identify the peak decade.
        The 2020s are incomplete, and rank averages include only years with published records.

        Args:
            name: Given name to analyze (e.g. 'Jennifer', 'Michael').
            sex: Sex: 'M' for male, 'F' for female.

        Returns:
            Decade-by-decade totals, average and best rank per decade, and peak decade.
        """
        return query_name_trend(
            name=name,
            sex=sex,
            db_path=settings.BABY_NAMES_DB_PATH,
        )

    @mcp.tool(
        name="compare_names",
        annotations={
            "title": "Compare Names Across Decades",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def compare_names(
        names: NameList,
        sex: SexType,
        start_year: DatasetYear = 2000,
        end_year: DatasetYear = LAST_YEAR,
    ) -> dict:
        """Compare published counts, peak counts, and latest observed ranks for multiple names.

        Args:
            names: 1 to 20 names to compare (e.g. ['Sophia', 'Olivia', 'Emma']).
            sex: Sex: 'M' for male, 'F' for female.
            start_year: Comparison start year (1880-2025, default: 2000).
            end_year: Comparison end year (1880-2025, default: 2025).

        Returns:
            Side-by-side metrics including peak counts, best ranks, and latest observed ranks in the range.
        """
        return query_compare_names(
            names=names,
            sex=sex,
            start_year=start_year,
            end_year=end_year,
            db_path=settings.BABY_NAMES_DB_PATH,
        )

    @mcp.tool(
        name="get_all_time_popular_names",
        annotations={
            "title": "Get All-Time Popular Names (1880-2025)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @agent_errors
    def get_all_time_popular_names(
        sex: SexType = "F",
        top_n: TopNAllTime = 10,
    ) -> dict:
        """Retrieve the most popular names by summed published counts across SSA records for 1880-2025.

        Args:
            sex: 'M' for male, 'F' for female (default: 'F').
            top_n: Number of all-time leaders to retrieve (1 to 50, default: 10).

        Returns:
            All-time leaders, summed published occurrences, and peak rank achieved.
        """
        return get_all_time_leaders(
            sex=sex,
            top_n=top_n,
            db_path=settings.BABY_NAMES_DB_PATH,
        )
