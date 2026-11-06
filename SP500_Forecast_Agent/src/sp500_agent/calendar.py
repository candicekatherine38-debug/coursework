from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import csv
import hashlib
import json

from .contracts import ContractError, timestamp

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Session:
    day: date
    open: datetime
    close: datetime
    early_close: bool = False


class MarketCalendar:
    def __init__(self, sessions, start: date, end: date, *, calendar_id="SYNTHETIC", pit_notices_verified=False):
        self.start, self.end, self.calendar_id = start, end, calendar_id
        self.pit_notices_verified = pit_notices_verified
        rows = list(sessions)
        if len({r.day for r in rows}) != len(rows):
            raise ContractError("DUPLICATE_CALENDAR_SESSION")
        if any(timestamp(r.open) >= timestamp(r.close) for r in rows):
            raise ContractError("INVALID_CALENDAR_INTERVAL")
        self.sessions = {r.day: r for r in rows}

    @classmethod
    def load(cls, path: Path, metadata_path: Path):
        meta = json.loads(metadata_path.read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta["snapshot_sha256"]:
            raise ContractError("CALENDAR_HASH_MISMATCH")
        with path.open() as f:
            rows = [Session(date.fromisoformat(r["session_date"]), timestamp(r["open_time_utc"]),
                            timestamp(r["close_time_utc"]), r["early_close"] == "true") for r in csv.DictReader(f)]
        return cls(rows, date.fromisoformat(meta["coverage_start"]), date.fromisoformat(meta["coverage_end"]),
                   calendar_id="XNYS_exchange_calendars_"+meta["packages"]["exchange_calendars"])

    def check_date(self, day):
        if not self.start <= day <= self.end:
            raise ContractError("OUTSIDE_CALENDAR_COVERAGE")

    def latest_close(self, at: datetime):
        at = timestamp(at)
        self.check_date(at.astimezone(NY).date())
        candidates = [s for s in self.sessions.values() if timestamp(s.close) <= at]
        if not candidates:
            raise ContractError("NO_PREVIOUS_SESSION_IN_CALENDAR")
        return max(candidates, key=lambda s: s.close)

    def between(self, after: datetime, through: datetime):
        return sorted((s for s in self.sessions.values() if timestamp(after) < timestamp(s.close) <= timestamp(through)), key=lambda s: s.day)

    def fridays(self, first: date, last: date):
        self.check_date(first)
        self.check_date(last + timedelta(days=7))
        day = first + timedelta(days=(4-first.weekday()) % 7)
        while day <= last:
            origin = timestamp(datetime(day.year, day.month, day.day, 7, tzinfo=NY))
            target_day = day + timedelta(days=7)
            target = timestamp(datetime(target_day.year, target_day.month, target_day.day, 16, tzinfo=NY))
            yield origin, target
            day += timedelta(days=7)
