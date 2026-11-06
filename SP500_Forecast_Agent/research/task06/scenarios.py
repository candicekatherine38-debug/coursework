"""Bounded acceptance probes. Formal clock examples never run model inference."""
import argparse
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import FORMAL_ORIGIN, check_clock, now, read, require, sha, write
from .engine import build_rows, calculate
from .legacy.model import predict
from .pipeline import approval_binding, inspect_candidate, validate_model_approval, verify_approval_binding
from .sources import calendar, parsed_capture
from .verify import verify


def rejected(operation, text):
    try:
        operation()
    except (ValueError, OSError, KeyError) as exc:
        require(text in str(exc), f"UNEXPECTED_FAILURE_{exc}")
        return {"pass": True, "expected": text, "actual": str(exc)}
    raise ValueError("EXPECTED_REJECTION_DID_NOT_OCCUR_" + text)


def anomaly(candidate, source_probe):
    snapshot, forecast = inspect_candidate(candidate)
    stale = deepcopy(snapshot)
    removed = stale["prices"].pop(stale["baseline_date"])
    results = {"stale": {**rejected(lambda: calculate(stale), "STALE_BASELINE_EXPECTED_"),
                        "removed_session": stale["baseline_date"], "injection": "removed genuine final close",
                        "removed_value": removed, "forecast_emitted": False}}
    bad = deepcopy(snapshot)
    bad["prices"]["2026-11-20"] = 1.0
    results["future"] = rejected(lambda: calculate(bad), "POST_BASELINE_INPUT")
    altered = {**forecast, "point": forecast["point"] + .02}
    results["changed_point"] = rejected(lambda: verify(snapshot, altered), "FORECAST_POINT_MISMATCH")
    rows, current = build_rows(snapshot)
    current["historical_feature_values"].pop("mom20")
    results["missing_factor"] = rejected(lambda: predict(rows, current), "mom20")
    with TemporaryDirectory() as d:
        p = Path(d)
        observed_html = (source_probe / "SP_DJI.html").read_bytes()
        (p / "FRED_SP500.csv").write_bytes(observed_html)
        rec = next(r for r in read(source_probe / "capture_manifest.json") if r["provider"] == "FRED_SP500")
        rec["sha256"] = sha(p / "FRED_SP500.csv")
        results["html_masquerading_as_csv"] = rejected(lambda: parsed_capture(p, rec, calendar()), "HTML_NOT_CSV")
    return results


def cutoff_rehearsal(candidate):
    _, result = inspect_candidate(candidate)
    valid_start = "2026-11-13T11:55:00Z"
    check_clock(valid_start, "2026-11-13T11:59:59Z", FORMAL_ORIGIN)
    tests = {"pre_cutoff": {"pass": True, "example_clock": "SIMULATED"}}
    tests["crossed_cutoff"] = rejected(
        lambda: check_clock(valid_start, "2026-11-13T12:00:01Z", FORMAL_ORIGIN),
        "CAPTURE_OR_EXECUTION_CROSSES_CUTOFF")
    tests["equal_cutoff"] = rejected(
        lambda: check_clock(valid_start, FORMAL_ORIGIN, FORMAL_ORIGIN),
        "CAPTURE_OR_EXECUTION_CROSSES_CUTOFF")
    plan_file = candidate / "model_plan.json"
    plan = read(plan_file)
    sample = {"approved_at": valid_start, "plan_sha256": sha(plan_file),
              "model_id": plan["primary"], "kind": "SIMULATED_FIXTURE"}
    validate_model_approval(sample, plan_file, plan, plan["primary"], "2026-11-13T11:56:00Z")
    tests["unapproved_switch"] = rejected(
        lambda: validate_model_approval(sample, plan_file, plan, plan["fallback"], "2026-11-13T11:56:00Z"),
        "MODEL_APPROVAL_BINDING_MISMATCH")
    altered = deepcopy(plan)
    altered["code_sha256"][next(iter(altered["code_sha256"]))] = "0"*64
    tests["model_hash_changed"] = rejected(
        lambda: validate_model_approval(sample, plan_file, altered, plan["primary"], "2026-11-13T11:56:00Z"),
        "MODEL_PLAN_CHANGED")
    binding = approval_binding(candidate, result)
    require(set(k for k in binding if k.startswith("point")) == {"point_2dp"}, "NON_UNIQUE_POINT")
    bad = {**binding, "point_2dp": "1.00"}
    tests["modified_point"] = rejected(
        lambda: verify_approval_binding(bad, candidate, result), "FORECAST_APPROVAL_DOES_NOT_BIND_RESULT")
    tests["single_point_contract"] = {"pass": True, "formal_forecast_computed": False}
    return tests


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scenario", choices=("anomaly", "cutoff"))
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--source-probe", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = anomaly(a.candidate, a.source_probe) if a.scenario == "anomaly" else cutoff_rehearsal(a.candidate)
    write(a.output, {"mode": "REHEARSAL", "executed_at_utc": now(), "checks": result})
    print("PASS", a.scenario, len(result), "checks")


if __name__ == "__main__":
    main()
