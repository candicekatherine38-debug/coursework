"""Normalise saved provider snapshots without inventing release/vintage times."""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import csv
import hashlib
import io
import json

from .calendar import NY
from .contracts import INDEX_POINTS, SP500, UTC, ContractError, Observation, Qualification, timestamp


def _base(path, provenance):
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if provenance.get("sha256") and sha != provenance["sha256"]:
        raise ContractError("RAW_HASH_MISMATCH")
    retrieved = provenance.get("retrieved_at_utc", provenance.get("received_at_utc"))
    if not retrieved:
        raise ContractError("MISSING_ACTUAL_RETRIEVAL_TIME")
    url = provenance.get("url", provenance.get("source_url", provenance.get("requested_url")))
    if not url:
        raise ContractError("MISSING_SOURCE_URL")
    return raw, sha, timestamp(retrieved), url


def yahoo_chart(path, provenance, symbol, interval):
    raw, sha, retrieved, url = _base(path, provenance)
    if not url:
        raise ContractError("MISSING_SOURCE_URL")
    try:
        payload = json.loads(raw)
        if payload["chart"].get("error"):
            raise ContractError("PROVIDER_ERROR")
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        if meta["symbol"] != symbol or meta["dataGranularity"] != interval:
            raise ContractError("WRONG_PROVIDER_INSTRUMENT_OR_INTERVAL")
        allowed_type = {"^GSPC": "INDEX", "^VIX": "INDEX", "SPY": "ETF", "ES=F": "FUTURE"}[symbol]
        if meta["instrumentType"] != allowed_type:
            raise ContractError("WRONG_PROVIDER_INSTRUMENT_TYPE")
        stamps = result["timestamp"]
        quotes = result["indicators"]["quote"][0]
        if any(len(v) != len(stamps) for v in quotes.values()):
            raise ContractError("QUOTE_ARRAY_LENGTH_MISMATCH")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ContractError("INVALID_CHART_SCHEMA_OR_CONTENT") from exc
    return result, meta, stamps, quotes, sha, retrieved, url


def yahoo_daily_sp500(path, provenance, calendar):
    result, meta, stamps, quote, sha, retrieved, url = yahoo_chart(path, provenance, "^GSPC", "1d")
    rows, rejected = [], []
    for i, stamp in enumerate(stamps):
        raw_time = datetime.fromtimestamp(stamp, UTC)
        day = raw_time.astimezone(NY).date()
        session = calendar.sessions.get(day)
        if session is None:
            rejected.append({"row": i, "session_date": day.isoformat(), "reason": "NONTRADING_DATE"})
            continue
        if timestamp(session.close) > retrieved:
            rejected.append({"row": i, "session_date": day.isoformat(), "reason": "CURRENT_UNCOMPLETED_SESSION"})
            continue
        rows.append(Observation(f"yahoo-sp500-{day}-{i}", SP500, "Yahoo Finance", quote["close"][i], INDEX_POINTS,
                                timestamp(session.close), None, retrieved, "DIRECT_CAPTURE", "CASH_OFFICIAL_CLOSE_PROXY",
                                meta.get("exchangeDataDelayedBy"), url, Qualification.EXPLORATORY_ONLY, day,
                                availability_evidence_id="sha256:"+sha, raw_sha256=sha,
                                observation_semantics="CALENDAR_CLOSE_EVENT_NOT_PROVIDER_PUBLICATION",
                                extra={"provider_bar_time_utc": raw_time.isoformat(), "source_row": i,
                                       "close_finality": "HISTORICAL_PROVIDER_ROW_NOT_CERTIFIED_OFFICIAL"}))
    return rows, {"rejected_rows": rejected, "meta": meta, "total_raw_rows": len(stamps)}


def fred_csv(path, provenance, series, calendar=None):
    if series == "SP500" and calendar is None:
        raise ContractError("CASH_INDEX_CALENDAR_REQUIRED")
    raw, sha, retrieved, url = _base(path, provenance)
    text = raw.decode("utf-8-sig")
    if text.lstrip().startswith("<"):
        raise ContractError("HTML_NOT_CSV")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or series not in reader.fieldnames:
        raise ContractError("INVALID_FRED_HEADER")
    date_field = "observation_date" if "observation_date" in reader.fieldnames else "DATE"
    rows, missing = [], []
    for i, row in enumerate(reader):
        day = date.fromisoformat(row[date_field])
        if row[series] in ("", "."):
            missing.append(day.isoformat())
            continue
        price = series == "SP500"
        observed = calendar.sessions[day].close if price and day in calendar.sessions else None
        rows.append(Observation(f"fred-{series}-{day}-{i}", SP500 if price else series, "FRED", float(row[series]),
                                INDEX_POINTS if price else "PERCENT_PER_ANNUM", observed, None, retrieved,
                                "DIRECT_CAPTURE", "DAILY_CLOSE_PROXY" if price else "MACRO_RELEASE_TIME_UNVERIFIED", None,
                                url, Qualification.EXPLORATORY_ONLY, day, availability_evidence_id="sha256:"+sha,
                                raw_sha256=sha, observation_semantics="DATE_ONLY_PLUS_CALENDAR_EVENT_IF_SP500"))
    return rows, {"missing_provider_dates": missing}


def cboe_vix_csv(path, provenance):
    raw, sha, retrieved, url = _base(path, provenance)
    text = raw.decode("utf-8-sig")
    if text.lstrip().startswith("<"):
        raise ContractError("HTML_NOT_CSV")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"DATE", "CLOSE"} <= set(reader.fieldnames):
        raise ContractError("INVALID_CBOE_HEADER")
    rows = []
    for i, row in enumerate(reader):
        day = datetime.strptime(row["DATE"], "%m/%d/%Y").date()
        rows.append(Observation(f"cboe-vix-{day}-{i}", "VIX_SPOT", "Cboe", float(row["CLOSE"]), "VIX_POINTS",
                                None, None, retrieved, "DIRECT_CAPTURE", "CBOE_DAILY_CLOSE_TIME_UNVERIFIED", None,
                                url, Qualification.EXPLORATORY_ONLY, day, availability_evidence_id="sha256:"+sha,
                                raw_sha256=sha, observation_semantics="SOURCE_DATE_ONLY_NO_FABRICATED_INTRADAY_CLOCK"))
    return rows


def yahoo_intraday(path, provenance, symbol, *, documented_delay_seconds=None, delay_evidence_id=None):
    result, meta, stamps, quote, sha, retrieved, url = yahoo_chart(path, provenance, symbol, "5m")
    unit, identity = ("FUTURES_INDEX_POINTS", "ES_CONTINUOUS_FUTURE") if symbol == "ES=F" else ("USD_PER_SHARE", "SPY_ETF")
    api_delay = meta.get("exchangeDataDelayedBy")
    delay = api_delay if api_delay is not None else documented_delay_seconds
    rows = []
    for i, stamp in enumerate(stamps):
        start = datetime.fromtimestamp(stamp, UTC)
        end = start+timedelta(minutes=5)
        local = start.astimezone(NY)
        minute = local.hour*60+local.minute
        session = "GLOBEX_SESSION_UNVERIFIED" if symbol == "ES=F" else "PREMARKET" if 240 <= minute < 570 else "RTH" if 570 <= minute < 960 else "POSTMARKET_OR_OVERNIGHT"
        rows.append(Observation(f"yahoo-{symbol}-{stamp}-{i}", identity, "Yahoo Finance", quote["close"][i], unit,
                                end, None, retrieved, "DIRECT_CAPTURE", session, delay, url,
                                Qualification.EXPLORATORY_ONLY, local.date(), availability_evidence_id="sha256:"+sha,
                                raw_sha256=sha, observation_semantics="FIVE_MINUTE_BAR_END_DERIVED_FROM_START_NOT_QUOTE_TIME",
                                extra={"interval_start_utc": start.isoformat(), "interval_seconds": 300,
                                       "completed_at_retrieval": end <= retrieved, "bar_aligned": stamp % 300 == 0,
                                       "api_reported_delay": api_delay, "documented_delay_seconds": documented_delay_seconds,
                                       "delay_evidence_id": delay_evidence_id,
                                       "delay_conflict": api_delay is not None and documented_delay_seconds is not None and api_delay != documented_delay_seconds,
                                       "session_verified": False, "is_bid_ask_quote": False,
                                       "roll_adjustment_policy": "UNKNOWN" if symbol == "ES=F" else "NOT_APPLICABLE"}))
    return rows, meta
