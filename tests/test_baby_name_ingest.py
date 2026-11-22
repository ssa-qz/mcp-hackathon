import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from scripts.baby_name_ingest import ingest_baby_names


def write_year(data_dir: Path, year: int, contents: str) -> Path:
    path = data_dir / f"yob{year}.txt"
    path.write_text(contents, encoding="utf-8")
    return path


def connect(db_path: Path):
    # sqlite3's own context manager only commits; it never closes, which keeps the
    # file locked on Windows and makes the next atomic os.replace() fail.
    return closing(sqlite3.connect(db_path))


def get_rows(db_path: Path) -> list[tuple]:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT name, sex, year, count, rank FROM baby_names ORDER BY year, sex, rank"
        ).fetchall()


def test_builds_database_from_public_sources_and_adds_indexes(tmp_path):
    data_dir = tmp_path / "source"
    data_dir.mkdir()
    write_year(data_dir, 1880, "Mary,F,10\nAnna,F,5\nJohn,M,11\n")
    write_year(data_dir, 1881, "Mary,F,12\nJohn,M,13\n")
    db_path = tmp_path / "data" / "names.db"

    inserted = ingest_baby_names(data_dir, db_path)

    assert inserted == 5
    assert get_rows(db_path) == [
        ("Mary", "F", 1880, 10, 1),
        ("Anna", "F", 1880, 5, 2),
        ("John", "M", 1880, 11, 1),
        ("Mary", "F", 1881, 12, 1),
        ("John", "M", 1881, 13, 1),
    ]
    with connect(db_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(baby_names)")}
    assert {"idx_name_sex_year", "idx_year_sex_rank", "idx_name_lower"} <= indexes
    with connect(db_path) as conn:
        totals = conn.execute(
            "SELECT name, sex, total_occurrences, peak_rank, years_present FROM name_totals ORDER BY sex, name"
        ).fetchall()
    assert totals == [("Anna", "F", 5, 2, 1), ("Mary", "F", 22, 1, 2), ("John", "M", 24, 1, 2)]


def test_existing_database_without_summary_gets_it_added_in_place(tmp_path):
    data_dir = tmp_path / "source"
    data_dir.mkdir()
    write_year(data_dir, 1880, "Mary,F,10\nJohn,M,7\n")
    db_path = tmp_path / "names.db"
    ingest_baby_names(data_dir, db_path)
    with connect(db_path) as conn, conn:
        conn.execute("DROP TABLE name_totals")  # simulate a database built by an older version

    assert ingest_baby_names(data_dir, db_path) is None  # no rebuild
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM name_totals").fetchone()[0] == 2
    assert len(get_rows(db_path)) == 2


def test_existing_database_is_preserved_without_force(tmp_path):
    data_dir = tmp_path / "source"
    data_dir.mkdir()
    write_year(data_dir, 1880, "Mary,F,10\n")
    db_path = tmp_path / "names.db"
    ingest_baby_names(data_dir, db_path)
    original_bytes = db_path.read_bytes()

    write_year(data_dir, 1881, "Anna,F,10\n")
    assert ingest_baby_names(data_dir, db_path) is None
    assert db_path.read_bytes() == original_bytes
    assert len(get_rows(db_path)) == 1


def test_force_replaces_only_after_a_successful_build(tmp_path):
    data_dir = tmp_path / "source"
    data_dir.mkdir()
    write_year(data_dir, 1880, "Mary,F,10\n")
    db_path = tmp_path / "names.db"
    ingest_baby_names(data_dir, db_path)
    original_rows = get_rows(db_path)

    write_year(data_dir, 1881, "Anna,F,not-a-count\n")
    with pytest.raises(ValueError, match="Invalid count"):
        ingest_baby_names(data_dir, db_path, force=True)
    assert get_rows(db_path) == original_rows

    write_year(data_dir, 1881, "Anna,F,10\n")
    assert ingest_baby_names(data_dir, db_path, force=True) == 2
    assert get_rows(db_path) == [
        ("Mary", "F", 1880, 10, 1),
        ("Anna", "F", 1881, 10, 1),
    ]


def test_missing_or_malformed_sources_do_not_replace_existing_database(tmp_path):
    data_dir = tmp_path / "source"
    data_dir.mkdir()
    db_path = tmp_path / "names.db"
    db_path.write_bytes(b"keep-existing-file")

    with pytest.raises(FileNotFoundError, match="No yobYYYY"):
        ingest_baby_names(data_dir, db_path, force=True)
    assert db_path.read_bytes() == b"keep-existing-file"

    write_year(data_dir, 1880, "Mary,F,10\ninvalid,row\n")
    with pytest.raises(ValueError, match="Malformed record"):
        ingest_baby_names(data_dir, db_path, force=True)
    assert db_path.read_bytes() == b"keep-existing-file"


def test_out_of_order_source_fails_without_publishing_database(tmp_path):
    data_dir = tmp_path / "source"
    data_dir.mkdir()
    write_year(data_dir, 1880, "Mary,F,5\nAnna,F,10\n")
    db_path = tmp_path / "names.db"

    with pytest.raises(ValueError, match="not ordered descending"):
        ingest_baby_names(data_dir, db_path)
    assert not db_path.exists()
    assert not list(db_path.parent.glob(".names.db.building-*"))


def test_no_matching_input_does_not_create_output_directory(tmp_path):
    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    db_path = tmp_path / "nested" / "names.db"

    with pytest.raises(FileNotFoundError, match="No yobYYYY"):
        ingest_baby_names(data_dir, db_path)
    assert not db_path.exists()
    assert not db_path.parent.exists()
