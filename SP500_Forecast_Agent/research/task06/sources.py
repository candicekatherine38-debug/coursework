"""Read-only bounded captures and separate evidence-based qualification."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import math
import shutil
import subprocess
import time

from sp500_agent.adapters import fred_csv, yahoo_daily_sp500
from sp500_agent.calendar import MarketCalendar
from .contracts import CAL, MARKET, UNIT, now, sha, require, utc, write

SOURCES = {
    "SP_DJI": ("https://www.spglobal.com/spdji/en/indices/equity/sp-500/", "html"),
    "FRED_SP500": ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500", "csv"),
    "YAHOO_GSPC": ("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=3mo&interval=1d", "json"),
}
RIGHTS = "RESTRICTED_OR_UNVERIFIED_LOCAL_ONLY"
TOLERANCE_POINTS = .01


def calendar():
    return MarketCalendar.load(CAL / "calendar_xnys_2016_2026.csv", CAL / "calendar_manifest.json")


def fetch_one(item, output):
    provider, (url, suffix) = item
    body = output / f"{provider}.{suffix}"
    hdr = output / f"{provider}.http_headers"
    command = ["curl", "--silent", "--show-error", "--location", "--max-redirs", "3",
               "--proto", "=https", "--proto-redir", "=https", "--max-time", "20",
               "--max-filesize", "5000000", "--dump-header", str(hdr), "--output", str(body),
               "--write-out", "%{http_code}\n%{url_effective}\n%{content_type}", url]
    started = now()
    begin = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, timeout=24)
    completed = now()
    lines = (result.stdout.splitlines() + ["", "", ""])[:3]
    return {"provider": provider, "url": url, "resolved_url": lines[1],
            "requested_at_utc": started, "retrieved_at_utc": completed,
            "elapsed_seconds": time.monotonic()-begin, "command": command,
            "returncode": result.returncode, "http_status": lines[0], "content_type": lines[2],
            "error": result.stderr.strip(), "raw_file": body.name if body.exists() else None,
            "sha256": sha(body) if body.exists() else None,
            "publication_at_utc": None, "vintage_at_utc": None, "rights": RIGHTS,
            "instrument": MARKET, "unit": UNIT, "timezone": "America/New_York"}


def parsed_capture(directory, record, cal):
    provider = record["provider"]
    require(provider in SOURCES and record["url"] == SOURCES[provider][0], "UNREGISTERED_SOURCE")
    require(record["returncode"] == 0 and record["http_status"] == "200", "SOURCE_REQUEST_FAILED")
    require(record["raw_file"] == f"{provider}.{SOURCES[provider][1]}", "WRONG_PROVIDER_PAYLOAD")
    p = directory / record["raw_file"]
    require(record["sha256"] == sha(p), "RAW_HASH_MISMATCH")
    if provider == "SP_DJI":
        return None, {"status": "DEFINITION_ONLY_NO_NUMERICAL_EXPORT"}
    if provider == "FRED_SP500":
        rows, _ = fred_csv(p, record, "SP500", cal)
    else:
        rows, info = yahoo_daily_sp500(p, record, cal)
        require(info["meta"].get("exchangeTimezoneName") == "America/New_York", "INVALID_YAHOO_TIMEZONE")
        require(not any(r["reason"] == "NONTRADING_DATE" for r in info["rejected_rows"]),
                "YAHOO_NONTRADING_DATE")
    by_day = {}
    for row in rows:
        day = row.session_date.isoformat()
        value = row.value
        require(row.observation_time_utc is not None and row.observation_time_utc <= utc(record["retrieved_at_utc"]),
                "NONTRADING_OR_INCOMPLETE_SESSION")
        require(day not in by_day and isinstance(value, (int, float)) and math.isfinite(value) and value > 0,
                "DUPLICATE_OR_INVALID_PRICE")
        by_day[day] = {"value": value, "observation_at_utc": row.observation_time_utc.isoformat(),
                       "publication_at_utc": None, "available_by_utc": record["retrieved_at_utc"]}
    require(by_day, "EMPTY_PRICE_SERIES")
    return by_day, {"status": "CAPTURED_CURRENT_VERSION", "latest_session": max(by_day),
                    "row_count": len(by_day), "first_publication": "UNKNOWN"}


def capture(directory):
    directory.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(lambda item: fetch_one(item, directory), SOURCES.items()))
    write(directory / "capture_manifest.json", records)
    return records


def check_capture(directory):
    from .contracts import read
    cal = calendar()
    result, prices = {}, {}
    for r in read(directory / "capture_manifest.json"):
        try:
            values, audit = parsed_capture(directory, r, cal)
            if values is not None:
                prices[r["provider"]] = values
                expected = cal.latest_close(utc(r["retrieved_at_utc"])).day.isoformat()
                audit.update(expected_session_at_capture=expected,
                             freshness="MATCHED_LATEST_SESSION" if max(values) == expected else "STALE",
                             latest_observation=values[max(values)])
        except Exception as exc:
            audit = {"status": "REJECTED", "error": str(exc)}
        result[r["provider"]] = {**audit, "http_status": r["http_status"], "raw_sha256": r["sha256"],
                                 "elapsed_seconds": r["elapsed_seconds"], "rights": RIGHTS}
    if "FRED_SP500" in prices and "YAHOO_GSPC" in prices:
        a, b = (prices[key] for key in ("FRED_SP500", "YAHOO_GSPC"))
        result["overlap"] = {"common_sessions": len(a.keys() & b.keys()),
                             "conflicts": [{"date": day, "FRED_SP500": a[day]["value"],
                                            "YAHOO_GSPC": b[day]["value"]}
                                           for day in sorted(a.keys() & b.keys())
                                           if abs(a[day]["value"] - b[day]["value"]) > TOLERANCE_POINTS]}
    return result


def capture_workbook(workbook, directory):
    directory.mkdir(parents=True, exist_ok=False)
    start = now()
    dest = directory / "workbook.xlsx"
    shutil.copyfile(workbook, dest)
    record = {"provider": "GROUP_BLOOMBERG_SPX_EXPORT", "source_id": "Bloomberg/SPX Index/PX_LAST",
              "market": MARKET, "unit": UNIT, "timezone": "America/New_York",
              "requested_at_utc": start, "retrieved_at_utc": now(), "publication_at_utc": None,
              "vintage_at_utc": None, "sha256": sha(dest), "rights": RIGHTS}
    write(directory / "workbook_capture.json", record)
    return record
