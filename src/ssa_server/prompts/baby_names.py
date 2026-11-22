from typing import Literal
from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.prompt
    def demographic_name_analysis(name: str, sex: Literal["M", "F"]) -> str:
        """Guide an agent through a source-grounded analysis of a name's published historical popularity."""
        return f"""Analyze the historical popularity of the name '{name}' ({sex}) using the SSA Baby Name Server:
1. Read ssa://datasets/baby-names/metadata to understand the dataset coverage and methodology.
2. Query its popularity from 1900 to 2025 using get_baby_name_popularity.
3. Analyze its published counts and ranks by decade using get_name_trend, noting incomplete decades.
4. Identify its peak year and rank, and compare it against other names of that era using compare_names. Do not assume latest observed ranks refer to the same year.
5. Summarize observed trends and cite the SSA dataset and years queried. Explain that suppressed or missing records do not prove zero occurrences, and avoid unsupported cultural explanations or individual demographic inferences."""
