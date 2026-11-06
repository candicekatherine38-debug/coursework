from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math
import statistics

from .contracts import INDEX_POINTS, SP500, ContractError, timestamp


def number(value):
    if isinstance(value, bool):
        raise ContractError("BOOLEAN_NOT_FORECAST")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ContractError("INVALID_NUMERIC_VALUE") from exc
    if not result.is_finite() or result <= 0:
        raise ContractError("NONFINITE_OR_NONPOSITIVE_LEVEL")
    return result


def score(forecast, actual):
    f, a = number(forecast), number(actual)
    error = f-a
    ape = Decimal(100)*abs(error)/a
    s = max(Decimal(0), Decimal(50)-5*ape)
    return {"forecast": f, "actual": a, "error": error, "absolute_error": abs(error),
            "absolute_percentage_error": ape, "accuracy_score": s}


def display_score(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def score_prediction(prediction, window, *, data_snapshot_id, training_cutoff, regime="UNCLASSIFIED", exploratory=False):
    if prediction.instrument_id != SP500 or prediction.unit != INDEX_POINTS:
        raise ContractError("PREDICTION_NOT_SP500_INDEX_POINTS")
    if prediction.data_snapshot_id != data_snapshot_id:
        raise ContractError("PREDICTION_SNAPSHOT_MISMATCH")
    if timestamp(training_cutoff) >= window.forecast_origin:
        raise ContractError("TRAINING_CUTOFF_NOT_BEFORE_ORIGIN")
    if not window.qualified and not exploratory:
        raise ContractError("UNQUALIFIED_WINDOW_CANNOT_RECEIVE_FORMAL_SCORE")
    if exploratory and not window.exploratory_eligible:
        raise ContractError("INCOMPLETE_EXPLORATORY_WINDOW")
    if window.target is None:
        raise ContractError("MISSING_TARGET")
    if window.target.issues(expected_instrument=SP500, expected_unit=INDEX_POINTS):
        raise ContractError("INVALID_TARGET_INSTRUMENT_OR_UNIT")
    if not exploratory and not window.target.reference_verified:
        raise ContractError("TARGET_REFERENCE_UNVERIFIED")
    values = score(prediction.value, window.target.value)
    return {**{k: float(v) for k, v in values.items()}, "model_identifier": prediction.model_identifier,
            "data_snapshot_id": data_snapshot_id, "training_cutoff": timestamp(training_cutoff).isoformat(),
            "forecast_origin": window.forecast_origin.isoformat(), "target_session": window.target_session.isoformat(),
            "unit": INDEX_POINTS, "instrument_id": SP500, "regime": regime,
            "cohort": window.cohort, "evaluation_status": "EXPLORATORY_NOT_POINT_IN_TIME_VALIDATED" if exploratory else "QUALIFIED",
            "rounding": "INTERNAL_VALUES_UNROUNDED_SCORE_DISPLAY_ONLY_TWO_DECIMALS"}


def summarise(records):
    if not records:
        return {"count": 0, "mean_accuracy_score": None, "reason": "NO_ELIGIBLE_SCORES"}
    if len({(r["forecast_origin"], r["target_session"]) for r in records}) != len(records):
        raise ContractError("DUPLICATE_SCORED_WINDOW")
    def subset(rows):
        return {"count": len(rows), "mean_accuracy_score": statistics.mean(r["accuracy_score"] for r in rows),
                "median_accuracy_score": statistics.median(r["accuracy_score"] for r in rows),
                "MAPE": statistics.mean(r["absolute_percentage_error"] for r in rows),
                "MAE_index_points": statistics.mean(r["absolute_error"] for r in rows)}
    regimes = sorted({r["regime"] for r in records})
    return {**subset(records), "worst_errors": sorted(records, key=lambda r: r["absolute_percentage_error"], reverse=True)[:5],
            "regime_specific_performance": {regime: subset([r for r in records if r["regime"] == regime]) for regime in regimes},
            "evaluation_statuses": sorted({r["evaluation_status"] for r in records})}


def paired_improvement(candidate, benchmark):
    def keyed(rows):
        keys = [(r["forecast_origin"], r["target_session"]) for r in rows]
        if len(keys) != len(set(keys)):
            raise ContractError("DUPLICATE_COMPARISON_WINDOW")
        return dict(zip(keys, rows))
    a, b = keyed(candidate), keyed(benchmark)
    if not a or a.keys() != b.keys():
        raise ContractError("MODEL_WINDOWS_NOT_IDENTICAL")
    deltas = []
    for key in sorted(a):
        x, y = a[key], b[key]
        if any(x[k] != y[k] for k in ("actual", "data_snapshot_id", "evaluation_status", "unit", "instrument_id")):
            raise ContractError("COMPARISON_TARGET_SNAPSHOT_OR_STATUS_MISMATCH")
        deltas.append({"forecast_origin": key[0], "score_improvement": x["accuracy_score"]-y["accuracy_score"],
                       "ape_reduction": y["absolute_percentage_error"]-x["absolute_percentage_error"]})
    return {"count": len(deltas), "mean_score_improvement": statistics.mean(r["score_improvement"] for r in deltas),
            "per_window": deltas}
