"""Read cached group workbook values without Excel, Bloomberg or network access."""
from __future__ import annotations

import csv
from datetime import datetime, date, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import posixpath
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from .features import generate

NY = ZoneInfo("America/New_York")
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assumed_availability(day):
    d = date.fromisoformat(day) + timedelta(days=1)
    return datetime.combine(d, time(6, 30), NY).astimezone(timezone.utc).isoformat()


def read_workbook(path, *, max_date=date(2026, 10, 30)):
    """Minimal strict OOXML reader for this supplied sheet and cached array result.

    Formula text is recorded but never executed. Missing numeric caches fail.
    PX_BID is retained for review only and cannot enter the forecast pipeline.
    """
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            sr = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(n.itertext()) for n in sr.findall("s:si", NS)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        prop = workbook.find("s:workbookPr", NS)
        if prop is not None and prop.attrib.get("date1904") in {"1", "true"}:
            raise ValueError("UNSUPPORTED_1904_DATE_SYSTEM")
        sheet = next((x for x in workbook.findall("s:sheets/s:sheet", NS)
                      if x.attrib.get("name") == "Worksheet"), None)
        if sheet is None:
            raise ValueError("EXPECTED_WORKSHEET_MISSING")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(x.attrib["Target"] for x in relationships
                      if x.attrib["Id"] == sheet.attrib["{" + REL + "}id"])
        part = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        if not part.startswith("xl/"):
            raise ValueError("INVALID_WORKSHEET_PART")
        tree = ET.fromstring(archive.read(part))
        cells, formulas = {}, []
        for cell in tree.findall("s:sheetData/s:row/s:c", NS):
            address = cell.attrib["r"]
            formula = cell.find("s:f", NS)
            val = cell.find("s:v", NS)
            if formula is not None:
                formulas.append({"cell": address, "formula": formula.text,
                                 "range": formula.attrib.get("ref"), "cached_value_present": val is not None})
            kind = cell.attrib.get("t")
            if kind == "inlineStr":
                value = "".join(cell.find("s:is", NS).itertext())
            elif val is None:
                value = None
            elif kind == "s":
                value = strings[int(val.text)]
            elif kind in {"str", "e"}:
                value = val.text
            else:
                value = float(val.text)
            cells[address] = value
    if cells.get("B1") != "SPX Index":
        raise ValueError("WRONG_SECURITY_EXPECTED_SPX_INDEX")
    if [cells.get(x) for x in ["A7", "B7", "C7"]] != ["Date", "PX_LAST", "PX_BID"]:
        raise ValueError("WORKBOOK_COLUMNS_CHANGED")
    rows = []
    row_numbers = sorted({int(k[1:]) for k in cells if k[0] in "ABC" and k[1:].isdigit() and int(k[1:]) >= 8})
    for row in row_numbers:
        serial, last, bid = [cells.get(f"{c}{row}") for c in "ABC"]
        if serial is None and last is None and bid is None:
            continue
        if not isinstance(serial, (float, int)) or not math.isfinite(serial) or serial != int(serial):
            raise ValueError(f"MISSING_OR_INVALID_DATE_CACHE_AT_ROW_{row}")
        d = (datetime(1899, 12, 30) + timedelta(days=serial)).date()
        if d.weekday() >= 5 or d > max_date:
            raise ValueError(f"INVALID_OR_FUTURE_SESSION_AT_ROW_{row}")
        if not isinstance(last, (float, int)) or not math.isfinite(last) or last <= 0:
            raise ValueError(f"MISSING_OR_INVALID_PX_LAST_AT_ROW_{row}")
        if bid is not None and (not isinstance(bid, (float, int)) or not math.isfinite(bid)):
            raise ValueError(f"INVALID_PX_BID_AT_ROW_{row}")
        rows.append({"date": d.isoformat(), "PX_LAST": last, "PX_BID": bid,
                     "source_row": row})
    if not rows or len({r["date"] for r in rows}) != len(rows):
        raise ValueError("EMPTY_OR_DUPLICATE_SOURCE_DATES")
    if any(a["date"] <= b["date"] for a, b in zip(rows, rows[1:])):
        raise ValueError("EXPECTED_STRICT_DESCENDING_SOURCE")
    rows.sort(key=lambda r: r["date"])
    start_meta = (datetime(1899, 12, 30) + timedelta(days=cells["B2"])).date().isoformat()
    end_meta = (datetime(1899, 12, 30) + timedelta(days=cells["B3"])).date().isoformat()
    if rows[0]["date"] != start_meta or rows[-1]["date"] != end_meta or cells.get("B4") != "D":
        raise ValueError("SOURCE_METADATA_COVERAGE_MISMATCH")
    audit = {"source_file": Path(path).name, "source_sha256": sha(path), "sheet": "Worksheet",
             "row_count": len(rows), "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
             "security": "SPX Index", "price_field": "PX_LAST", "unit": "INDEX_POINTS",
             "excluded_field": "PX_BID", "cached_values_only": True, "formulas": formulas,
             "publication_time": None, "original_retrieval_time": None, "historical_vintage_time": None,
             "qualification": "GROUP_SUPPLIED_SNAPSHOT_NOT_PIT_CERTIFIED"}
    return rows, audit


def build_dataset(package, workbook, allow_source_change=False):
    package = Path(package)
    cfg = json.loads((package / "config/model.json").read_text())
    # This file is a frozen manifest, not an unconsumed tuning interface.
    from .model import CONFIG
    expected_settings = {"model": CONFIG["model_id"], "weights": {"HUBER_PRICE3": .5, "VS_LAD_PRICE3": .5},
                         "huberalpha": CONFIG["huber"]["alpha"], "huber_epsilon": CONFIG["huber"]["epsilon"],
                         "lad_alpha": CONFIG["lad"]["alpha"], "volatility_scale_sessions": CONFIG["lad"]["scale_sessions"],
                         "volatility_floor_percent": CONFIG["lad"]["scale_floor_percent"], "min_train": 104,
                         "evaluation_start": "2024-01-01", "evaluation_end": "2025-12-31",
                         "source_field": "PX_LAST", "source_security": "SPX Index", "source_sheet": "Worksheet"}
    for key, expected_value in expected_settings.items():
        if cfg.get(key) != expected_value:
            raise ValueError("FROZEN_MODEL_MANIFEST_MISMATCH:" + key)
    for relative, key in [("config/matched_origins.csv", "cohort_sha256"),
                          ("data/calendar_xnys_2016_2026.csv", "calendar_sha256")]:
        if sha(package / relative) != cfg[key]:
            raise ValueError("FIXED_PROTOCOL_FILE_CHANGED:" + relative)
    if not allow_source_change and sha(workbook) != cfg["source_sha256"]:
        raise ValueError("WORKBOOK_HASH_CHANGED_USE_EXPLICIT_ACCEPT_NEW_SNAPSHOT")
    raw, audit = read_workbook(workbook)
    prices = {r["date"]: r["PX_LAST"] for r in raw}
    with (package / "data/calendar_xnys_2016_2026.csv").open(newline="") as f:
        calendar = list(csv.DictReader(f))
    start = max(raw[0]["date"], calendar[0]["session_date"])
    end = min(raw[-1]["date"], calendar[-1]["session_date"])
    expected = {r["session_date"] for r in calendar if start <= r["session_date"] <= end}
    observed = {r["date"] for r in raw if start <= r["date"] <= end}
    if observed != expected:
        raise ValueError("SOURCE_MISSING_OR_UNEXPECTED_CALENDAR_SESSIONS:" + str({
            "missing": sorted(expected-observed), "extra": sorted(observed-expected)}))
    # Exclude 2026 before any feature calculation or historical model evaluation.
    pre2026 = {d: v for d, v in prices.items() if d < "2026-01-01"}
    series = pd.Series(pre2026, dtype=float)
    features = generate(series)
    with (package / "config/matched_origins.csv").open(newline="") as f:
        cohort = list(csv.DictReader(f))
    sessions = [(r["session_date"], stamp(r["close_time_utc"])) for r in calendar]
    output, excluded = [], []
    for item in cohort:
        origin = stamp(item["forecast_origin"])
        if origin.year >= 2026 or origin.astimezone(NY).weekday() != 4:
            raise ValueError("INVALID_REVIEW_ORIGIN")
        scheduled = datetime.combine(origin.astimezone(NY).date()+timedelta(days=7), time(16), NY)
        baseline = max((x for x in sessions if x[1] <= origin), key=lambda x: x[1])
        target = max((x for x in sessions if x[1] <= scheduled), key=lambda x: x[1])
        if (baseline[0], target[0]) != (item["baseline_date"], item["target_date"]):
            raise ValueError("FIXED_COHORT_CALENDAR_MISMATCH")
        if baseline[1] != stamp(item["baseline_time"]) or target[1] != stamp(item["target_time"]):
            raise ValueError("FIXED_COHORT_CLOCK_MISMATCH")
        if item["eligible"] != "True":
            excluded.append({"forecast_origin": item["forecast_origin"],
                             "reason": item["legacy_exclusion_reason"],
                             "interpretation": "legacy matched-cohort mask, not a defect found in group workbook"})
            continue
        b, t = item["baseline_date"], item["target_date"]
        if b not in pre2026 or t not in prices:
            raise ValueError("MISSING_REQUIRED_BASELINE_OR_TARGET:" + item["sample_id"])
        values = features.loc[b].to_dict()
        if not np.isfinite(list(values.values())).all():
            raise ValueError("INCOMPLETE_REQUIRED_PAST_FEATURES:" + b)
        output.append({**{k: item[k] for k in ["sample_id", "forecast_origin", "baseline_date",
                                               "baseline_time", "target_date", "target_time"]},
                       "eligible": True, "baseline_index_level": pre2026[b], "target_index_level": prices[t],
                       "historical_feature_values": values, "y_log_return": math.log(prices[t]/pre2026[b]),
                       "feature_assumed_available_time": assumed_availability(b),
                       "label_assumed_available_time": assumed_availability(t)})
    audit.update(calendar_sessions_checked=len(expected), calendar_missing=[], calendar_unexpected=[],
                 modelling_source_end=max(pre2026), model_rows_2026=0, eligible_weekly_rows=len(output),
                 legacy_excluded_origins=excluded, training_cohort="ORIGINAL_MATCHED_COHORT",
                 historical_availability=cfg["historical_availability"])
    return output, raw, audit


def training_rows(pool, current):
    fit = stamp(current["forecast_origin"]) - timedelta(microseconds=1)
    base = stamp(current["baseline_time"])
    rows = [r for r in pool if stamp(r["forecast_origin"]) < fit
            and stamp(r["label_assumed_available_time"]) <= fit
            and stamp(r["target_time"]) <= base]
    if any(stamp(r["feature_assumed_available_time"]) > stamp(r["forecast_origin"]) for r in rows):
        raise ValueError("TRAINING_FEATURE_AFTER_ORIGIN")
    return rows
