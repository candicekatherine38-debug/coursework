"""Past-only price factors used by the existing BLEND_VOL_LAD model.

The 134 candidate definitions reproduce the close-only subset of Qlib's public
Alpha158 and Alpha360 formula families. This module implements the formulas
directly and does not require or redistribute the private QuantML repository.
Core momentum and volatility use full input precision. Candidate factors retain
the original provider's float32 inputs and float32 output convention. No price is
forward-filled and an incomplete 63-session warm-up is explicitly unavailable.
"""
from __future__ import annotations

import math
import statistics

import numpy as np
import pandas as pd

WINDOWS = (5, 10, 20, 30, 60)
FAMILIES = ("ROC", "MA", "STD", "BETA", "RSQR", "RESI", "QTLU", "QTLD",
            "RANK", "CNTP", "CNTN", "CNTD", "SUMP", "SUMN", "SUMD")
POOL = [f"{family}{n}" for family in FAMILIES for n in WINDOWS] + [
    f"CLOSE{n}" for n in range(59, 0, -1)]
CORE = ["mom20", "rv20"]


def _regression(values: np.ndarray, window: int):
    """OLS against consecutive session positions, using each past window."""
    slopes = np.full(len(values), np.nan)
    squares = np.full(len(values), np.nan)
    residuals = np.full(len(values), np.nan)
    for end in range(window - 1, len(values)):
        y = values[end - window + 1:end + 1].astype(float)
        x = np.arange(1, window + 1, dtype=float)
        sx = float(x.sum())
        sy = float(y.sum())
        sxx = float(np.sum(x * x))
        syy = float(np.sum(y * y))
        sxy = float(np.sum(x * y))
        numerator = window * sxy - sx * sy
        denominator = window * sxx - sx * sx
        slope = numerator / denominator
        slopes[end] = slope
        residuals[end] = y[-1] - (slope * window + sy / window - slope * sx / window)
        variance_term = window * syy - sy * sy
        if variance_term > 0 and np.std(y, ddof=1) > 2e-5:
            r = numerator / math.sqrt(denominator * variance_term)
            squares[end] = r * r
    return slopes, squares, residuals


def generate(close: pd.Series) -> pd.DataFrame:
    """Return mom20, rv20 and the fixed 134-factor pool, indexed as input.

    Input must be chronological, positive, finite and unique by session. Rows
    before the 63rd supplied session are all NaN. Missing sessions must be
    detected by the calling calendar validator, not silently filled here.
    """
    if not isinstance(close, pd.Series) or close.empty:
        raise ValueError("CLOSE_MUST_BE_NONEMPTY_SERIES")
    if close.index.has_duplicates or not close.index.is_monotonic_increasing:
        raise ValueError("CLOSE_SESSIONS_NOT_UNIQUE_AND_SORTED")
    raw = close.to_numpy(dtype=float)
    if not np.isfinite(raw).all() or np.any(raw <= 0):
        raise ValueError("CLOSE_MUST_BE_POSITIVE_AND_FINITE")
    price = pd.Series(raw.astype(np.float32), index=close.index)
    previous = price.shift(1)
    change = price - previous
    up = pd.Series(np.maximum(change, 0), index=close.index)
    down = pd.Series(np.maximum(-change, 0), index=close.index)
    absolute = change.abs()
    positive = (price > previous).astype(float)
    negative = (price < previous).astype(float)
    columns = {}
    for n in WINDOWS:
        rolling = price.rolling(n, min_periods=n)
        slope, square, residual = _regression(price.to_numpy(), n)
        columns[f"ROC{n}"] = price.shift(n) / price
        columns[f"MA{n}"] = rolling.mean() / price
        columns[f"STD{n}"] = rolling.std(ddof=1) / price
        columns[f"BETA{n}"] = pd.Series(slope, index=close.index) / price
        columns[f"RSQR{n}"] = pd.Series(square, index=close.index)
        columns[f"RESI{n}"] = pd.Series(residual, index=close.index) / price
        columns[f"QTLU{n}"] = rolling.quantile(.8) / price
        columns[f"QTLD{n}"] = rolling.quantile(.2) / price
        columns[f"RANK{n}"] = rolling.rank(pct=True)
        pos = positive.rolling(n, min_periods=n).mean()
        neg = negative.rolling(n, min_periods=n).mean()
        columns[f"CNTP{n}"], columns[f"CNTN{n}"] = pos, neg
        columns[f"CNTD{n}"] = pos - neg
        su = up.rolling(n, min_periods=n).sum()
        sd = down.rolling(n, min_periods=n).sum()
        total = absolute.rolling(n, min_periods=n).sum() + 1e-12
        columns[f"SUMP{n}"] = su / total
        columns[f"SUMN{n}"] = sd / total
        columns[f"SUMD{n}"] = (su - sd) / total
    for lag in range(59, 0, -1):
        columns[f"CLOSE{lag}"] = price.shift(lag) / price
    factors = pd.DataFrame(columns, index=close.index)[POOL].astype(np.float32).astype(float)
    # math.log and statistics.stdev preserve the original core definitions.
    momentum = np.full(len(raw), np.nan)
    volatility = np.full(len(raw), np.nan)
    for end in range(62, len(raw)):
        momentum[end] = math.log(raw[end] / raw[end - 20])
        returns = [math.log(raw[j] / raw[j - 1]) for j in range(end - 19, end + 1)]
        volatility[end] = statistics.stdev(returns) * math.sqrt(252)
    factors.insert(0, "rv20", volatility)
    factors.insert(0, "mom20", momentum)
    factors.iloc[:62] = np.nan
    return factors


def definitions() -> dict:
    """Human-readable definitions for the fixed pool; t is baseline session."""
    return {
        "mom20": "log(P[t]/P[t-20]) using full input precision",
        "rv20": "sample std (ddof=1) of last20 log close returns * sqrt(252)",
        "candidate_precision": "float32 closes and final factors, matching former Qlib provider",
        "windows": list(WINDOWS), "candidate_count": len(POOL),
        "ROC": "P[t-n]/P[t]", "MA": "mean(last n closes)/P[t]",
        "STD": "sample std(last n closes)/P[t]",
        "BETA": "OLS slope of last n closes against positions1..n /P[t]",
        "RSQR": "squared Pearson correlation of last n closes and positions1..n",
        "RESI": "last observation minus fitted OLS value, divided by P[t]",
        "QTLU": "80th percentile(last n closes)/P[t]",
        "QTLD": "20th percentile(last n closes)/P[t]",
        "RANK": "average percentile rank of current close within last n closes",
        "CNTP": "fraction of last n close changes strictly positive",
        "CNTN": "fraction of last n close changes strictly negative",
        "CNTD": "CNTP-CNTN",
        "SUMP": "sum positive last n point changes/(sum absolute changes+1e-12)",
        "SUMN": "sum absolute negative last n changes/(sum absolute changes+1e-12)",
        "SUMD": "SUMP-SUMN, before final float32 rounding",
        "CLOSE1..CLOSE59": "P[t-lag]/P[t], 59 individual lag ratios",
    }
