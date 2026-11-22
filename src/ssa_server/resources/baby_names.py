import json
from fastmcp import FastMCP
from ssa_server.core.baby_name_data import get_all_time_leaders, get_dataset_metadata
from ssa_server.config import settings


def register(mcp: FastMCP) -> None:
    @mcp.resource("ssa://datasets/baby-names/metadata", mime_type="application/json")
    def dataset_metadata() -> str:
        """Official SSA methodology and catalog metadata for the National Baby Names dataset."""
        meta = get_dataset_metadata(db_path=settings.BABY_NAMES_DB_PATH)
        return json.dumps(meta, indent=2)

    @mcp.resource("ssa://datasets/baby-names/all-time-top-10", mime_type="text/markdown")
    def all_time_top_10_resource() -> str:
        """Markdown summary of the all-time top 10 male and female names in the published SSA records."""
        males = get_all_time_leaders("M", top_n=10, db_path=settings.BABY_NAMES_DB_PATH)["leaders"]
        females = get_all_time_leaders("F", top_n=10, db_path=settings.BABY_NAMES_DB_PATH)["leaders"]

        lines = [
            "# SSA All-Time Top 10 Names (1880–2025)",
            "",
            "### Female Leaders",
            "| Rank | Name | Total Births | Years on Record |",
            "|---|---|---|---|",
        ]
        for leader in females:
            lines.append(f"| {leader['rank']} | {leader['name']} | {leader['total_occurrences']:,} | {leader['years_on_record']} |")

        lines.extend([
            "",
            "### Male Leaders",
            "| Rank | Name | Total Births | Years on Record |",
            "|---|---|---|---|",
        ])
        for leader in males:
            lines.append(f"| {leader['rank']} | {leader['name']} | {leader['total_occurrences']:,} | {leader['years_on_record']} |")

        return "\n".join(lines)
