import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional, Literal

from ssa_server.core import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "names.db"

# Coverage of the bundled data/names/yobYYYY.txt files. Bump LAST_YEAR when SSA
# publishes the next annual file (each May) and the file is added to data/names/.
FIRST_YEAR = 1880
LAST_YEAR = 2025


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not target_path.exists():
        raise FileNotFoundError(
            f"Database file not found at: {target_path}. "
            "Please run scripts/baby_name_ingest.py to generate names.db."
        )
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    return conn


def query_name_popularity(
    name: str,
    sex: Literal["M", "F"],
    start_year: int = 1950,
    end_year: int = LAST_YEAR,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    clean_name = name.strip().capitalize()
    clean_sex = sex.upper().strip()

    if clean_sex not in ("M", "F"):
        raise ValueError("Parameter 'sex' must be either 'M' or 'F'.")
    if start_year < FIRST_YEAR or end_year > LAST_YEAR:
        raise ValueError(f"Year range must be between {FIRST_YEAR} and {LAST_YEAR}.")
    if start_year > end_year:
        raise ValueError("'start_year' must be less than or equal to 'end_year'.")

    conn = get_db_connection(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT year, rank, count
        FROM baby_names
        WHERE lower(name) = lower(?) AND sex = ? AND year BETWEEN ? AND ?
        ORDER BY year ASC
        """,
        (clean_name, clean_sex, start_year, end_year),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {
            "name": clean_name,
            "sex": clean_sex,
            "found": False,
            "message": f"No records found for name '{clean_name}' ({clean_sex}) between {start_year} and {end_year}. "
                       "Note: Names with fewer than 5 occurrences are omitted per SSA privacy standards.",
            "total_births": 0,
            "history": [],
        }

    history = [{"year": r["year"], "rank": r["rank"], "count": r["count"]} for r in rows]
    total_births = sum(r["count"] for r in history)

    peak_count_row = max(history, key=lambda x: x["count"])
    best_rank_row = min(history, key=lambda x: x["rank"])
    latest_row = history[-1]

    return {
        "name": clean_name,
        "sex": clean_sex,
        "found": True,
        "year_range": {"start": start_year, "end": end_year},
        "total_births_in_range": total_births,
        "peak_year": {
            "year": peak_count_row["year"],
            "count": peak_count_row["count"],
            "rank": peak_count_row["rank"],
        },
        "best_rank": {
            "year": best_rank_row["year"],
            "rank": best_rank_row["rank"],
            "count": best_rank_row["count"],
        },
        "latest_data": {
            "year": latest_row["year"],
            "rank": latest_row["rank"],
            "count": latest_row["count"],
        },
        "history": history,
    }


def query_top_names(
    year: int,
    sex: Literal["M", "F"] = "F",
    top_n: int = 10,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    clean_sex = sex.upper().strip()

    if clean_sex not in ("M", "F"):
        raise ValueError("Parameter 'sex' must be either 'M' or 'F'.")
    if year < FIRST_YEAR or year > LAST_YEAR:
        raise ValueError(f"Year must be between {FIRST_YEAR} and {LAST_YEAR}.")
    if top_n < 1 or top_n > 100:
        raise ValueError("Parameter 'top_n' must be between 1 and 100.")

    conn = get_db_connection(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT rank, name, count
        FROM baby_names
        WHERE year = ? AND sex = ?
        ORDER BY rank ASC
        LIMIT ?
        """,
        (year, clean_sex, top_n),
    )
    rows = cur.fetchall()

    cur.execute(
        "SELECT SUM(count) as total_sex_births FROM baby_names WHERE year = ? AND sex = ?",
        (year, clean_sex),
    )
    total_births_res = cur.fetchone()
    total_annual_births = total_births_res["total_sex_births"] if total_births_res else 0
    conn.close()

    names_list = []
    top_n_total = 0
    for r in rows:
        pct = (r["count"] / total_annual_births * 100) if total_annual_births > 0 else 0
        names_list.append({
            "rank": r["rank"],
            "name": r["name"],
            "count": r["count"],
            "percentage_of_births": round(pct, 2),
        })
        top_n_total += r["count"]

    top_share_pct = (top_n_total / total_annual_births * 100) if total_annual_births > 0 else 0

    return {
        "year": year,
        "sex": clean_sex,
        "top_n_requested": top_n,
        "top_names": names_list,
        "total_annual_registered_births": total_annual_births,
        "top_n_cumulative_share_percent": round(top_share_pct, 2),
    }


def query_name_trend(
    name: str,
    sex: Literal["M", "F"],
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    clean_name = name.strip().capitalize()
    clean_sex = sex.upper().strip()

    if clean_sex not in ("M", "F"):
        raise ValueError("Parameter 'sex' must be either 'M' or 'F'.")

    conn = get_db_connection(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT 
            (year / 10) * 10 as decade,
            SUM(count) as decade_count,
            MIN(rank) as best_rank,
            ROUND(AVG(rank), 1) as avg_rank,
            COUNT(year) as years_recorded
        FROM baby_names
        WHERE lower(name) = lower(?) AND sex = ?
        GROUP BY decade
        ORDER BY decade ASC
        """,
        (clean_name, clean_sex),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {
            "name": clean_name,
            "sex": clean_sex,
            "found": False,
            "message": f"No trend data available for '{clean_name}'.",
        }

    decades = [
        {
            "decade": f"{r['decade']}s",
            "decade_start": r["decade"],
            "total_births": r["decade_count"],
            "best_rank": r["best_rank"],
            "avg_rank": r["avg_rank"],
            "years_recorded": r["years_recorded"],
        }
        for r in rows
    ]

    peak_decade = max(decades, key=lambda d: d["total_births"])
    total_lifetime_births = sum(d["total_births"] for d in decades)

    return {
        "name": clean_name,
        "sex": clean_sex,
        "found": True,
        "total_lifetime_births": total_lifetime_births,
        "peak_decade": peak_decade["decade"],
        "peak_decade_births": peak_decade["total_births"],
        "decades": decades,
    }


def compare_names(
    names: List[str],
    sex: Literal["M", "F"],
    start_year: int = 2000,
    end_year: int = LAST_YEAR,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    clean_sex = sex.upper().strip()
    if clean_sex not in ("M", "F"):
        raise ValueError("Parameter 'sex' must be either 'M' or 'F'.")

    clean_names = [n.strip().capitalize() for n in names if n.strip()]
    if not clean_names:
        raise ValueError("Please provide at least one valid name to compare.")

    results = {}
    for n in clean_names:
        pop = query_name_popularity(n, clean_sex, start_year, end_year, db_path)
        if pop["found"]:
            results[n] = {
                "total_births": pop["total_births_in_range"],
                "peak_year": pop["peak_year"]["year"],
                "peak_count": pop["peak_year"]["count"],
                "best_rank": pop["best_rank"]["rank"],
                "latest_rank": pop["latest_data"]["rank"],
                "latest_count": pop["latest_data"]["count"],
            }
        else:
            results[n] = {"total_births": 0, "message": "No records in range."}

    return {
        "comparison_span": {"start_year": start_year, "end_year": end_year},
        "sex": clean_sex,
        "names_analyzed": clean_names,
        "metrics": results,
    }


def get_all_time_leaders(
    sex: Literal["M", "F"] = "F",
    top_n: int = 10,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    clean_sex = sex.upper().strip()
    if clean_sex not in ("M", "F"):
        raise ValueError("Parameter 'sex' must be either 'M' or 'F'.")
    if not 1 <= top_n <= 50:
        raise ValueError("Parameter 'top_n' must be between 1 and 50.")

    conn = get_db_connection(db_path)
    has_summary = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'name_totals'"
    ).fetchone()
    # name_totals is pre-aggregated at ingest; the GROUP BY fallback serves databases built
    # before it existed (run scripts/baby_name_ingest.py once to add it).
    source = "name_totals" if has_summary else (
        "(SELECT name, sex, SUM(count) AS total_occurrences, MIN(rank) AS peak_rank, "
        "COUNT(DISTINCT year) AS years_present FROM baby_names GROUP BY name, sex)"
    )
    rows = conn.execute(
        f"SELECT name, total_occurrences, peak_rank, years_present FROM {source} "
        "WHERE sex = ? ORDER BY total_occurrences DESC, name LIMIT ?",
        (clean_sex, top_n),
    ).fetchall()
    conn.close()

    leaders = [
        {
            "rank": i + 1,
            "name": r["name"],
            "total_occurrences": r["total_occurrences"],
            "peak_rank": r["peak_rank"],
            "years_on_record": r["years_present"],
        }
        for i, r in enumerate(rows)
    ]

    return {
        "title": f"All-Time Top {top_n} Given Names ({'Female' if clean_sex == 'F' else 'Male'}) in U.S. History ({FIRST_YEAR}-{LAST_YEAR})",
        "sex": clean_sex,
        "leaders": leaders,
    }


def get_dataset_metadata(db_path: Optional[Path] = None) -> Dict[str, Any]:
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(year), MAX(year), COUNT(DISTINCT name) FROM baby_names")
    total_records, min_year, max_year, unique_names = cur.fetchone()
    conn.close()

    return {
        "dataset_name": "Social Security Administration National Popular Baby Names",
        "agency": "U.S. Social Security Administration (SSA) - Office of the Chief Actuary",
        "tabulation_date": "March 1, 2026",
        "years_covered": f"{min_year} - {max_year}",
        "total_annual_files": max_year - min_year + 1,
        "total_records": total_records,
        "unique_given_names": unique_names,
        "privacy_threshold": "Only names with at least 5 occurrences in a birth year are tabulated.",
        "sorting_rule": "First on sex, then on occurrences descending, then alphabetical for ties.",
    }
