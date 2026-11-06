"""Independent reconstruction. This module does not import the prediction engine."""
import csv
from datetime import datetime, timedelta, time, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
import math
from statistics import median
from zoneinfo import ZoneInfo

from .contracts import (CAL, CONFLICT_DAY, FORMAL_ORIGIN, FORMAL_TARGET, HERE, MARKET, UNIT,
                        REFERENCE, TOLERANCE, require, utc)

NY = ZoneInfo("America/New_York")


def finite_positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def as_bound(day):
    next_day = datetime.fromisoformat(day).date() + timedelta(days=1)
    return datetime.combine(next_day, time(6, 30), NY).astimezone(timezone.utc)


def core_factors(prices):
    with localcontext() as ctx:
        ctx.prec = 45
        xs = [Decimal(str(v)) for v in prices[-21:]]
        changes = [(b/a).ln() for a, b in zip(xs, xs[1:])]
        mean = sum(changes)/20
        vol = (Decimal(252)*sum((x-mean)**2 for x in changes)/19).sqrt()
        return {"mom20": float((xs[-1]/xs[0]).ln()), "rv20": float(vol)}


def close_enough(a, b, error, tol=TOLERANCE):
    require(math.isfinite(a) and math.isfinite(b) and abs(a-b) <= tol, error)


def verify(snapshot, result):
    require(snapshot["actual_target"] is None and result["actual_target"] is None, "KNOWN_CURRENT_TARGET")
    keys = ("market", "unit", "mode", "model_id", "origin", "cutoff", "target", "baseline_date", "baseline_time")
    require(all(snapshot[k] == result[k] for k in keys), "FORECAST_RECORD_CONTRACT_MISMATCH")
    require((result["market"], result["unit"]) == (MARKET, UNIT), "WRONG_ASSET_OR_UNIT")
    require(snapshot["reference_definition"] == result["reference_definition"] == REFERENCE,
            "WRONG_TARGET_REFERENCE")
    source = snapshot["source"]
    require(source["source_id"] == "Bloomberg/SPX Index/PX_LAST"
            and source["provider"] == "GROUP_BLOOMBERG_SPX_EXPORT"
            and snapshot["raw_field"] == "PX_LAST" and source["market"] == MARKET
            and source["unit"] == UNIT, "SOURCE_IDENTITY_MISMATCH")
    origin = utc(snapshot["origin"])
    require(origin == utc(snapshot["cutoff"]), "WRONG_CUTOFF")
    mode = snapshot["mode"]
    if mode == "FORMAL":
        require(origin == utc(FORMAL_ORIGIN) and utc(snapshot["target"]) == utc(FORMAL_TARGET),
                "FORMAL_TIME_CONTRACT_MISMATCH")
        ref = snapshot["source_reference"]
        require(snapshot["qualification"] == "ASSUMED_AVAILABLE_WITH_EVIDENCE"
                and ref["provider"] in {"FRED_SP500", "YAHOO_GSPC"}
                and (ref["provider"] != "YAHOO_GSPC" or ref.get("downgrade_reason"))
                and ref["audit"][ref["provider"]]["latest_session"] == snapshot["baseline_date"],
                "FORMAL_REFERENCE_NOT_QUALIFIED")
        close_enough(ref["value"], result["baseline"], "FORMAL_REFERENCE_MISMATCH", .01)
        require(utc(ref["capture"]["retrieved_at_utc"]) < origin, "REFERENCE_AFTER_CUTOFF")
    else:
        require(mode == "REHEARSAL" and origin.year < 2026, "INVALID_REHEARSAL")
    require(utc(snapshot["availability_upper_bound"]) < origin, "FEATURES_NOT_AVAILABLE")
    with (CAL / "calendar_xnys_2016_2026.csv").open(newline="") as stream:
        sessions = {r["session_date"]: utc(r["close_time_utc"]) for r in csv.DictReader(stream)}
    base_date = max(k for k, t in sessions.items() if t < origin)
    require(base_date == result["baseline_date"] and sessions[base_date] == utc(result["baseline_time"]),
            "STALE_BASELINE_IN_VERIFIER")
    target_bound = datetime.combine(origin.astimezone(NY).date()+timedelta(days=7), time(16), NY)
    require(utc(result["target"]) == max(t for t in sessions.values() if t <= target_bound),
            "WRONG_NEXT_WEEK_CLOSE")
    prices = snapshot["prices"]
    ordered = sorted(prices)
    require(ordered[-1] == base_date and set(d for d in prices if d >= "2016-01-01") ==
            {d for d, t in sessions.items() if t <= sessions[base_date]}, "INCOMPLETE_OR_FUTURE_INPUT")
    require(all(finite_positive(v) for v in prices.values()), "NONFINITE_SOURCE_PRICE")
    close_enough(prices[base_date], result["baseline"], "BASELINE_LEVEL_MISMATCH", 0.)
    positions = {d: i for i, d in enumerate(ordered)}
    with (HERE / "matched_origins.csv").open(newline="") as stream:
        cohort = list(csv.DictReader(stream))
    expected, labels, gross = [], [], []
    for r in cohort:
        b, t = r["baseline_date"], r["target_date"]
        if (r["eligible"] != "True" or utc(r["forecast_origin"]) >= origin
                or as_bound(t) >= origin or sessions[t] > sessions[base_date]):
            continue
        require(sessions[b] == utc(r["baseline_time"]) and sessions[t] == utc(r["target_time"])
                and as_bound(b) <= utc(r["forecast_origin"]), "TRAINING_CLOCK_INCONSISTENT")
        used = ordered[positions[b]-62:positions[b]+1]
        require(len(used) == 63 and CONFLICT_DAY not in used and t != CONFLICT_DAY,
                "TRAINING_CONFLICT_OR_MISSING_LOOKBACK")
        expected.append(r["sample_id"])
        labels.append(as_bound(t))
        gross.append(prices[t]/prices[b])
    fit = result["fit"]
    require(result["training_ids"] == expected and len(expected) >= 104
            and len(expected) == fit["training_count"]
            and utc(result["max_training_label_time"]) == max(labels),
            "TRAINING_LINEAGE_OR_MATURITY_MISMATCH")
    checked_core = {}
    if result["model_id"] == "HISTORICAL_MEDIAN":
        logret = median(map(math.log, gross))
        close_enough(logret, fit["log_return"], "MEDIAN_RETURN_MISMATCH", 1e-12)
        point = prices[base_date] * math.exp(logret)
    elif result["model_id"] == "CORE_QUANTILE":
        names = ["mom20", "rv20"]
        require(fit["features"] == names and
                len(fit["scale"]) == len(fit["center"]) == len(fit["coefficients"]) == 2 and
                all(finite_positive(s) for s in fit["scale"]), "CORE_QUANTILE_PARAMETER_CONTRACT")
        checked_core = core_factors([prices[d] for d in ordered[-21:]])
        for k, v in checked_core.items():
            close_enough(result["feature_values"][k], v, "CORE_FACTOR_MISMATCH", 1e-10)
        predicted = fit["intercept"] + math.fsum(c*(checked_core[k]-m)/s
            for k, c, m, s in zip(names, fit["coefficients"], fit["center"], fit["scale"]))
        close_enough(predicted, fit["log_return_percent"], "CORE_QUANTILE_RETURN_MISMATCH", 1e-10)
        point = prices[base_date] * math.exp(predicted/100)
    else:
        require(result["model_id"] == "BLEND_VOL_LAD" and fit["component_weights"] == [.5, .5],
                "MODEL_OR_WEIGHTS_MISMATCH")
        names = fit["features"]
        require(len(names) == 5 and len(set(names)) == 5 and names[:2] == ["mom20", "rv20"]
                and fit["training_ids"] == expected and fit["current_feature_values"] == result["feature_values"],
                "FACTOR_OR_TRAINING_LINEAGE_MISMATCH")
        checked_core = core_factors([prices[d] for d in ordered[-21:]])
        for k, v in checked_core.items():
            close_enough(result["feature_values"][k], v, "CORE_FACTOR_MISMATCH", 1e-10)
        component_points = []
        for name in ("huber", "lad"):
            m = fit[name]
            centers, scales = m["scaler"]["center"], m["scaler"]["scale"]
            require(m["features"] == names and len(centers) == len(scales) == len(m["coefficients"]) == 5
                    and all(finite_positive(s) for s in scales), "PARAMETER_CONTRACT")
            value = m["intercept"] + math.fsum(
                coef*(result["feature_values"][key]-center)/scale
                for coef, key, center, scale in zip(m["coefficients"], names, centers, scales))
            if name == "lad":
                sigma = max(.25, 100*checked_core["rv20"]*math.sqrt(6/252))
                close_enough(sigma, m["current_scale_percent"], "LAD_VOLATILITY_SCALE_MISMATCH", 1e-10)
                value *= sigma
            close_enough(value, m["prediction_simple_percent"], "COMPONENT_RETURN_MISMATCH", 1e-10)
            component_points.append(prices[base_date]*(1+value/100))
            close_enough(component_points[-1], m["forecast"], "COMPONENT_POINT_MISMATCH")
        point = sum(component_points)/2
    close_enough(point, result["point"], "FORECAST_POINT_MISMATCH")
    close_enough(point/prices[base_date]-1, result["expected_simple_return"], "RETURN_MISMATCH", 1e-12)
    require(finite_positive(point), "NONPOSITIVE_INDEPENDENT_POINT")
    independently_rounded = str(Decimal(str(point)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
    require(independently_rounded == result["point_2dp"], "ROUNDING_MISMATCH")
    return {"pass": True, "independent_point": point, "point_2dp": result["point_2dp"],
            "tolerance_points": TOLERANCE, "absolute_difference_points": abs(point-result["point"]),
            "verified_core_factors": checked_core, "verified_training_rows": len(expected),
            "verified_baseline_date": base_date, "source_qualification": snapshot["qualification"],
            "separation": "Independent stdlib calendar/label checks and scalar Decimal/core/return reconstruction; full refit checked by offline replay"}
