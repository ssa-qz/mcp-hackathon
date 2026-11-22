"""Deterministic tool-level evaluation of the SSA MCP server.

Every case calls a tool through a real MCP client (in-memory transport) and checks the
response against ground truth recomputed from the raw public files (see ``oracle.py``),
not merely that no error was raised. Categories:

* dataset        - baby-name answers vs. raw yobYYYY.txt files
* service_read   - field-office answers vs. the raw CSV
* service_write  - full book -> lookup -> cancel -> re-cancel lifecycle
* error_handling - invalid or adversarial inputs must fail cleanly with an actionable message
* live           - Census Geocoder / BLS calls (skip with --skip-live; BLS v1 allows ~25 calls/day)

Writes go to a throwaway SQLite file, never to data/sandbox_appointments.db.

Usage:
    python eval/run_eval.py [--iterations 10] [--live-iterations 2] [--skip-live]
"""

import argparse
import asyncio
import datetime
import json
import platform
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

import fastmcp  # noqa: E402
from fastmcp import Client  # noqa: E402

from eval.oracle import Oracle  # noqa: E402
from ssa_server.app import mcp  # noqa: E402
from ssa_server.config import settings  # noqa: E402

Check = Callable[[dict], list[str]]


@dataclass
class Case:
    id: str
    category: str
    tool: str
    args: dict[str, Any]
    check: Check | None = None
    expect_error: str | None = None  # substring the error message must contain ("" = any error)
    live: bool = False
    repeatable: bool = True  # False for state-changing calls: run exactly once


@dataclass
class Result:
    case: Case
    passed: bool
    failures: list[str] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)


def expect(condition: bool, message: str) -> list[str]:
    return [] if condition else [message]


def display_path(path: Path) -> str:
    """Repo-relative path when inside the repository, otherwise the absolute path."""
    path = path.resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def percentile(values: list[float], pct: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[pct - 1]


def next_open_weekday(office: dict[str, str], min_days_ahead: int = 7) -> datetime.date:
    day = datetime.date.today() + datetime.timedelta(days=min_days_ahead)
    while day.weekday() >= 5 or office[f"{day.strftime('%A').upper()} OPEN TIME"] in ("", "CLOSED"):
        day += datetime.timedelta(days=1)
    return day


# ---------------------------------------------------------------------------- cases
def build_cases(oracle: Oracle) -> list[Case]:
    top_2025 = oracle.year_rows(2025, "F")[:10]
    mary = oracle.name_counts("Mary", "F", 1880, 2025)
    trio = {n: sum(oracle.name_counts(n, "F", 2010, 2025).values()) for n in ("Emma", "Olivia", "Sophia")}
    leaders = oracle.all_time_leaders("F", 10)
    nv_codes = oracle.office_codes_in_state("NV")
    a00 = oracle.office("A00")
    zero_zip = oracle.first_leading_zero_zip_office()
    open_day = next_open_weekday(a00)
    next_saturday = (open_day + datetime.timedelta(days=5 - open_day.weekday())).isoformat()
    booking = {"office_code": "A00", "service_type": "CARD_REPLACEMENT", "appointment_date": open_day.isoformat(),
               "time_slot": "10:00 AM", "applicant_name": "Eval User", "contact_phone": "(555) 010-0000"}

    return [
        # --- dataset ---------------------------------------------------------
        Case("top10_female_2025", "dataset", "get_top_names_by_year", {"year": 2025, "sex": "F", "top_n": 10},
             lambda r: expect([(n["name"], n["count"]) for n in r["top_names"]] == top_2025,
                              f"top-10 mismatch: expected {top_2025}")),
        Case("mary_full_history", "dataset", "get_baby_name_popularity",
             {"name": "mary", "sex": "F", "start_year": 1880, "end_year": 2025},
             lambda r: expect(r["total_births_in_range"] == sum(mary.values()), "total births mismatch")
             + expect(r["peak_year"]["year"] == max(mary, key=mary.get), "peak year mismatch")
             + expect(len(r["history"]) == len(mary), "history length mismatch")),
        Case("liam_peak_decade", "dataset", "get_name_trend", {"name": "Liam", "sex": "M"},
             lambda r: expect(r["peak_decade"] == oracle.peak_decade("Liam", "M"), "peak decade mismatch")),
        Case("compare_three_names", "dataset", "compare_names",
             {"names": list(trio), "sex": "F", "start_year": 2010, "end_year": 2025},
             lambda r: [f"{n} total mismatch" for n, total in trio.items() if r["metrics"][n]["total_births"] != total]),
        Case("all_time_top10_female", "dataset", "get_all_time_popular_names", {"sex": "F", "top_n": 10},
             lambda r: expect([x["name"].lower() for x in r["leaders"]] == leaders, f"leaders mismatch: {leaders}")),
        Case("unknown_name_not_found", "dataset", "get_baby_name_popularity", {"name": "Zzyzxqv", "sex": "F"},
             lambda r: expect(r["found"] is False, "nonexistent name reported as found")),
        # --- service read ----------------------------------------------------
        Case("nevada_offices_complete", "service_read", "find_field_offices", {"state": "nv", "limit": 50},
             lambda r: expect({o["office_code"] for o in r["offices"]} == nv_codes, "NV office set mismatch")
             + expect(r["total_matches"] == len(nv_codes), "NV total_matches mismatch")),
        Case("office_a00_details", "service_read", "get_field_office_details", {"office_code": "a00"},
             lambda r: expect(r["found"], "A00 not found")
             + expect(r["office"]["phone"] == (a00["PHONE"] or None), "phone mismatch")
             + expect(r["office"]["address"]["lines"][0] == a00["ADDRESS LINE 1"], "address mismatch")),
        Case("leading_zero_zip_preserved", "service_read", "find_field_offices", {"zip_code": zero_zip["ZIP CODE"][:5]},
             lambda r: expect(zero_zip["OFFICE CODE"].upper() in {o["office_code"] for o in r["offices"]},
                              "office with leading-zero ZIP not returned")),
        Case("unknown_office_not_found", "service_read", "get_field_office_details", {"office_code": "ZZZ"},
             lambda r: expect(r["found"] is False and r["office"] is None, "nonexistent office reported as found")),
        # --- error handling --------------------------------------------------
        Case("reject_year_before_coverage", "error_handling", "get_top_names_by_year", {"year": 1700}, expect_error="1880"),
        Case("reject_oversized_top_n", "error_handling", "get_all_time_popular_names", {"top_n": 100000}, expect_error="50"),
        Case("reject_21_name_comparison", "error_handling", "compare_names", {"names": ["Emma"] * 21, "sex": "F"},
             expect_error="20"),
        Case("reject_reversed_year_range", "error_handling", "get_baby_name_popularity",
             {"name": "Emma", "sex": "F", "start_year": 2020, "end_year": 2000}, expect_error="start_year"),
        Case("reject_bad_zip_format", "error_handling", "find_field_offices", {"zip_code": "8905"}, expect_error=""),
        Case("reject_weekend_booking", "error_handling", "book_field_office_appointment",
             {**booking, "appointment_date": next_saturday}, expect_error="weekend", repeatable=False),
        Case("reject_past_date_booking", "error_handling", "book_field_office_appointment",
             {**booking, "appointment_date": "2020-01-06"}, expect_error="outside that window", repeatable=False),
        Case("reject_slot_outside_hours", "error_handling", "book_field_office_appointment",
             {**booking, "time_slot": "03:00 AM"}, expect_error="outside", repeatable=False),
        Case("reject_unknown_office_booking", "error_handling", "book_field_office_appointment",
             {**booking, "office_code": "ZZZ"}, expect_error="not found", repeatable=False),
        Case("reject_malformed_time_slot", "error_handling", "book_field_office_appointment",
             {**booking, "time_slot": "25:00"}, expect_error="", repeatable=False),
        Case("reject_oversized_notes", "error_handling", "book_field_office_appointment",
             {**booking, "notes": "x" * 50_000}, expect_error="", repeatable=False),
        # --- live federal APIs -----------------------------------------------
        Case("census_geocode_henderson", "live", "geocode_address",
             {"address": "10416 S Eastern Ave, Henderson, NV 89052"},
             lambda r: expect(r.get("matched") is True, f"not matched: {r.get('error') or r.get('message')}")
             + expect((r.get("county") or {}).get("state_fips") == "32", "state FIPS should be 32 (NV)")
             + expect("Clark" in ((r.get("county") or {}).get("name") or ""), "county should be Clark"),
             live=True),
        Case("census_unmatched_address", "live", "geocode_address", {"address": "zzzz not a real place 00000"},
             lambda r: expect(r.get("matched") is False, "nonsense address reported as matched"), live=True),
        Case("bls_cpi_w_six_months", "live", "get_cpi_cola_indicators", {"series": "CPI-W", "recent_months": 6},
             lambda r: expect(r.get("series_id") == "CWUR0000SA0", f"wrong series / error: {r.get('error')}")
             + expect(r.get("recent_observations_count") == 6, "expected 6 observations")
             + expect(r.get("latest_reading") is not None, "missing latest reading"),
             live=True),
        Case("cola_matches_statutory_formula", "live", "estimate_social_security_cola", {}, check_cola, live=True),
        Case("offices_for_resident_address", "live", "find_offices_near_address",
             {"address": "10416 S Eastern Ave, Henderson, NV 89052"},
             lambda r: expect(r.get("address_matched") is True, "address not matched")
             + expect(r.get("match_basis") == "same ZIP code", f"basis {r.get('match_basis')}")
             + expect([o["office_code"] for o in r.get("offices", [])] == ["A00"], "expected office A00"),
             live=True),
    ]


def check_cola(r: dict) -> list[str]:
    """Independently recompute the COLA from the six monthly CPI-W values the tool reports."""
    cola = r.get("determined_cola")
    if not cola:
        return [f"no COLA: {r.get('error')}"]
    monthly = cola["monthly_cpi_w"]
    years = sorted({key[:4] for key in monthly})
    averages = {y: round(sum(v for k, v in monthly.items() if k.startswith(y)) / 3, 3) for y in years}
    expected = max(round((averages[years[1]] / averages[years[0]] - 1) * 100, 1), 0.0)
    return (expect(averages == cola["q3_average_cpi_w"], "Q3 averages do not match monthly values")
            + expect(cola["cola_percent"] == expected, f"COLA {cola['cola_percent']} != recomputed {expected}"))


# ------------------------------------------------------------------------- runner
async def run_case(client: Client, case: Case, iterations: int) -> Result:
    latencies, response = [], None
    for _ in range(iterations if case.repeatable else 1):
        start = time.perf_counter()
        response = await client.call_tool(case.tool, case.args, raise_on_error=False)
        latencies.append((time.perf_counter() - start) * 1000)

    text = " ".join(getattr(block, "text", "") for block in response.content)
    if case.expect_error is not None:
        failures = expect(response.is_error, "expected a tool error, got success")
        if response.is_error:
            failures += expect(case.expect_error.lower() in text.lower(),
                               f"error message lacks '{case.expect_error}': {text[:160]}")
    elif response.is_error:
        failures = [f"unexpected tool error: {text[:200]}"]
    else:
        try:
            failures = case.check(response.structured_content) if case.check else []
        except (KeyError, TypeError, IndexError) as exc:
            failures = [f"malformed response ({type(exc).__name__}: {exc})"]
    return Result(case, not failures, failures, latencies)


async def run_lifecycle(client: Client, oracle: Oracle) -> list[Result]:
    """Book -> lookup -> cancel -> lookup -> cancel again, each step verified."""
    a00 = oracle.office("A00")
    day = next_open_weekday(a00)
    booking = {"office_code": "A00", "service_type": "RETIREMENT_BENEFITS", "appointment_date": day.isoformat(),
               "time_slot": "10:00 AM", "applicant_name": "Eval User", "contact_phone": "(555) 010-0000"}
    results: list[Result] = []

    async def step(step_id: str, tool: str, args: dict, check: Check) -> None:
        results.append(await run_case(client, Case(step_id, "service_write", tool, args, check, repeatable=False), 1))

    captured: dict[str, Any] = {}

    def check_booking(r: dict) -> list[str]:
        captured.update(r)
        return (expect(r["status"] == "CONFIRMED_SANDBOX", "booking not confirmed")
                + expect(bool(r["required_documents_checklist"]), "missing document checklist")
                + expect(r["appointment_date"] == day.isoformat(), "booked date mismatch"))

    await step("book_appointment", "book_field_office_appointment", booking, check_booking)
    if "confirmation_code" not in captured:
        return results
    lookup_args = {"confirmation_code": captured["confirmation_code"], "phone_last4": "0000"}

    results.append(await run_case(client, Case(
        "reject_double_booking", "service_write", "book_field_office_appointment",
        {**booking, "applicant_name": "Other User"}, expect_error="already booked", repeatable=False), 1))
    await step("wrong_phone_hides_record", "lookup_field_office_appointment", {**lookup_args, "phone_last4": "9999"},
               lambda r: expect(r["found"] is False and "Eval User" not in str(r),
                                "record exposed without phone verification"))
    await step("wrong_phone_cannot_cancel", "cancel_field_office_appointment", {**lookup_args, "phone_last4": "9999"},
               lambda r: expect(r["cancelled"] is False, "cancelled without phone verification"))
    await step("lookup_confirmed", "lookup_field_office_appointment", lookup_args,
               lambda r: expect(r["found"] and r["status"] == "CONFIRMED_SANDBOX", "lookup should show CONFIRMED")
               + expect(r["appointment_date"] == day.isoformat(), "stored date mismatch"))
    await step("cancel_appointment", "cancel_field_office_appointment", {**lookup_args, "reason": "eval cleanup"},
               lambda r: expect(r["cancelled"] is True, "cancel did not succeed"))
    await step("lookup_cancelled", "lookup_field_office_appointment", lookup_args,
               lambda r: expect(r["status"] == "CANCELLED_SANDBOX", "lookup should show CANCELLED"))
    await step("cancel_is_idempotent", "cancel_field_office_appointment", lookup_args,
               lambda r: expect(r["cancelled"] is False and r["status"] == "CANCELLED_SANDBOX",
                                "second cancel should be a no-op"))
    await step("lookup_unknown_code", "lookup_field_office_appointment",
               {"confirmation_code": "SSA-APT-0000-NOPE2345", "phone_last4": "0000"},
               lambda r: expect(r["found"] is False, "unknown code reported as found"))
    return results


def print_report(results: list[Result]) -> None:
    print(f"\n{'Case':<34} {'Category':<15} {'n':>3} {'p50 ms':>9} {'p95 ms':>9}  Result")
    print("-" * 84)
    for r in results:
        p50 = statistics.median(r.latencies_ms) if r.latencies_ms else float("nan")
        p95 = percentile(r.latencies_ms, 95) if r.latencies_ms else float("nan")
        print(f"{r.case.id:<34} {r.case.category:<15} {len(r.latencies_ms):>3} {p50:>9.1f} {p95:>9.1f}  "
              f"{'PASS' if r.passed else 'FAIL'}")
        for failure in r.failures:
            print(f"    - {failure}")

    print("-" * 84)
    categories = sorted({r.case.category for r in results})
    for category in categories:
        group = [r for r in results if r.case.category == category]
        print(f"{category:<15} {sum(r.passed for r in group)}/{len(group)} passed")
    total = sum(r.passed for r in results)
    print(f"{'OVERALL':<15} {total}/{len(results)} passed ({100 * total / len(results):.1f}%)")


def to_json(results: list[Result], args: argparse.Namespace) -> dict:
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "fastmcp": fastmcp.__version__},
        "settings": {"iterations": args.iterations, "live_iterations": args.live_iterations,
                     "skip_live": args.skip_live},
        "summary": {"passed": sum(r.passed for r in results), "total": len(results)},
        "cases": [
            {"id": r.case.id, "category": r.case.category, "tool": r.case.tool, "passed": r.passed,
             "failures": r.failures, "samples": len(r.latencies_ms),
             "p50_ms": round(statistics.median(r.latencies_ms), 2) if r.latencies_ms else None,
             "p95_ms": round(percentile(r.latencies_ms, 95), 2) if r.latencies_ms else None}
            for r in results
        ],
    }


async def main(args: argparse.Namespace) -> int:
    args.output = args.output.resolve()
    oracle = Oracle()
    cases = [c for c in build_cases(oracle) if not (c.live and args.skip_live)]

    with tempfile.TemporaryDirectory() as sandbox_dir:
        settings.APPOINTMENTS_DB_PATH = Path(sandbox_dir) / "eval_appointments.db"
        async with Client(mcp) as client:
            tool_count = len(await client.list_tools())
            print(f"Discovered {tool_count} tools; running {len(cases)} cases + write lifecycle "
                  f"({args.iterations} iterations local, {args.live_iterations} live).")
            results = [await run_case(client, c, args.live_iterations if c.live else args.iterations)
                       for c in cases]
            results += await run_lifecycle(client, oracle)

    print_report(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(to_json(results, args), indent=2), encoding="utf-8")
    print(f"\nWrote {display_path(args.output)}")
    return 0 if all(r.passed for r in results) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iterations", type=int, default=10, help="calls per local case (default 10)")
    parser.add_argument("--live-iterations", type=int, default=2, help="calls per live-API case (default 2)")
    parser.add_argument("--skip-live", action="store_true", help="skip Census/BLS network cases")
    parser.add_argument("--output", type=Path, default=ROOT / "eval" / "results" / "tool_eval.json")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
