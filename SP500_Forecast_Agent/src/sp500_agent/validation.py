from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable

from .contracts import INDEX_POINTS, SP500, Observation, Qualification, timestamp


@dataclass(frozen=True)
class GateResult:
    eligible: bool
    reasons: tuple[str, ...]
    knowledge_time: datetime | None = None


def knowledge_time(record: Observation):
    """A proven capture is a bound, never a fabricated first-publication timestamp."""
    if not record.availability_evidence_id:
        return None
    if record.availability_basis == "DIRECT_CAPTURE":
        return timestamp(record.retrieval_time_utc)
    if record.availability_basis == "ARCHIVED_PUBLICATION_AND_VINTAGE":
        if record.publication_time_utc is None or record.vintage_available_at_utc is None:
            return None
        return max(timestamp(record.publication_time_utc), timestamp(record.vintage_available_at_utc))
    if record.availability_basis == "DOCUMENTED_CONSERVATIVE_BOUND":
        if record.availability_upper_bound_utc is None or record.vintage_available_at_utc is None:
            return None
        return max(timestamp(record.availability_upper_bound_utc), timestamp(record.vintage_available_at_utc))
    return None


def cutoff_gate(record: Observation, origin: datetime, *, allow_assumed=False, live_freeze=False,
                expected_instrument=None, expected_unit=None, require_reference=False):
    origin = timestamp(origin)
    reasons = record.issues(expected_instrument=expected_instrument, expected_unit=expected_unit)
    if record.observation_time_utc is None:
        reasons.append("UNKNOWN_OBSERVATION_TIME")
    elif timestamp(record.observation_time_utc) > origin:
        reasons.append("FUTURE_OBSERVATION")
    allowed = {Qualification.VERIFIED_POINT_IN_TIME}
    if allow_assumed:
        allowed.add(Qualification.ASSUMED_AVAILABLE_WITH_EVIDENCE)
    if record.qualification not in allowed:
        reasons.append("DATA_QUALIFICATION_NOT_ALLOWED:"+record.qualification.value)
    when = knowledge_time(record)
    if when is None:
        reasons.append("UNPROVEN_AVAILABILITY_OR_VINTAGE")
    elif when > origin:
        reasons.append("POST_ORIGIN_AVAILABILITY_OR_REVISION")
    # Even a published older observation cannot justify a later revised version.
    if record.publication_time_utc is not None and timestamp(record.publication_time_utc) > origin:
        reasons.append("POST_ORIGIN_PUBLICATION")
    if record.vintage_available_at_utc is not None and timestamp(record.vintage_available_at_utc) > origin:
        reasons.append("FUTURE_REVISION")
    if live_freeze and timestamp(record.retrieval_time_utc) > origin:
        reasons.append("SNAPSHOT_CAPTURE_AFTER_CUTOFF")
    if require_reference and not record.reference_verified:
        reasons.append("OFFICIAL_REFERENCE_EQUIVALENCE_UNVERIFIED")
    return GateResult(not reasons, tuple(dict.fromkeys(reasons)), when)


def quality_audit(records: Iterable[Observation], calendar, start, end, *, jump_threshold=0.08):
    rows = list(records)
    keys, duplicate_ids, invalid = {}, [], []
    for r in rows:
        issues = r.issues(expected_instrument=SP500, expected_unit=INDEX_POINTS)
        if r.session_date is None:
            issues.append("MISSING_SESSION_DATE")
        if issues:
            invalid.append({"record_id": r.record_id, "reasons": issues})
        key = (r.provider, r.instrument_id, r.session_date, r.vintage_id)
        if key in keys:
            duplicate_ids.append([keys[key], r.record_id])
        keys[key] = r.record_id
    dates = [r.session_date for r in rows]
    expected = {d for d in calendar.sessions if start <= d <= end}
    present = {d for d in dates if d is not None}
    ordered = sorted((r for r in rows if r.session_date is not None and not r.issues()), key=lambda r: r.session_date)
    jumps = []
    for previous, current in zip(ordered, ordered[1:]):
        change = current.value/previous.value-1
        if abs(change) >= jump_threshold:
            jumps.append({"previous_session": previous.session_date.isoformat(), "session": current.session_date.isoformat(),
                          "simple_return": change, "previous_record": previous.record_id, "record": current.record_id,
                          "action": "FLAG_RETAIN_NOT_AUTOMATICALLY_DELETE", "investigation": "SOURCE_CORROBORATION_REQUIRED"})
    return {"rows": len(rows), "source_order_strict": all(a < b for a, b in zip(dates, dates[1:]) if a is not None and b is not None),
            "duplicates": duplicate_ids, "invalid": invalid, "expected_sessions": len(expected),
            "missing_sessions": [d.isoformat() for d in sorted(expected-present)],
            "unexpected_nontrading_dates": [d.isoformat() for d in sorted(present-expected)],
            "jump_threshold": jump_threshold, "jump_flags": jumps, "automatic_deletions": 0,
            "calendar_id": calendar.calendar_id}


def compare_sources(left: list[Observation], right: list[Observation], tolerance=0.01):
    """Retain conflicts, unmatched dates and common-upstream limitations."""
    def group(rows):
        out = {}
        for r in rows:
            out.setdefault((r.instrument_id, r.unit, r.session_date), []).append(r)
        return out
    a, b = group(left), group(right)
    comparisons, duplicates = [], []
    for key in sorted(a.keys() & b.keys(), key=str):
        if len(a[key]) != 1 or len(b[key]) != 1:
            duplicates.append(str(key))
            continue
        x, y = a[key][0], b[key][0]
        if x.issues() or y.issues():
            comparisons.append({"date": str(key[2]), "status": "INVALID_SOURCE_RECORD", "records": [x.record_id, y.record_id]})
            continue
        diff = abs(x.value-y.value)
        comparisons.append({"date": str(key[2]), "absolute_difference": diff,
                            "status": "AGREE_WITHIN_TOLERANCE" if diff <= tolerance else "CONFLICT_STOP_DEPENDENT_USE",
                            "left_record": x.record_id, "right_record": y.record_id,
                            "left_value": x.value, "right_value": y.value})
    return {"tolerance_points": tolerance, "comparisons": comparisons, "duplicate_groups": duplicates,
            "left_only": len(a.keys()-b.keys()), "right_only": len(b.keys()-a.keys()),
            "independent_upstream_proven": False, "official_certification": False}


def quarantine_conflicts(records, comparison):
    """Preserve every row/value, but prevent unresolved conflicts entering factors."""
    disputed = {r[field] for r in comparison["comparisons"] if r["status"] == "CONFLICT_STOP_DEPENDENT_USE"
                for field in ("left_record", "right_record")}
    return [replace(r, validation_status="CONFLICT_QUARANTINED",
                    extra={**r.extra, "conflict_action": "STOP_DEPENDENT_USE_PRESERVE_SOURCE_VALUE"})
            if r.record_id in disputed else r for r in records]
