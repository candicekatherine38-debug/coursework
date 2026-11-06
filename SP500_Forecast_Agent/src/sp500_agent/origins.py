from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
import statistics

from .calendar import NY
from .contracts import INDEX_POINTS, SP500, ContractError, Observation, Qualification, timestamp
from .validation import cutoff_gate, knowledge_time
from .freshness import freshness


@dataclass(frozen=True)
class OriginWindow:
    forecast_origin: datetime
    nominal_target_time: datetime
    target_session: date
    effective_target_time: datetime
    information_cutoff: datetime
    baseline_session: date
    baseline_time: datetime
    baseline: Observation | None
    target: Observation | None
    last_eligible_observation: str | None
    label_available_time: datetime | None
    eligible_features: tuple[str, ...]
    rejected_features: tuple[dict, ...]
    exclusion_reasons: tuple[str, ...]
    cohort: str
    future_sessions: int
    qualified: bool
    exploratory_eligible: bool
    target_rule: str


def generate_origins(calendar, prices, first: date, last: date, audit_asof: datetime, *, features=(), allow_assumed=False,
                     feature_kinds=None, required_feature_instruments=()):
    """Strict qualification and retrospective exploratory coverage stay separate.

    audit_asof is the actual saved snapshot's knowledge boundary. Labels with no
    historic publication evidence remain unknown; observation-only exploration is
    explicit and is never promoted to strict scoring.
    """
    audit_asof = timestamp(audit_asof)
    by_day = defaultdict(list)
    for row in prices:
        by_day[row.session_date].append(row)
    output = []
    for origin, nominal in calendar.fridays(first, last):
        baseline_session = calendar.latest_close(origin)
        target_session = calendar.latest_close(nominal)
        baseline_group, target_group = by_day[baseline_session.day], by_day[target_session.day]
        baseline = baseline_group[0] if len(baseline_group) == 1 else None
        target = target_group[0] if len(target_group) == 1 else None
        between = calendar.between(baseline_session.close, target_session.close)
        local = origin.astimezone(NY)
        primary = (local.date() in calendar.sessions and baseline_session.day == local.date()-timedelta(days=1)
                   and not baseline_session.early_close and target_session.day == nominal.astimezone(NY).date()
                   and not any(s.early_close for s in between) and len(between) == 6)
        reasons, exploratory_reasons = [], []
        if baseline is None:
            reason = "DUPLICATE_BASELINE" if baseline_group else "MISSING_EXPECTED_BASELINE_SESSION"
            reasons.append(reason); exploratory_reasons.append(reason)
        else:
            gate = cutoff_gate(baseline, origin, allow_assumed=allow_assumed, expected_instrument=SP500,
                               expected_unit=INDEX_POINTS, require_reference=True)
            reasons.extend(gate.reasons)
            if baseline.issues(expected_instrument=SP500, expected_unit=INDEX_POINTS):
                exploratory_reasons.append("INVALID_BASELINE")
            if baseline.observation_time_utc is None or timestamp(baseline.observation_time_utc) > origin:
                exploratory_reasons.append("BASELINE_OBSERVATION_NOT_BEFORE_ORIGIN")
            if baseline.observation_time_utc is None or timestamp(baseline.observation_time_utc) != timestamp(baseline_session.close):
                reasons.append("BASELINE_CLOSE_EVENT_TIME_MISMATCH"); exploratory_reasons.append("BASELINE_CLOSE_EVENT_TIME_MISMATCH")
        if target is None:
            reason = "DUPLICATE_TARGET" if target_group else "MISSING_TARGET_SESSION"
            reasons.append(reason); exploratory_reasons.append(reason)
        elif target.issues(expected_instrument=SP500, expected_unit=INDEX_POINTS):
            reasons.append("INVALID_TARGET"); exploratory_reasons.append("INVALID_TARGET")
        if target is not None and (target.observation_time_utc is None or timestamp(target.observation_time_utc) != timestamp(target_session.close)):
            reasons.append("TARGET_CLOSE_EVENT_TIME_MISMATCH"); exploratory_reasons.append("TARGET_CLOSE_EVENT_TIME_MISMATCH")
        if target_session.close > audit_asof:
            reasons.append("TARGET_EVENT_NOT_YET_OBSERVED"); exploratory_reasons.append("TARGET_EVENT_NOT_YET_OBSERVED")
        # DIRECT_CAPTURE proves today's downloaded version, not its historic release.
        label_available = None
        if target is not None and target.availability_basis != "DIRECT_CAPTURE":
            label_available = knowledge_time(target)
        if target is not None:
            if label_available is None:
                reasons.append("HISTORICAL_LABEL_RELEASE_TIME_UNKNOWN")
            elif label_available > audit_asof:
                reasons.append("LABEL_NOT_AVAILABLE_AT_AUDIT")
            if not target.reference_verified:
                reasons.append("TARGET_OFFICIAL_REFERENCE_UNVERIFIED")
            target_gate = cutoff_gate(target, audit_asof, allow_assumed=allow_assumed,
                                      expected_instrument=SP500, expected_unit=INDEX_POINTS, require_reference=True)
            reasons.extend("LABEL:"+r for r in target_gate.reasons)
        if not calendar.pit_notices_verified:
            reasons.append("CALENDAR_PIT_NOTICE_COVERAGE_UNVERIFIED")
        accepted, rejected, eligible_groups = [], [], defaultdict(list)
        for feature in features:
            gate = cutoff_gate(feature, origin, allow_assumed=allow_assumed)
            if gate.eligible:
                kind = (feature_kinds or {}).get(feature.instrument_id)
                check = freshness(feature, origin, calendar, kind=kind, allow_assumed=allow_assumed) if kind else None
                if check and not check["usable"]:
                    rejected.append({"record_id": feature.record_id, "reasons": (check["freshness_status"],)})
                else:
                    eligible_groups[feature.instrument_id].append(feature)
            else:
                rejected.append({"record_id": feature.record_id, "reasons": gate.reasons})
        for candidates in eligible_groups.values():
            selected = max(candidates, key=lambda r: (timestamp(r.observation_time_utc), knowledge_time(r)))
            accepted.append(selected.record_id)
        for identity in required_feature_instruments:
            if identity not in eligible_groups:
                reasons.append("REQUIRED_FEATURE_UNAVAILABLE_OR_STALE:"+identity)
        output.append(OriginWindow(origin, nominal, target_session.day, timestamp(target_session.close), origin,
                                   baseline_session.day, timestamp(baseline_session.close), baseline, target,
                                   baseline.record_id if baseline and not cutoff_gate(baseline, origin, expected_instrument=SP500,
                                       expected_unit=INDEX_POINTS, require_reference=True, allow_assumed=allow_assumed).reasons else None,
                                   label_available, tuple(accepted), tuple(rejected), tuple(dict.fromkeys(reasons)),
                                   "PRIMARY_SIX_REGULAR_SESSIONS" if primary else "SECONDARY_CALENDAR_VARIANT", len(between),
                                   not reasons, not exploratory_reasons,
                                   "LATEST_OFFICIAL_CLOSE_BEFORE_NOMINAL_TARGET" if target_session.day != nominal.astimezone(NY).date()
                                   else "ACTUAL_EARLY_CLOSE" if target_session.early_close else "REGULAR_OFFICIAL_CLOSE"))
    return output


def origin_counts(windows):
    return {"candidate_origins": len(windows), "qualified_origins": sum(w.qualified for w in windows),
            "excluded_strict_origins": sum(not w.qualified for w in windows),
            "exploratory_observation_complete_origins": sum(w.exploratory_eligible for w in windows),
            "strict_exclusion_reasons_nonexclusive": dict(Counter(r for w in windows for r in w.exclusion_reasons)),
            "cohort_counts": dict(Counter(w.cohort for w in windows)),
            "exploratory_counts_by_cohort": dict(Counter(w.cohort for w in windows if w.exploratory_eligible))}


@dataclass(frozen=True)
class TrainingSample:
    sample_id: str
    forecast_origin: datetime
    baseline_time: datetime
    target_time: datetime
    label_available_time: datetime | None
    features: tuple[float, ...]
    feature_available_times: tuple[datetime | None, ...]
    qualification: Qualification = Qualification.UNQUALIFIED
    evidence_ids: tuple[str, ...] = ()


def training_rows(samples, fit_time, forecast_origin, *, validation_intervals=(), allow_assumed=False):
    fit_time, forecast_origin = timestamp(fit_time), timestamp(forecast_origin)
    if fit_time >= forecast_origin:
        raise ContractError("FIT_TIME_NOT_BEFORE_FORECAST_ORIGIN")
    accepted, rejected = [], []
    for s in samples:
        reasons = []
        if s.qualification != Qualification.VERIFIED_POINT_IN_TIME and not (allow_assumed and s.qualification == Qualification.ASSUMED_AVAILABLE_WITH_EVIDENCE):
            reasons.append("UNQUALIFIED_TRAINING_SAMPLE")
        if not s.evidence_ids:
            reasons.append("MISSING_TRAINING_EVIDENCE")
        if timestamp(s.forecast_origin) >= fit_time:
            reasons.append("TRAIN_ORIGIN_NOT_BEFORE_FIT")
        if s.label_available_time is None or timestamp(s.label_available_time) > fit_time:
            reasons.append("LABEL_NOT_MATURE")
        if timestamp(s.target_time) > fit_time:
            reasons.append("TARGET_NOT_YET_REALIZED")
        if len(s.features) != len(s.feature_available_times) or not s.features:
            reasons.append("FEATURE_SCHEMA_MISMATCH")
        for value, available in zip(s.features, s.feature_available_times):
            if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value):
                reasons.append("INVALID_FEATURE")
            if available is None or timestamp(available) > timestamp(s.forecast_origin):
                reasons.append("FEATURE_NOT_KNOWN_AT_ITS_ORIGIN")
        # Label intervals (baseline,target] intersect iff max(left ends)<min(right ends).
        if any(max(timestamp(s.baseline_time), timestamp(a)) < min(timestamp(s.target_time), timestamp(b)) for a, b in validation_intervals):
            reasons.append("OVERLAPPING_VALIDATION_LABEL")
        if reasons:
            rejected.append({"sample_id": s.sample_id, "reasons": list(dict.fromkeys(reasons))})
        else:
            accepted.append(s)
    return accepted, rejected


@dataclass(frozen=True)
class TrainingScaler:
    means: tuple[float, ...]
    scales: tuple[float, ...]
    fitted_sample_ids: tuple[str, ...]
    fit_time: datetime
    forecast_origin: datetime

    @classmethod
    def fit(cls, samples, fit_time, forecast_origin, *, validation_intervals=(), allow_assumed=False):
        rows, rejected = training_rows(samples, fit_time, forecast_origin, validation_intervals=validation_intervals, allow_assumed=allow_assumed)
        if rejected:
            raise ContractError("REJECTED_TRAINING_ROWS:"+",".join(r["sample_id"] for r in rejected))
        if not rows or len({len(r.features) for r in rows}) != 1:
            raise ContractError("EMPTY_OR_INCONSISTENT_TRAINING_SET")
        cols = list(zip(*(r.features for r in rows)))
        return cls(tuple(statistics.mean(c) for c in cols), tuple(statistics.pstdev(c) or 1.0 for c in cols),
                   tuple(r.sample_id for r in rows), timestamp(fit_time), timestamp(forecast_origin))

    def transform(self, values):
        if len(values) != len(self.means) or any(not math.isfinite(x) for x in values):
            raise ContractError("INVALID_TRANSFORM_INPUT")
        return tuple((x-m)/s for x, m, s in zip(values, self.means, self.scales))
