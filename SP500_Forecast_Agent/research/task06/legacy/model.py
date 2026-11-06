"""Fixed 50/50 Huber + volatility-normalized weighted LAD point forecast.

The portability work changes software packaging, not the registered statistical
specification. Fitting and supervised factor screening use mature training rows
only. Current target prices are never read by predict(). Every fitted parameter
needed to reconstruct each component point is returned in plain JSON data.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import warnings

import numpy as np
import pandas as pd
from scipy.optimize import linprog, minimize
from scipy.sparse import csc_matrix, eye, hstack

from .features import CORE, POOL

CONFIG = {
    "model_id": "BLEND_VOL_LAD", "component_weights": [0.5, 0.5],
    "components": ["HUBER_SELECT3", "VS_LAD_PRICE3"],
    "minimum_mature_weekly_training_rows": 104,
    "training": "expanding mature weekly history; no recency weighting",
    "core_features": CORE, "selected_price_factors": 3,
    "candidate_count": 134, "screen": "absolute training Spearman vs log return",
    "screen_tie_break": "factor name ascending", "redundancy_threshold": 0.95,
    "huber": {"alpha": .01, "epsilon": 1.35, "max_iter": 500, "gtol": 1e-7},
    "lad": {"alpha": .01, "quantile": .5, "scale_sessions": 6,
            "scale_floor_percent": .25},
    "target": "simple return percent", "forecast_mapping": "baseline*(1+return_percent/100)",
    "outcome_use": "current outcome never read",
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _finite(value, label):
    if not np.isfinite(value).all():
        raise ValueError("NONFINITE_" + label)


def _validate(rows, current):
    if len(rows) < CONFIG["minimum_mature_weekly_training_rows"]:
        raise ValueError("INSUFFICIENT_MATURE_TRAINING")
    origin = _time(current["forecast_origin"])
    baseline_time = _time(current["baseline_time"])
    if baseline_time >= origin or _time(current["feature_assumed_available_time"]) > origin:
        raise ValueError("CURRENT_FEATURE_OR_BASELINE_NOT_AVAILABLE")
    ids = [r["sample_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("DUPLICATE_TRAINING_ORIGIN")
    for row in rows:
        past_origin = _time(row["forecast_origin"])
        if not past_origin < origin or not _time(row["label_assumed_available_time"]) < origin:
            raise ValueError("TRAINING_LABEL_NOT_MATURE")
        if _time(row["target_time"]) > baseline_time:
            raise ValueError("TRAINING_INTERVAL_OVERLAPS_CURRENT_INTERVAL")
        if _time(row["feature_assumed_available_time"]) > past_origin:
            raise ValueError("TRAINING_FEATURE_AFTER_ORIGIN")
        if not (math.isfinite(float(row["target_index_level"])) and row["target_index_level"] > 0
                and math.isfinite(float(row["baseline_index_level"])) and row["baseline_index_level"] > 0):
            raise ValueError("INVALID_TRAINING_LEVEL")
    if not math.isfinite(float(current["baseline_index_level"])) or current["baseline_index_level"] <= 0:
        raise ValueError("INVALID_CURRENT_BASELINE")


def select_features(rows):
    """Training-only association ranking followed by ordered redundancy removal."""
    frame = pd.DataFrame([{n: r["historical_feature_values"][n] for n in CORE + POOL}
                          for r in rows])
    target = pd.Series([r["y_log_return"] for r in rows])
    usable = np.isfinite(frame).mean()
    candidates = [n for n in POOL if usable[n] >= .98 and frame[n].std() > 1e-12]
    scores = frame[candidates].corrwith(target, method="spearman").fillna(0).to_dict()
    ordered = sorted(candidates, key=lambda n: (-abs(scores[n]), n))
    correlation = frame[CORE + ordered].replace([np.inf, -np.inf], np.nan).corr(
        method="spearman", min_periods=10)
    kept, dropped = [], {}
    for name in correlation.columns:
        match = next((k for k in kept if pd.notna(correlation.loc[name, k])
                      and abs(float(correlation.loc[name, k])) >= .95), None)
        if match is None:
            kept.append(name)
        else:
            dropped[name] = match
    selected = [n for n in kept if n not in CORE][:3]
    if len(selected) != 3:
        raise ValueError("INSUFFICIENT_NONREDUNDANT_FEATURES")
    return CORE + selected, {"selected": selected, "scores": scores, "dropped": dropped,
                              "threshold": .95, "training_count": len(rows)}


def _standardize(x, weights=None):
    _finite(x, "DESIGN")
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    _finite(w, "SCALER_WEIGHTS")
    if np.any(w <= 0):
        raise ValueError("SCALER_WEIGHTS_NOT_POSITIVE")
    w = w / w.sum()
    center = np.einsum("i,ij->j", w, x, optimize=False)
    scale = np.sqrt(np.einsum("i,ij,ij->j", w, x-center, x-center, optimize=False))
    scale = np.where(scale > 1e-12, scale, 1.)
    scaled = (x-center)/scale
    _finite(scaled, "SCALED_DESIGN")
    return scaled, center, scale


def _huber(x, y):
    """Joint Huber location/scale objective and L2 penalty on slopes only."""
    n, width = x.shape
    calls = 0
    def loss_and_gradient(parameters):
        nonlocal calls
        calls += 1
        coef, intercept, scale = parameters[:width], parameters[width], parameters[width+1]
        residual = y - np.einsum("ij,j->i", x, coef, optimize=False) - intercept
        magnitude = np.abs(residual)
        inside = magnitude <= 1.35*scale
        psi = np.where(inside, residual/scale, 1.35*np.sign(residual))
        loss = n*scale + np.sum(np.where(inside, residual**2/scale,
                                         2*1.35*magnitude-1.35**2*scale)) + .01*np.sum(coef**2)
        gradient = np.r_[-2*np.einsum("i,ij->j", psi, x, optimize=False)+.02*coef,
                         -2*np.sum(psi), n-np.sum(np.where(inside, (residual/scale)**2, 1.35**2))]
        _finite(gradient, "HUBER_GRADIENT")
        if not math.isfinite(loss):
            raise ValueError("NONFINITE_HUBER_LOSS")
        return float(loss), gradient
    result = minimize(loss_and_gradient, np.r_[np.zeros(width+1), 1.], jac=True,
                      method="L-BFGS-B", bounds=[(None, None)]*(width+1)+[(1e-10, None)],
                      options={"maxiter": 500, "gtol": 1e-7})
    if not result.success:
        raise ValueError("HUBER_NOT_CONVERGED:"+str(result.message))
    _finite(result.x, "HUBER_COEFFICIENTS")
    return result.x[:width], float(result.x[width]), {
        "scale": float(result.x[width+1]), "iterations": int(result.nit),
        "objective": float(result.fun), "objective_evaluations": calls,
        "solver_message": str(result.message), "all_intermediates_finite": True}


def _lad(x, target, weights):
    """Sparse storage of the exact original weighted LAD linear program."""
    design = np.column_stack([np.ones(len(x)), x])
    n, width = design.shape
    normalized = weights / weights.sum()
    penalties = np.r_[0., np.full(width-1, .01)]
    objective = np.r_[penalties, penalties, .5*normalized, .5*normalized]
    equality = hstack([csc_matrix(design), csc_matrix(-design), eye(n, format="csc"),
                       -eye(n, format="csc")], format="csc")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        fit = linprog(objective, A_eq=equality, b_eq=target, bounds=(0, None),
                      method="highs", options={"threads": 1})
    if not fit.success:
        raise ValueError("LAD_NOT_CONVERGED:"+str(fit.message))
    coef = fit.x[:width] - fit.x[width:2*width]
    _finite(coef, "LAD_COEFFICIENTS")
    residual = target - np.einsum("ij,j->i", design, coef, optimize=False)
    check = .5*float(np.einsum("i,i->", normalized, np.abs(residual), optimize=False))
    check += .01*float(np.abs(coef[1:]).sum())
    if abs(check-float(fit.fun)) > 1e-7:
        raise ValueError("LAD_OBJECTIVE_RECONSTRUCTION_FAILED")
    return coef[1:], float(coef[0]), {
        "solver": "scipy.optimize.linprog/HiGHS", "objective": float(fit.fun),
        "independent_objective": check, "iterations": int(fit.nit),
        "solver_message": str(fit.message), "warnings": [str(w.message) for w in recorded]}


def predict(training_rows, current):
    """Return (forecast index points, JSON-compatible fitted evidence).

    Input rows use the project's weekly schema. Current target_index_level and
    y_log_return may be absent: their values cannot affect this prediction.
    """
    rows = list(training_rows)
    _validate(rows, current)
    names, screen = select_features(rows)
    x = np.asarray([[r["historical_feature_values"][n] for n in names] for r in rows], float)
    current_x = np.asarray([current["historical_feature_values"][n] for n in names], float)
    _finite(x, "TRAIN_FEATURES")
    _finite(current_x, "CURRENT_FEATURES")
    gross = np.asarray([r["target_index_level"]/r["baseline_index_level"] for r in rows], float)
    simple = 100.*(gross-1.)
    baseline = float(current["baseline_index_level"])
    scaled, center, scale = _standardize(x)
    coef, intercept, fit = _huber(scaled, simple)
    standardized_current = (current_x-center)/scale
    huber_return = intercept + math.fsum(float(a)*float(b) for a, b in zip(coef, standardized_current))
    huber_point = baseline*(1.+huber_return/100.)
    huber = {"features": names, "coefficients": coef.tolist(), "intercept": intercept,
             "scaler": {"center": center.tolist(), "scale": scale.tolist()},
             "current_standardized_features": standardized_current.tolist(),
             "prediction_simple_percent": huber_return, "forecast": huber_point,
             "fit": fit, "training_weight_rule": "UNIFORM"}
    annual = np.asarray([r["historical_feature_values"]["rv20"] for r in rows], float)
    current_annual = float(current["historical_feature_values"]["rv20"])
    if np.any(annual < 0) or current_annual < 0:
        raise ValueError("NEGATIVE_VOLATILITY")
    volatility = np.maximum(.25, 100.*math.sqrt(6./252.)*annual)
    current_volatility = max(.25, 100.*math.sqrt(6./252.)*current_annual)
    weights = volatility/gross
    scaled, center, scale = _standardize(x, weights)
    coef, intercept, fit = _lad(scaled, simple/volatility, weights)
    standardized_current = (current_x-center)/scale
    lad_return = current_volatility*(intercept + math.fsum(
        float(a)*float(b) for a, b in zip(coef, standardized_current)))
    lad_point = baseline*(1.+lad_return/100.)
    lad = {"features": names, "coefficients": coef.tolist(), "intercept": intercept,
           "scaler": {"center": center.tolist(), "scale": scale.tolist()},
           "current_standardized_features": standardized_current.tolist(),
           "current_scale_percent": current_volatility,
           "prediction_simple_percent": lad_return, "forecast": lad_point,
           "fit": fit, "training_weight_rule": "volatility_percent / gross_return",
           "training_weight_hash": _hash(weights.tolist())}
    point = .5*huber_point+.5*lad_point
    if not math.isfinite(point) or point <= 0:
        raise ValueError("INVALID_BLENDED_FORECAST")
    return point, {"model_id": "BLEND_VOL_LAD", "config": CONFIG, "config_hash": _hash(CONFIG),
                   "features": names, "screen": screen,
                   "current_feature_values": dict(zip(names, current_x.tolist())),
                   "training_count": len(rows), "training_ids": [r["sample_id"] for r in rows],
                   "training_ids_hash": _hash([r["sample_id"] for r in rows]),
                   "max_training_label_time": max(r["label_assumed_available_time"] for r in rows),
                   "max_training_target_time": max(r["target_time"] for r in rows),
                   "huber": huber, "lad": lad, "component_weights": [.5, .5],
                   "forecast": point, "baseline": baseline, "current_outcome_read": False}
