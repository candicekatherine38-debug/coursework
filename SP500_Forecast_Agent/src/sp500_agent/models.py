"""Uniform interface for future competition; only the no-change benchmark runs."""
from dataclasses import dataclass
from typing import Protocol
import math

from .contracts import INDEX_POINTS, SP500, ContractError, timestamp
from .validation import cutoff_gate

MODEL_FAMILIES = ("UNCHANGED_PRICE", "HISTORICAL_DRIFT", "MOMENTUM_REVERSAL", "ARIMA", "RIDGE", "ELASTIC_NET",
                  "RANDOM_FOREST", "XGBOOST", "LIGHTGBM", "ENSEMBLE", "DEEP_LEARNING_CONDITIONAL")


@dataclass(frozen=True)
class ForecastInput:
    window: object
    data_snapshot_id: str
    training_cutoff: object
    features: dict
    feature_evidence_ids: tuple[str, ...]
    exploratory: bool = False


@dataclass(frozen=True)
class ForecastPrediction:
    value: float
    model_identifier: str
    data_snapshot_id: str
    unit: str = INDEX_POINTS
    instrument_id: str = SP500
    mapping: str = ""


class ResearchModel(Protocol):
    def predict(self, inputs: ForecastInput) -> ForecastPrediction: ...


class NoChange:
    def predict(self, inputs):
        w = inputs.window
        if timestamp(inputs.training_cutoff) >= w.forecast_origin:
            raise ContractError("TRAINING_CUTOFF_NOT_BEFORE_ORIGIN")
        if w.baseline is None or w.baseline.issues(expected_instrument=SP500, expected_unit=INDEX_POINTS):
            raise ContractError("INVALID_SP500_BASELINE")
        if inputs.exploratory:
            if not w.exploratory_eligible:
                raise ContractError("INCOMPLETE_EXPLORATORY_WINDOW")
        elif not w.qualified or not cutoff_gate(w.baseline, w.forecast_origin, expected_instrument=SP500,
                                                expected_unit=INDEX_POINTS, require_reference=True).eligible:
            raise ContractError("UNQUALIFIED_BASELINE")
        return ForecastPrediction(float(w.baseline.value), "UNCHANGED_PRICE_V1", inputs.data_snapshot_id,
                                  mapping="Forecast index points = last eligible cash price-index close; no fitting")


def log_return_to_index_points(baseline, expected_log_return):
    if not math.isfinite(baseline) or baseline <= 0 or not math.isfinite(expected_log_return):
        raise ContractError("INVALID_LEVEL_MAPPING_INPUT")
    try:
        value = baseline*math.exp(expected_log_return)
    except OverflowError as exc:
        raise ContractError("LEVEL_MAPPING_OVERFLOW") from exc
    if not math.isfinite(value) or value <= 0:
        raise ContractError("INVALID_LEVEL_MAPPING_OUTPUT")
    return value
