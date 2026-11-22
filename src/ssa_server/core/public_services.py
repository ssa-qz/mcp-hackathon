import os
import time
import httpx
from typing import Any, Dict, Literal, Optional

CENSUS_GEOGRAPHIES_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
BLS_API_BASE_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data"
# Optional free BLS registration key: v2 raises the limit from ~25 to 500 queries/day.
BLS_API_V2_BASE_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data"
BLS_CACHE_TTL_SECONDS = 6 * 60 * 60  # CPI is published monthly; avoid burning the daily quota
_bls_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}

BLS_SERIES_MAP = {
    "CPI-W": {
        "series_id": "CWUR0000SA0",
        "name": "Consumer Price Index for Urban Wage Earners and Clerical Workers (CPI-W)",
        "ssa_relevance": (
            "Statutory benchmark for Social Security Cost-of-Living Adjustments (COLA). "
            "SSA calculates annual COLA based on percentage increase in average third-quarter "
            "(July, August, September) CPI-W from the prior year's third quarter."
        ),
    },
    "CPI-U": {
        "series_id": "CUUR0000SA0",
        "name": "Consumer Price Index for All Urban Consumers (CPI-U)",
        "ssa_relevance": (
            "Headline national inflation rate covering approximately 93% of the U.S. population. "
            "Often compared against CPI-W to analyze broader consumer expenditure trends."
        ),
    },
}

DEFAULT_TIMEOUT = 10.0

DATA_NOTICE = (
    "Live federal public services read integration. Coordinates and administrative boundaries "
    "(counties, congressional districts, census tracts) are derived from the U.S. Census Bureau "
    "MAF/TIGER benchmark. Consumer Price Index and COLA economic indicators are retrieved live from "
    "the U.S. Bureau of Labor Statistics (BLS) Public Data API."
)


def describe_upstream_failure(service: str, exc: Exception) -> Dict[str, str]:
    """Summarize a failed upstream call without echoing URLs, query strings, or bodies.

    httpx exception text embeds the full request URL, which here would contain the user's
    street address (Census) or an API key (BLS v2), so only the failure class is reported.
    """
    if isinstance(exc, httpx.TimeoutException):
        reason, hint = "timed out", "The federal service is slow or unreachable; retry in a minute."
    elif isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        reason = f"returned HTTP {code}"
        hint = ("Rate limit reached; retry later." if code == 429
                else "The federal service rejected or failed the request; retry later.")
    elif isinstance(exc, httpx.RequestError):
        reason, hint = "could not be reached", "Check network or proxy connectivity to the federal service."
    else:
        reason, hint = "returned an unreadable response", "Retry later; the service may be degraded."
    return {"error": f"{service} request {reason}.", "hint": hint}


def geocode_address(
    address: str,
    benchmark: str = "Public_AR_Current",
    vintage: str = "Current_Current",
    client: Optional[httpx.Client] = None,
) -> Dict[str, Any]:
    """Geocode a street address and resolve its geographic boundaries via U.S. Census Bureau.

    Retrieves coordinates (latitude/longitude), County FIPS, Congressional District,
    State Legislative Districts, and Census Tract information.

    Args:
        address: Full single-line address string (e.g. '10416 S Eastern Ave, Henderson, NV 89052').
        benchmark: Census spatial benchmark dataset (default: 'Public_AR_Current').
        vintage: Census geography vintage (default: 'Current_Current').
        client: Optional pre-configured httpx.Client (useful for testing and session reuse).

    Returns:
        Dictionary containing matched address, coordinates, county, congressional district,
        census tract, and boundary details.
    """
    clean_addr = address.strip()
    if not clean_addr:
        raise ValueError("Parameter 'address' must not be empty.")

    params = {
        "address": clean_addr,
        "benchmark": benchmark,
        "vintage": vintage,
        "format": "json",
    }

    def _do_request(c: httpx.Client) -> Dict[str, Any]:
        resp = c.get(CENSUS_GEOGRAPHIES_URL, params=params)
        resp.raise_for_status()
        return resp.json()

    try:
        if client is not None:
            data = _do_request(client)
        else:
            with httpx.Client(timeout=DEFAULT_TIMEOUT, trust_env=True) as c:
                data = _do_request(c)
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "query_address": clean_addr,
            "matched": False,
            **describe_upstream_failure("Census Geocoder", exc),
            "notice": DATA_NOTICE,
        }

    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        return {
            "query_address": clean_addr,
            "matched": False,
            "message": "No matching address found by U.S. Census Geocoder.",
            "notice": DATA_NOTICE,
        }

    primary = matches[0]
    coords = primary.get("coordinates", {})
    # Note: Census Geocoder returns 'x' as Longitude, 'y' as Latitude
    lon = coords.get("x")
    lat = coords.get("y")

    geos = primary.get("geographies", {})

    # Extract County
    counties = geos.get("Counties", [])
    county_info = None
    if counties:
        c = counties[0]
        county_info = {
            "name": c.get("NAME"),
            "geoid": c.get("GEOID"),
            "state_fips": c.get("STATE"),
            "county_fips": c.get("COUNTY"),
        }

    # Extract Congressional District (handles 118th, 119th, 120th etc.)
    cong_info = None
    for k, v in geos.items():
        if "Congressional District" in k and v:
            cd = v[0]
            cong_info = {
                "name": cd.get("NAME"),
                "geoid": cd.get("GEOID"),
                "session": k,
            }
            break

    # Extract Census Tract
    tracts = geos.get("Census Tracts", [])
    tract_info = None
    if tracts:
        t = tracts[0]
        tract_info = {
            "name": t.get("NAME"),
            "geoid": t.get("GEOID"),
            "tract": t.get("TRACT"),
        }

    # Extract State Legislative Districts
    leg_upper = geos.get("2026 State Legislative Districts - Upper") or geos.get("State Legislative Districts - Upper", [])
    leg_lower = geos.get("2026 State Legislative Districts - Lower") or geos.get("State Legislative Districts - Lower", [])

    return {
        "query_address": clean_addr,
        "matched": True,
        "matched_address": primary.get("matchedAddress"),
        "latitude": lat,
        "longitude": lon,
        "county": county_info,
        "congressional_district": cong_info,
        "census_tract": tract_info,
        "state_legislative_districts": {
            "upper": leg_upper[0].get("NAME") if leg_upper else None,
            "lower": leg_lower[0].get("NAME") if leg_lower else None,
        },
        "tiger_line": primary.get("tigerLine", {}),
        "address_components": primary.get("addressComponents", {}),
        "notice": DATA_NOTICE,
    }


def _fetch_bls_monthly(
    series_id: str, client: Optional[httpx.Client] = None
) -> tuple[list[Dict[str, Any]], Optional[Dict[str, str]]]:
    """Return (monthly observations newest-first, None) or ([], error/hint dict).

    Uses the v2 endpoint when BLS_API_KEY is set, and a 6-hour in-process cache for live calls.
    """
    api_key = os.getenv("BLS_API_KEY")
    url = f"{BLS_API_V2_BASE_URL if api_key else BLS_API_BASE_URL}/{series_id}"
    params = {"registrationkey": api_key} if api_key else None
    headers = {"User-Agent": "(SSAMCPHackathon, open.data@ssa.gov)"}

    def _do_request(c: httpx.Client) -> Dict[str, Any]:
        resp = c.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    cached = _bls_cache.get(series_id) if client is None else None
    try:
        if cached and time.monotonic() - cached[0] < BLS_CACHE_TTL_SECONDS:
            data = cached[1]
        elif client is not None:
            data = _do_request(client)
        else:
            with httpx.Client(timeout=DEFAULT_TIMEOUT, trust_env=True) as c:
                data = _do_request(c)
    except (httpx.HTTPError, ValueError) as exc:
        return [], describe_upstream_failure("BLS Public Data API", exc)

    if data.get("status") != "REQUEST_SUCCEEDED":
        messages = data.get("message") or ["Unknown BLS API error"]
        return [], {"error": f"BLS API error: {'; '.join(messages)}",
                    "hint": "BLS v1 allows ~25 requests/day per IP; set BLS_API_KEY for 500/day."}
    series_data = data.get("Results", {}).get("series", [])
    if not series_data:
        return [], {"error": "No series data returned from BLS API.", "hint": "Retry later."}

    if client is None:
        _bls_cache[series_id] = (time.monotonic(), data)
    # Monthly periods only (M13 is the annual average).
    return [item for item in series_data[0].get("data", [])
            if item.get("period", "").startswith("M") and item.get("period") != "M13"], None


def get_cpi_cola_indicators(
    series: Literal["CPI-W", "CPI-U"] = "CPI-W",
    recent_months: int = 12,
    client: Optional[httpx.Client] = None,
) -> Dict[str, Any]:
    """Retrieve live Consumer Price Index and COLA economic indicators from the U.S. Bureau of Labor Statistics.

    Social Security Cost-of-Living Adjustments (COLA) are legally tied to CPI-W.
    This tool retrieves monthly index values, calculates annual inflation changes, and explains
    the COLA determination context.

    Args:
        series: 'CPI-W' (statutory index for Social Security COLA) or 'CPI-U' (broad headline inflation). Default 'CPI-W'.
        recent_months: Number of recent monthly observations to return (1 to 24, default 12).
        client: Optional pre-configured httpx.Client (useful for testing and session reuse).

    Returns:
        Latest index value, 12-month change percentage, recent monthly readings, and SSA COLA calculation notes.
    """
    clean_series = series.strip().upper()
    if clean_series not in BLS_SERIES_MAP:
        raise ValueError("Parameter 'series' must be either 'CPI-W' or 'CPI-U'.")
    if not (1 <= recent_months <= 24):
        raise ValueError("Parameter 'recent_months' must be between 1 and 24.")

    meta = BLS_SERIES_MAP[clean_series]
    series_id = meta["series_id"]
    monthly_items, failure = _fetch_bls_monthly(series_id, client)
    if failure:
        return {"series": clean_series, "series_id": series_id, **failure, "notice": DATA_NOTICE}

    readings = []
    for item in monthly_items[:recent_months]:
        try:
            val = float(item.get("value", 0))
        except (ValueError, TypeError):
            val = None

        readings.append({
            "year": int(item.get("year", 0)),
            "period": item.get("period"),
            "period_name": item.get("periodName"),
            "index_value": val,
            "is_latest": item.get("latest") == "true",
        })

    latest_reading = readings[0] if readings else None

    # Calculate 12-month change if 12 or more months are available
    annual_change_pct = None
    if len(monthly_items) >= 13:
        try:
            val_now = float(monthly_items[0]["value"])
            val_year_ago = float(monthly_items[12]["value"])
            if val_year_ago > 0:
                annual_change_pct = round(((val_now - val_year_ago) / val_year_ago) * 100, 2)
        except (ValueError, TypeError, KeyError):
            annual_change_pct = None

    return {
        "series": clean_series,
        "series_id": series_id,
        "series_name": meta["name"],
        "ssa_cola_relevance": meta["ssa_relevance"],
        "latest_reading": latest_reading,
        "annual_12_month_inflation_rate_percent": annual_change_pct,
        "recent_observations_count": len(readings),
        "recent_observations": readings,
        "notice": DATA_NOTICE,
    }


def _q3_average(values: Dict[tuple[int, int], float], year: int) -> Optional[float]:
    """Average July-September CPI-W, rounded to 3 decimals as SSA does; None if incomplete."""
    months = [values.get((year, m)) for m in (7, 8, 9)]
    return round(sum(months) / 3, 3) if all(v is not None for v in months) else None


def estimate_cola(client: Optional[httpx.Client] = None) -> Dict[str, Any]:
    """Compute the Social Security COLA from live CPI-W using the statutory formula.

    COLA = (average CPI-W for Q3 of year Y) / (average CPI-W for Q3 of year Y-1) - 1, rounded to
    the nearest 0.1% (42 U.S.C. 415(i)); a decrease yields 0%. The COLA determined from Q3 of
    year Y applies to benefits payable starting January of Y+1. If CPI-W data for the following
    year has started arriving, a clearly-labeled provisional projection is also returned.
    """
    series_id = BLS_SERIES_MAP["CPI-W"]["series_id"]
    monthly_items, failure = _fetch_bls_monthly(series_id, client)
    if failure:
        return {"series_id": series_id, **failure, "notice": DATA_NOTICE}

    values: Dict[tuple[int, int], float] = {}
    for item in monthly_items:
        try:
            values[(int(item["year"]), int(item["period"][1:]))] = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
    q3_years = sorted((y for y in {y for y, _ in values} if _q3_average(values, y) is not None), reverse=True)
    year = next((y for y in q3_years if _q3_average(values, y - 1) is not None), None)
    if year is None:
        return {"series_id": series_id, "error": "BLS returned too little history for two complete Q3 periods.",
                "hint": "Retry later.", "notice": DATA_NOTICE}

    q3_now, q3_base = _q3_average(values, year), _q3_average(values, year - 1)
    raw_change = (q3_now / q3_base - 1) * 100
    result: Dict[str, Any] = {
        "series_id": series_id,
        "determined_cola": {
            "cola_percent": max(round(raw_change, 1), 0.0),
            "benefits_payable_from": f"January {year + 1}",
            "q3_average_cpi_w": {str(year): q3_now, str(year - 1): q3_base},
            "monthly_cpi_w": {f"{year}-{m:02d}": values[(year, m)] for m in (7, 8, 9)}
            | {f"{year - 1}-{m:02d}": values[(year - 1, m)] for m in (7, 8, 9)},
            "unrounded_change_percent": round(raw_change, 3),
            "formula": f"Q3 {year} avg / Q3 {year - 1} avg - 1, rounded to nearest 0.1% (negative -> 0%)",
        },
        "caveats": [
            "Computed from BLS data with the statutory formula; SSA's announcement is the official figure.",
            "If no COLA was payable in the prior year, SSA's base is the last year a COLA was determined; "
            "this calculation assumes the immediately preceding year.",
        ],
        "notice": DATA_NOTICE,
    }

    following = sorted(((y, m) for (y, m) in values if (y, m) > (year, 9)), reverse=True)[:3]
    if following and year + 1 not in q3_years:
        avg = sum(values[k] for k in following) / len(following)
        result["provisional_projection"] = {
            "projected_cola_percent": max(round((avg / q3_now - 1) * 100, 1), 0.0),
            "for_benefits_payable_from": f"January {year + 2}",
            "months_used": [f"{y}-{m:02d}" for y, m in sorted(following)],
            "label": (f"NOT an official estimate: compares the latest available months with Q3 {year}. "
                      f"The actual COLA uses July-September {year + 1} CPI-W."),
        }
    return result


def get_public_services_metadata() -> Dict[str, Any]:
    """Metadata describing the public services integrations."""
    return {
        "service_name": "Federal Public Services Read Integration",
        "description": (
            "Live read-only integrations with the U.S. Census Bureau Geocoding Services API "
            "(Geographies endpoint) and the U.S. Bureau of Labor Statistics (BLS) Public Data API "
            "(Consumer Price Index & Social Security COLA series)."
        ),
        "endpoints": {
            "census_geographies": CENSUS_GEOGRAPHIES_URL,
            "bls_cpi_api": BLS_API_BASE_URL,
        },
        "capabilities": [
            "Geocode street addresses and resolve administrative boundaries (County FIPS, Congressional District, Census Tract).",
            "Retrieve live Consumer Price Index (CPI-W and CPI-U) economic indicators governing the Social Security Cost-of-Living Adjustment (COLA).",
        ],
        "mission_alignment": (
            "Directly connects public SSA information assets with authoritative inter-agency services: "
            "geographical jurisdictions from the Census Bureau and statutory Cost-of-Living Adjustment (COLA) data from the Bureau of Labor Statistics."
        ),
        "authentication": "None required. Open federal public web services.",
    }
