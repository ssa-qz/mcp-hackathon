"""Build the local baby-names SQLite database from the public annual SSA text files."""

import argparse
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional

FILE_PATTERN = re.compile(r"yob(\d{4})\.txt")
BATCH_SIZE = 50_000


def add_name_totals(conn: sqlite3.Connection) -> None:
    """(Re)build the per-name all-time summary used by the all-time leaderboard.

    Aggregating 2M+ rows on every request took ~7 s; the leaderboard now reads this
    pre-aggregated table through an index instead.
    """
    conn.execute("DROP TABLE IF EXISTS name_totals;")
    conn.execute(
        """
        CREATE TABLE name_totals AS
        SELECT name, sex, SUM(count) AS total_occurrences, MIN(rank) AS peak_rank,
               COUNT(DISTINCT year) AS years_present
        FROM baby_names
        GROUP BY name, sex;
        """
    )
    conn.execute("CREATE INDEX idx_totals_sex_total ON name_totals (sex, total_occurrences DESC, name);")


def ensure_name_totals(db_path: Path) -> bool:
    """Add the summary table to a database built before it existed. Returns True if added."""
    conn = sqlite3.connect(str(db_path))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'name_totals'"
        ).fetchone()
        if exists:
            return False
        with conn:
            add_name_totals(conn)
        return True
    finally:
        conn.close()


def _find_source_files(data_dir: Path) -> list[tuple[int, Path]]:
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Baby-name source directory not found: {data_dir}")

    files_with_year: list[tuple[int, Path]] = []
    seen_years: set[int] = set()
    for path in sorted(data_dir.glob("yob*.txt")):
        match = FILE_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        year = int(match.group(1))
        if not 1880 <= year <= 2025:
            raise ValueError(f"Unsupported source year {year} in {path.name} (expected 1880-2025).")
        if year in seen_years:
            raise ValueError(f"Duplicate annual source file for {year} in {data_dir}.")
        seen_years.add(year)
        files_with_year.append((year, path))

    if not files_with_year:
        raise FileNotFoundError(f"No yobYYYY.txt annual files found in {data_dir}.")
    return files_with_year


def _build_database(source_files: list[tuple[int, Path]], db_path: Path) -> int:
    """Build and validate one staging database. The existing target is never opened here."""
    conn = sqlite3.connect(str(db_path))
    total_records = 0
    batch: list[tuple[str, str, int, int, int]] = []

    try:
        # Speed-oriented pragmas are safe for the disposable staging database.
        conn.execute("PRAGMA synchronous = OFF;")
        conn.execute("PRAGMA journal_mode = MEMORY;")
        conn.execute("PRAGMA cache_size = 100000;")
        conn.execute(
            """
            CREATE TABLE baby_names (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sex TEXT NOT NULL CHECK(sex IN ('M', 'F')),
                year INTEGER NOT NULL,
                count INTEGER NOT NULL CHECK(count >= 5),
                rank INTEGER NOT NULL,
                UNIQUE(name, sex, year)
            );
            """
        )

        for year, filepath in source_files:
            ranks = {"F": 0, "M": 0}
            previous_counts: dict[str, int] = {}
            with filepath.open("r", encoding="utf-8") as source:
                for line_number, raw_line in enumerate(source, start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    parts = line.split(",")
                    if len(parts) != 3:
                        raise ValueError(f"Malformed record in {filepath.name} at line {line_number}.")

                    name, sex, count_text = parts
                    if not name or sex not in ranks:
                        raise ValueError(f"Invalid name or sex in {filepath.name} at line {line_number}.")
                    try:
                        count = int(count_text)
                    except ValueError as exc:
                        raise ValueError(f"Invalid count in {filepath.name} at line {line_number}.") from exc
                    if count < 5:
                        raise ValueError(f"Count below SSA's publication threshold in {filepath.name} at line {line_number}.")
                    if sex in previous_counts and count > previous_counts[sex]:
                        raise ValueError(f"Counts are not ordered descending in {filepath.name} at line {line_number}.")

                    previous_counts[sex] = count
                    ranks[sex] += 1
                    batch.append((name, sex, year, count, ranks[sex]))
                    total_records += 1

                    if len(batch) >= BATCH_SIZE:
                        conn.executemany(
                            "INSERT INTO baby_names (name, sex, year, count, rank) VALUES (?, ?, ?, ?, ?)",
                            batch,
                        )
                        batch.clear()

        if batch:
            conn.executemany(
                "INSERT INTO baby_names (name, sex, year, count, rank) VALUES (?, ?, ?, ?, ?)",
                batch,
            )

        if total_records == 0:
            raise ValueError("No baby-name records were found in the source files.")

        conn.execute("CREATE INDEX idx_name_sex_year ON baby_names (name, sex, year);")
        conn.execute("CREATE INDEX idx_year_sex_rank ON baby_names (year, sex, rank);")
        conn.execute("CREATE INDEX idx_name_lower ON baby_names (lower(name));")
        add_name_totals(conn)
        conn.execute("ANALYZE;")
        conn.commit()

        integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Staging database failed integrity_check: {integrity}")
        stored_records = conn.execute("SELECT COUNT(*) FROM baby_names;").fetchone()[0]
        if stored_records != total_records:
            raise RuntimeError(f"Record count mismatch: inserted {total_records}, stored {stored_records}.")
    finally:
        conn.close()

    return total_records


def ingest_baby_names(data_dir: Path, db_path: Path, force: bool = False) -> Optional[int]:
    """Create the database once, or atomically replace it with ``force=True``.

    Without ``force``, an existing database is left untouched. Rebuilds happen in a
    temporary sibling directory and replace the destination only after all source
    rows, indexes, and SQLite integrity checks pass.
    """
    data_dir = Path(data_dir)
    db_path = Path(db_path)

    if db_path.exists() and not force:
        if ensure_name_totals(db_path):
            print(f"Added the all-time name_totals summary table to the existing database: {db_path}")
        else:
            print(f"Database already exists; leaving it untouched: {db_path}")
        print("Pass --force to build a verified replacement.")
        return None

    source_files = _find_source_files(data_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    source_years = [year for year, _ in source_files]
    print(f"Building baby-name database from {len(source_files)} annual files ({min(source_years)}-{max(source_years)}).")

    with tempfile.TemporaryDirectory(prefix=f".{db_path.name}.building-", dir=db_path.parent) as temp_dir:
        staged_db = Path(temp_dir) / db_path.name
        total_records = _build_database(source_files, staged_db)
        # Atomic on the same filesystem; a failed build never removes the previous DB.
        os.replace(staged_db, db_path)

    elapsed = time.perf_counter() - start
    size_mb = db_path.stat().st_size / (1024 * 1024)
    print(f"Built {total_records:,} records in {elapsed:.2f}s ({size_mb:.2f} MB): {db_path}")
    return total_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the local SSA baby-name SQLite database.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "names",
        help="Directory containing public SSA yobYYYY.txt source files.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "names.db",
        help="Output SQLite path (default: data/names.db).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Atomically replace an existing database after the new one passes validation.",
    )
    args = parser.parse_args()
    ingest_baby_names(args.data_dir, args.db_path, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
