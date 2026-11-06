from dataclasses import dataclass
from datetime import date

from .contracts import Observation, timestamp
from .validation import cutoff_gate
from .calendar import NY


@dataclass(frozen=True)
class PremarketPolicy:
    max_extra_lag_seconds: int = 300
    max_reported_delay_seconds: int = 900


def freshness(record: Observation | None, origin, calendar, *, kind, required=False, live_freeze=False,
              latest_published_session: date | None = None, pending_release_evidence_id=None,
              premarket_policy=PremarketPolicy(), allow_assumed=False):
    origin = timestamp(origin)
    if record is None:
        return {"cutoff_eligible": False, "freshness_status": "STOP_REQUIRED_DATA_MISSING" if required else "DEGRADE_OPTIONAL_DATA_MISSING",
                "validation_status": "MISSING", "action": "STOP" if required else "DEGRADE", "reasons": ["NO_OBSERVATION"]}
    gate = cutoff_gate(record, origin, live_freeze=live_freeze, allow_assumed=allow_assumed)
    result = {"record_id": record.record_id, "cutoff_eligible": gate.eligible, "validation_status": "PASS" if gate.eligible else "REJECT",
              "reasons": list(gate.reasons), "reported_delay_seconds": record.reported_delay,
              "publication_time_utc": record.publication_time_utc, "observation_time_utc": record.observation_time_utc,
              "retrieval_time_utc": record.retrieval_time_utc, "forecast_origin": origin,
              "knowledge_time": gate.knowledge_time, "source_url": record.source_url}
    if record.observation_time_utc is not None:
        result["observation_age_seconds"] = (origin-timestamp(record.observation_time_utc)).total_seconds()
    if kind == "index_close":
        latest = calendar.latest_close(origin).day
        expected = latest_published_session or latest
        if expected > latest:
            status = "IMPOSSIBLE_FUTURE_PUBLISHED_SESSION"
        elif expected < latest and not pending_release_evidence_id:
            status = "UNSUBSTANTIATED_PUBLICATION_DELAY_CLAIM"
        elif record.session_date == expected and (record.observation_time_utc is None or timestamp(record.observation_time_utc) != timestamp(calendar.sessions[expected].close)):
            status = "INDEX_CLOSE_EVENT_TIME_MISMATCH"
        elif record.session_date == expected:
            status = "LATEST_PUBLISHED_SESSION" if latest_published_session else "LATEST_COMPLETED_SESSION_PUBLICATION_CHECK_SEPARATE"
        else:
            status = "STALE_OR_MISSING_INDEX_SESSION"
        result.update(freshness_status=status, latest_completed_session=latest, expected_published_session=expected,
                      publication_pending_evidence=pending_release_evidence_id)
    elif kind == "premarket":
        age = result.get("observation_age_seconds")
        delay = record.reported_delay
        if age is None:
            status = "UNKNOWN_OBSERVATION_TIME"
        elif age < 0 or record.extra.get("completed_at_retrieval") is False:
            status = "INCOMPLETE_OR_FUTURE_BAR"
        elif record.extra.get("delay_conflict"):
            status = "CONFLICTING_DELAY_EVIDENCE"
        elif record.market_session not in {"PREMARKET", "GLOBEX", "FUTURES_OVERNIGHT", "GLOBEX_SESSION_UNVERIFIED"}:
            status = "NOT_EXPECTED_PREMARKET_SESSION"
        elif record.instrument_id == "SPY_ETF" and (origin.astimezone(NY).date() not in calendar.sessions or
             not 240 <= origin.astimezone(NY).hour*60+origin.astimezone(NY).minute < 570):
            status = "OUTSIDE_VERIFIED_EQUITY_PREMARKET_WINDOW"
        elif delay is None or not record.extra.get("delay_evidence_id"):
            status = "UNKNOWN_PROVIDER_DELAY"
        elif delay > premarket_policy.max_reported_delay_seconds:
            status = "DELAY_EXCEEDS_RESEARCH_POLICY"
        elif age > delay+premarket_policy.max_extra_lag_seconds:
            status = "STALE_PREMARKET"
        elif not record.extra.get("session_verified"):
            status = "MARKET_SESSION_UNVERIFIED"
        else:
            status = "FRESH_DELAYED" if delay > 0 else "PROVIDER_DECLARED_REALTIME_NOT_TRADING_CERTIFIED"
        result.update(freshness_status=status, quote_type=record.observation_semantics,
                      max_age_with_declared_delay_seconds=None if delay is None else delay+premarket_policy.max_extra_lag_seconds)
    elif kind == "macro":
        if record.publication_time_utc is None:
            status = "UNKNOWN_RELEASE_TIME"
        elif record.next_release_time_utc is None:
            status = "LATEST_RELEASE_SCHEDULE_UNVERIFIED"
        elif timestamp(record.next_release_time_utc) <= origin:
            status = "SUPERSEDED_MACRO_RELEASE"
        else:
            status = "CURRENT_RELEASE_VINTAGE_CHECK_SEPARATE"
        result["freshness_status"] = status
    else:
        raise ValueError("Unknown freshness kind")
    valid_statuses = {"LATEST_PUBLISHED_SESSION", "LATEST_COMPLETED_SESSION_PUBLICATION_CHECK_SEPARATE",
                      "FRESH_DELAYED", "PROVIDER_DECLARED_REALTIME_NOT_TRADING_CERTIFIED", "CURRENT_RELEASE_VINTAGE_CHECK_SEPARATE"}
    result["usable"] = gate.eligible and result["freshness_status"] in valid_statuses
    result["action"] = "USE" if result["usable"] else "STOP" if required else "DEGRADE"
    return result
