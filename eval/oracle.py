"""Independent ground truth for evaluations, computed directly from the raw public files.

The server answers from SQLite (baby names) and a parsed CSV (field offices). This oracle
re-derives the same facts straight from ``data/names/yobYYYY.txt`` and the raw CSV with no
shared code, so an ingest, query, or normalization bug shows up as an eval failure instead
of being silently baked into both the answer and the expected value.
"""

import csv
from collections import defaultdict
from functools import cached_property
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Oracle:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.names_dir = data_dir / "names"
        self.office_csv = data_dir / "FO-Address-Open-Close-Times.csv"

    # ------------------------------------------------------------------ baby names
    def year_rows(self, year: int, sex: str) -> list[tuple[str, int]]:
        """(name, count) pairs for one year and sex, in SSA's published (rank) order."""
        rows = []
        with (self.names_dir / f"yob{year}.txt").open(encoding="utf-8") as stream:
            for line in stream:
                name, row_sex, count = line.strip().split(",")
                if row_sex == sex:
                    rows.append((name, int(count)))
        return rows

    @cached_property
    def _counts(self) -> dict[tuple[str, str], dict[int, int]]:
        """{(name_lower, sex): {year: count}} across every bundled annual file."""
        counts: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
        for path in sorted(self.names_dir.glob("yob*.txt")):
            year = int(path.stem[3:])
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    name, sex, count = line.strip().split(",")
                    counts[(name.lower(), sex)][year] = int(count)
        return counts

    def name_counts(self, name: str, sex: str, start: int, end: int) -> dict[int, int]:
        by_year = self._counts.get((name.lower(), sex), {})
        return {year: count for year, count in by_year.items() if start <= year <= end}

    def peak_decade(self, name: str, sex: str) -> str:
        decades: dict[int, int] = defaultdict(int)
        for year, count in self._counts[(name.lower(), sex)].items():
            decades[year // 10 * 10] += count
        return f"{max(decades, key=decades.get)}s"

    def all_time_leaders(self, sex: str, top_n: int) -> list[str]:
        totals = {
            name: sum(years.values())
            for (name, row_sex), years in self._counts.items()
            if row_sex == sex
        }
        return [name for name, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:top_n]]

    # ---------------------------------------------------------------- field offices
    @cached_property
    def offices(self) -> list[dict[str, str]]:
        with self.office_csv.open(encoding="utf-8-sig", newline="") as stream:
            return [
                {key: value.replace("\x00", "").strip() for key, value in row.items()}
                for row in csv.DictReader(stream)
            ]

    def office(self, code: str) -> dict[str, str]:
        return next(row for row in self.offices if row["OFFICE CODE"].upper() == code.upper())

    def office_codes_in_state(self, state: str) -> set[str]:
        return {row["OFFICE CODE"].upper() for row in self.offices if row["STATE"].upper() == state}

    def first_leading_zero_zip_office(self) -> dict[str, str]:
        return next(row for row in self.offices if row["ZIP CODE"].startswith("0"))
