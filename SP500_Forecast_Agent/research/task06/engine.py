"""Adapt the original fixed calculation to a snapshot without a current outcome."""
import csv
from datetime import datetime, time, timedelta
import math
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
from sp500_agent.calendar import NY

from .contracts import (CONFLICT_DAY, FORMAL_CANDIDATES, FORMAL_ORIGIN, FORMAL_TARGET, HERE, MARKET, MODELS, REFERENCE, UNIT,
                        WORKBOOK_HASH, check_clock, code_hashes, now, read, require, rounded, sha,
                        utc, versions)
from .legacy import data as legacy_data, features, model
from .sources import RIGHTS, TOLERANCE_POINTS, calendar, parsed_capture


def proposal(primary):
    require(primary in FORMAL_CANDIDATES, "UNKNOWN_MODEL")
    return {"status": "PROPOSED_NOT_LOCKED", "primary": primary,
            "fallback": next(x for x in FORMAL_CANDIDATES if x != primary),
            "model_contracts": read(HERE / "candidate_contracts.json"),
            "code_sha256": code_hashes(), "dependencies": versions()}


def dates(origin, mode):
    local = utc(origin).astimezone(NY)
    require(local.weekday() == 4 and local.time() == time(7), "ORIGIN_MUST_BE_FRIDAY_0700_NY")
    if mode == "REHEARSAL":
        require(local.year < 2026, "NO_NEW_2026_RESEARCH_ORIGINS")
    else:
        require(mode == "FORMAL" and utc(origin) == utc(FORMAL_ORIGIN), "FORMAL_ORIGIN_MISMATCH")
    cal = calendar()
    base = cal.latest_close(utc(origin))
    target = cal.latest_close(datetime.combine(local.date() + timedelta(days=7), time(16), NY))
    if mode == "FORMAL":
        require(target.close == utc(FORMAL_TARGET), "FORMAL_TARGET_MISMATCH")
    return cal, base, target


def validate_prices(prices, cal, base):
    require(isinstance(prices, dict) and bool(prices), "EMPTY_PRICE_HISTORY")
    require(all(isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v > 0 for v in prices.values()), "INVALID_PRICE_HISTORY")
    require(max(prices) <= base.day.isoformat(), "POST_BASELINE_INPUT")
    require(max(prices) == base.day.isoformat(), "STALE_BASELINE_EXPECTED_" + base.day.isoformat())
    expected = {d.isoformat() for d in cal.sessions if d <= base.day}
    actual = {d for d in prices if d >= cal.start.isoformat()}
    require(expected == actual, "MISSING_OR_NONTRADING_SESSIONS_" + str(sorted(expected ^ actual)[:5]))


def load_cohort():
    with (HERE / "matched_origins.csv").open(newline="") as f:
        return list(csv.DictReader(f))


def prepare(directory, origin, mode, selected, plan, data_approval=None):
    cal, base, target = dates(origin, mode)
    wb = directory / "workbook.xlsx"
    capture = read(directory / "workbook_capture.json")
    require(capture["sha256"] == sha(wb), "WORKBOOK_HASH_CHANGED")
    require((capture["provider"], capture["source_id"], capture["market"], capture["unit"], capture["timezone"])
            == ("GROUP_BLOOMBERG_SPX_EXPORT", "Bloomberg/SPX Index/PX_LAST", MARKET, UNIT,
                "America/New_York"), "WORKBOOK_CAPTURE_IDENTITY")
    require(selected in MODELS and plan["code_sha256"] == code_hashes()
            and plan["dependencies"] == versions(), "MODEL_CODE_OR_RUNTIME_CHANGED")
    formal = mode == "FORMAL"
    clock = now()
    if formal:
        require(data_approval is not None, "SIGNED_DATA_REVIEW_REQUIRED")
        check_clock(capture["requested_at_utc"], capture["retrieved_at_utc"], clock)
        check_clock(base.close.isoformat(), capture["requested_at_utc"], origin)
        require(base.close <= utc(data_approval["workbook_exported_at_utc"])
                <= utc(capture["requested_at_utc"]), "WORKBOOK_EXPORT_NOT_AFTER_COMPLETE_CLOSE")
    else:
        require(sha(wb) == WORKBOOK_HASH, "REHEARSAL_REQUIRES_ARCHIVED_GROUP_WORKBOOK")
    raw, audit = legacy_data.read_workbook(wb, max_date=base.day if formal else datetime(2026, 10, 30).date())
    prices = {r["date"]: r["PX_LAST"] for r in raw if r["date"] <= base.day.isoformat()}
    validate_prices(prices, cal, base)
    qualification = "EXPLORATORY_CURRENT_VINTAGE_REHEARSAL"
    reference = {}
    if formal:
        reference = confirm_reference(directory, origin, base, prices, capture, data_approval, cal, clock)
        qualification = data_approval["history_qualification"]
    snapshot = {
        "mode": mode, "market": MARKET, "unit": UNIT, "model_id": selected,
        "origin": utc(origin).isoformat(), "cutoff": utc(origin).isoformat(),
        "target": target.close.isoformat(), "baseline_date": base.day.isoformat(),
        "reference_definition": REFERENCE,
        "baseline_time": base.close.isoformat(), "prices": prices,
        "source": capture, "availability_upper_bound": (capture["retrieved_at_utc"] if formal else
                                                      legacy_data.assumed_availability(base.day.isoformat())),
        "qualification": qualification, "source_reference": reference,
        "actual_target": None, "publication_at_utc": None,
        "source_rights": RIGHTS, "known_conflict": CONFLICT_DAY,
        "historic_timing_rule": "Next civil day 06:30 America/New_York assumption",
        "historic_retrieval_proves_original_availability": False,
        "calendar_sha256": plan["code_sha256"]["data/snapshots/calendar_xnys_2016_2026.csv"],
        "model_code_sha256": plan["code_sha256"], "dependencies": plan["dependencies"],
        "excluded_later_workbook_rows": len(raw) - len(prices),
        "first_price_date": min(prices), "last_price_date": max(prices),
        "raw_sheet": audit["sheet"], "raw_field": audit["price_field"],
    }
    return snapshot


def confirm_reference(directory, origin, base, prices, capture, approval, cal, clock):
    market = directory / "reference"
    require(approval["workbook_sha256"] == capture["sha256"]
            and approval["workbook_capture_sha256"] == sha(directory / "workbook_capture.json")
            and approval["reference_manifest_sha256"] == sha(market / "capture_manifest.json")
            and approval["qualification_evidence_sha256"] == sha(directory / "qualification_evidence")
            and approval["baseline_date"] == base.day.isoformat(),
            "DATA_REVIEW_DOES_NOT_BIND_CAPTURE")
    require(approval["history_qualification"] == "ASSUMED_AVAILABLE_WITH_EVIDENCE"
            and approval.get("approved_history_and_vintage_review") is True
            and approval.get("approved_calendar_review") is True
            and approval.get("approved_clock_review") is True
            and approval.get("approved_local_use") is True and approval.get("evidence_summary"),
            "HISTORY_CALENDAR_OR_RIGHTS_EVIDENCE_REQUIRED")
    require((directory / "qualification_evidence").stat().st_size > 0, "EMPTY_QUALIFICATION_EVIDENCE")
    require(utc(capture["retrieved_at_utc"]) <= utc(approval["approved_at"]) <= utc(clock),
            "DATA_REVIEW_OUTSIDE_ACTUAL_TIMELINE")
    day = base.day.isoformat()
    audits = {}
    accepted = {}
    captures = read(market / "capture_manifest.json")
    require(len({r["provider"] for r in captures}) == len(captures), "DUPLICATE_REFERENCE_PROVIDER")
    for record in captures:
        key = record["provider"]
        if key not in ("FRED_SP500", "YAHOO_GSPC"):
            continue
        try:
            check_clock(record["requested_at_utc"], record["retrieved_at_utc"], origin)
            require(utc(record["retrieved_at_utc"]) <= utc(approval["approved_at"]), "CAPTURE_AFTER_DATA_REVIEW")
            values, diagnostics = parsed_capture(market, record, cal)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            audits[key] = {"status": "UNAVAILABLE_OR_STALE", "reason": str(exc)}
            continue
        # A mismatch is a stop, never evidence that a fallback may be substituted.
        common = set(values) & set(sorted(prices)[-63:])
        bad = [d for d in sorted(common) if abs(values[d]["value"] - prices[d]) > TOLERANCE_POINTS]
        require(not bad, "REFERENCE_CONFLICT_" + ",".join(bad))
        audits[key] = {**diagnostics, "comparison_sessions": len(common)}
        if max(values) != day:
            audits[key].update(status="UNAVAILABLE_OR_STALE", reason="REFERENCE_STALE_OR_WRONG_SESSION")
            continue
        accepted[key] = {"value": values[day]["value"], "capture": record}
    chosen = approval["reference_provider"]
    preferred = "FRED_SP500" if "FRED_SP500" in accepted else "YAHOO_GSPC"
    require(chosen == preferred and chosen in accepted, "NO_APPROVED_FRESH_REFERENCE")
    if chosen == "YAHOO_GSPC":
        require("FRED_SP500" in audits and approval.get("downgrade_reason"), "SIGNED_FRED_DOWNGRADE_REQUIRED")
    return {"provider": chosen, **accepted[chosen], "audit": audits,
            "downgrade_reason": approval.get("downgrade_reason"),
            "qualification_evidence_sha256": approval["qualification_evidence_sha256"]}


def build_rows(snapshot):
    cal, base, target = dates(snapshot["origin"], snapshot["mode"])
    prices = snapshot["prices"]
    validate_prices(prices, cal, base)
    ordered = sorted(prices)
    positions = {day: i for i, day in enumerate(ordered)}
    if snapshot["model_id"] != "HISTORICAL_MEDIAN":
        frame = features.generate(pd.Series({d: prices[d] for d in ordered}, dtype=float))
    else:
        frame = None
    rows = []
    for item in load_cohort():
        if item["eligible"] != "True":
            continue
        b, t = item["baseline_date"], item["target_date"]
        label = legacy_data.assumed_availability(t)
        if (utc(item["forecast_origin"]) >= utc(snapshot["origin"]) or
                utc(label) >= utc(snapshot["origin"]) or utc(item["target_time"]) > base.close):
            continue
        require(b in prices and t in prices, "MISSING_MATURE_TRAINING_LEVEL")
        require(cal.sessions[datetime.fromisoformat(b).date()].close == utc(item["baseline_time"])
                and cal.sessions[datetime.fromisoformat(t).date()].close == utc(item["target_time"]),
                "TRAINING_CALENDAR_MISMATCH")
        segment = ordered[positions[b]-62:positions[b]+1]
        require(len(segment) == 63 and segment[0] >= cal.start.isoformat(), "HISTORICAL_FEATURE_WARMUP")
        require(CONFLICT_DAY not in segment and t != CONFLICT_DAY, "QUARANTINED_2020_CONFLICT")
        rows.append({**item, "baseline_index_level": prices[b], "target_index_level": prices[t],
                     "y_log_return": math.log(prices[t] / prices[b]),
                     "feature_assumed_available_time": legacy_data.assumed_availability(b),
                     "label_assumed_available_time": label,
                     "historical_feature_values": frame.loc[b].to_dict() if frame is not None else {}})
    require(len(rows) >= 104, "INSUFFICIENT_MATURE_TRAINING")
    day = base.day.isoformat()
    require(CONFLICT_DAY not in ordered[-63:], "QUARANTINED_CURRENT_FEATURE_WINDOW")
    current = {"sample_id": snapshot["origin"], "forecast_origin": snapshot["origin"],
               "baseline_date": day, "baseline_time": base.close.isoformat(),
               "baseline_index_level": prices[day], "target_time": target.close.isoformat(),
               "feature_assumed_available_time": snapshot["availability_upper_bound"],
               "historical_feature_values": frame.loc[day].to_dict() if frame is not None else {}}
    return rows, current


def calculate(snapshot):
    require((snapshot["market"], snapshot["unit"], snapshot["actual_target"]) == (MARKET, UNIT, None),
            "INVALID_FORECAST_CONTRACT_OR_KNOWN_TARGET")
    require(snapshot["model_id"] in MODELS and snapshot["model_code_sha256"] == code_hashes()
            and snapshot["dependencies"] == versions(), "MODEL_CODE_OR_RUNTIME_CHANGED")
    rows, current = build_rows(snapshot)
    with threadpool_limits(limits=1):
        if snapshot["model_id"] == "BLEND_VOL_LAD":
            point, fit = model.predict(rows, current)
        elif snapshot["model_id"] == "CORE_QUANTILE":
            point, fit = fit_core_quantile(rows, current)
        else:
            model._validate(rows, current)
            g = np.array([r["target_index_level"] / r["baseline_index_level"] for r in rows])
            median = float(np.median(np.log(g)))
            point = current["baseline_index_level"] * float(np.exp(median))
            fit = {"log_return": median, "gross_estimate": float(np.exp(median)),
                   "features": [], "training_count": len(rows)}
    return {"mode": snapshot["mode"], "model_id": snapshot["model_id"],
            "market": MARKET, "unit": UNIT, "origin": snapshot["origin"],
            "cutoff": snapshot["cutoff"], "target": current["target_time"],
            "reference_definition": REFERENCE,
            "baseline_date": current["baseline_date"], "baseline_time": current["baseline_time"],
            "baseline": current["baseline_index_level"], "point": point, "point_2dp": rounded(point),
            "expected_simple_return": point/current["baseline_index_level"] - 1,
            "feature_values": {n: current["historical_feature_values"][n] for n in fit["features"]},
            "training_ids": [r["sample_id"] for r in rows],
            "max_training_label_time": max(r["label_assumed_available_time"] for r in rows),
            "fit": fit, "actual_target": None,
            "mapping": read(HERE / "candidate_contracts.json")["candidates"][snapshot["model_id"]]["mapping"]}


def fit_core_quantile(rows, current):
    """Original TASK03 FOCUS_10_QUANTILE estimator, scaler, target and mapping."""
    from sklearn.linear_model import QuantileRegressor
    from sklearn.preprocessing import StandardScaler

    model._validate(rows, current)
    names = list(features.CORE)
    x = np.array([[r["historical_feature_values"][n] for n in names] for r in rows])
    z = np.array([[current["historical_feature_values"][n] for n in names]])
    g = np.array([r["target_index_level"] / r["baseline_index_level"] for r in rows])
    scaler = StandardScaler().fit(x, sample_weight=np.ones(len(rows)))
    estimator = QuantileRegressor(alpha=.003, quantile=.5, solver="highs", solver_options={"threads": 1})
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        estimator.fit(scaler.transform(x), 100*np.log(g), sample_weight=np.ones(len(rows)))
        predicted = float(estimator.predict(scaler.transform(z))[0])
    point = current["baseline_index_level"] * math.exp(predicted/100)
    return point, {"features": names, "coefficients": estimator.coef_.tolist(),
                   "intercept": float(estimator.intercept_), "log_return_percent": predicted,
                   "center": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
                   "training_count": len(rows), "warnings": [str(w.message) for w in recorded]}
