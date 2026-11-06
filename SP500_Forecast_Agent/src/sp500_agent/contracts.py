from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import math

UTC = timezone.utc
SP500 = "SP500_PRICE_USD"
INDEX_POINTS = "INDEX_POINTS"


class ContractError(ValueError):
    pass


class Qualification(str, Enum):
    VERIFIED_POINT_IN_TIME = "VERIFIED_POINT_IN_TIME"
    ASSUMED_AVAILABLE_WITH_EVIDENCE = "ASSUMED_AVAILABLE_WITH_EVIDENCE"
    EXPLORATORY_ONLY = "EXPLORATORY_ONLY"
    UNQUALIFIED = "UNQUALIFIED"
    UNAVAILABLE = "UNAVAILABLE"


def timestamp(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("NAIVE_OR_INVALID_TIME")
    return result.astimezone(UTC)


def finite(value, *, positive=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError("NONFINITE_OR_NONNUMERIC_VALUE")
    if positive and value <= 0:
        raise ContractError("NONPOSITIVE_PRICE")
    return float(value)


@dataclass(frozen=True)
class Observation:
    record_id: str
    instrument_id: str
    provider: str
    value: float | None
    unit: str
    observation_time_utc: datetime | None
    publication_time_utc: datetime | None
    retrieval_time_utc: datetime
    availability_basis: str
    market_session: str
    reported_delay: int | None
    source_url: str
    qualification: Qualification = Qualification.UNQUALIFIED
    session_date: date | None = None
    vintage_available_at_utc: datetime | None = None
    availability_upper_bound_utc: datetime | None = None
    availability_evidence_id: str | None = None
    raw_sha256: str | None = None
    reference_verified: bool = False
    vintage_id: str = "UNVERIFIED_CURRENT_DOWNLOAD"
    validation_status: str = "UNVALIDATED"
    observation_semantics: str = "UNKNOWN"
    cutoff_eligible: bool = False
    freshness_status: str = "NOT_EVALUATED"
    redistribution_cleared: bool = False
    next_release_time_utc: datetime | None = None
    extra: dict = field(default_factory=dict)

    def issues(self, *, expected_instrument=None, expected_unit=None):
        issues = []
        if self.validation_status == "CONFLICT_QUARANTINED":
            issues.append("SOURCE_CONFLICT_UNRESOLVED")
        if expected_instrument and self.instrument_id != expected_instrument:
            issues.append("WRONG_INSTRUMENT")
        if expected_unit and self.unit != expected_unit:
            issues.append("WRONG_UNIT")
        try:
            finite(self.value, positive=self.unit in (INDEX_POINTS, "USD_PER_SHARE", "FUTURES_INDEX_POINTS", "VIX_POINTS"))
        except ContractError as exc:
            issues.append(str(exc))
        for name in ("observation_time_utc", "publication_time_utc", "retrieval_time_utc",
                     "vintage_available_at_utc", "availability_upper_bound_utc", "next_release_time_utc"):
            value = getattr(self, name)
            if value is not None:
                try:
                    timestamp(value)
                except (ContractError, ValueError):
                    issues.append("INVALID_TIME:" + name)
        if self.reported_delay is not None and (isinstance(self.reported_delay, bool) or not isinstance(self.reported_delay, int) or self.reported_delay < 0):
            issues.append("INVALID_REPORTED_DELAY")
        if self.qualification == Qualification.ASSUMED_AVAILABLE_WITH_EVIDENCE:
            if not self.availability_evidence_id or self.availability_upper_bound_utc is None:
                issues.append("ASSUMPTION_WITHOUT_EVIDENCE_OR_BOUND")
        return issues


def jsonable(value):
    if hasattr(value, "__dataclass_fields__"):
        return jsonable(asdict(value))
    if isinstance(value, datetime):
        return timestamp(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
