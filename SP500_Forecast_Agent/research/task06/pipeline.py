"""Evidence lifecycle for the fixed forecasting interface."""
from datetime import timedelta
from pathlib import Path
import os
import shutil
import sys
import time

from .contracts import (FORMAL_ORIGIN, HERE, MODELS, ROOT, attest, check_clock, code_hashes,
                        encoded, now, read, require, sha, utc, write)
from .engine import calculate, dates, prepare, proposal
from .verify import verify

FORMAL_FREEZE = ROOT / "evidence/task06/formal/freeze_v001"


def tree_hashes(root, exclude=()):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "INVALID_EVIDENCE_DIRECTORY")
    result = {}
    for p in sorted(root.rglob("*")):
        require(not p.is_symlink(), "EVIDENCE_SYMLINK_REJECTED")
        name = p.relative_to(root).as_posix()
        if p.is_file() and name not in exclude:
            result[name] = sha(p)
    return result


def protect_tree(directory):
    for p in sorted(directory.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        p.chmod(0o555 if p.is_dir() else 0o444)
    directory.chmod(0o555)


def append_stage(directory, stage, **detail):
    import json
    with (directory / "stages.jsonl").open("a") as f:
        f.write(json.dumps({"at_utc": now(), "stage": stage, **detail}, allow_nan=False) + "\n")


def validate_model_approval(approval, plan_file, plan, selected, wall):
    require(approval["plan_sha256"] == sha(plan_file)
            and approval["model_id"] == selected, "MODEL_APPROVAL_BINDING_MISMATCH")
    require(plan == proposal(plan["primary"]), "MODEL_PLAN_CHANGED")
    require(utc(approval["approved_at"]) <= utc(wall), "MODEL_APPROVAL_FROM_FUTURE")
    require(selected in (plan["primary"], plan["fallback"]), "UNAPPROVED_MODEL_ID")
    if selected == plan["fallback"]:
        require(approval.get("fallback_reason"), "SIGNED_MODEL_FALLBACK_REASON_REQUIRED")


def run_candidate(*, directory, capture, plan_file, origin, mode, selected,
                  model_approval=None, data_approval=None, trust=None, trust_sha=None):
    started = now()
    tick = time.monotonic()
    directory.mkdir(parents=True, exist_ok=False)
    append_stage(directory, "GOAL_OBSERVE", origin=origin, mode=mode, model=selected)
    plan = read(plan_file)
    require(plan == proposal(plan["primary"]), "MODEL_PLAN_CHANGED")
    approval_proofs = {}
    if mode == "FORMAL":
        m = attest(*(model_approval or (None, None)), trust, trust_sha, "MODEL_APPROVAL")
        validate_model_approval(m, plan_file, plan, selected, started)
        d = attest(*(data_approval or (None, None)), trust, trust_sha, "DATA_REVIEW")
        require(d["plan_sha256"] == sha(plan_file) and d["model_id"] == selected,
                "DATA_REVIEW_WRONG_PLAN_OR_MODEL")
        _, baseline, _ = dates(origin, mode)
        check_clock(baseline.close.isoformat(), started, FORMAL_ORIGIN)
        require(utc(m["approved_at"]) <= utc(d["approved_at"]) <= utc(started), "REVIEW_SEQUENCE_INVALID")
        approval_proofs = {"model": m, "data": d}
    else:
        require(mode == "REHEARSAL" and selected in MODELS, "INVALID_REHEARSAL_MODE")
        d = None
    snapshot = prepare(capture, origin, mode, selected, plan, d)
    shutil.copyfile(plan_file, directory / "model_plan.json")
    write(directory / "input_snapshot.json", snapshot)
    append_stage(directory, "VALIDATE_DECIDE", qualification=snapshot["qualification"],
                 baseline_date=snapshot["baseline_date"], future_rows_excluded=snapshot["excluded_later_workbook_rows"],
                 reference_provider=snapshot["source_reference"].get("provider"))
    if mode == "FORMAL":
        for prefix, pair in (("model", model_approval), ("data", data_approval)):
            for suffix, p in zip(("approval.json", "approval.sig"), pair):
                shutil.copyfile(p, directory / f"{prefix}_{suffix}")
        shutil.copyfile(trust, directory / "owner_allowed_signers")
        (directory / "reference").mkdir()
        shutil.copyfile(capture / "qualification_evidence", directory / "qualification_evidence")
        shutil.copyfile(capture / "workbook_capture.json", directory / "workbook_capture.json")
        shutil.copyfile(capture / "workbook.xlsx", directory / "workbook.xlsx")
        shutil.copyfile(capture / "reference/capture_manifest.json", directory / "reference/capture_manifest.json")
        for rec in read(directory / "reference/capture_manifest.json"):
            p = rec["raw_file"]
            if p:
                require(Path(p).name == p, "INVALID_RAW_CAPTURE_FILENAME")
                shutil.copyfile(capture / "reference" / p, directory / "reference" / p)
                require(sha(directory / "reference" / p) == rec["sha256"], "RAW_CAPTURE_CHANGED_DURING_COPY")
        require(sha(directory / "workbook.xlsx") == snapshot["source"]["sha256"], "WORKBOOK_CHANGED_DURING_COPY")
        require(sha(directory / "qualification_evidence") == approval_proofs["data"]["qualification_evidence_sha256"],
                "QUALIFICATION_EVIDENCE_CHANGED_DURING_COPY")
        check_formal_candidate(directory, snapshot, plan, trust, trust_sha)
    append_stage(directory, "ACT", operation="fixed model refit")
    result = calculate(snapshot)
    result["input_snapshot_sha256"] = sha(directory / "input_snapshot.json")
    write(directory / "forecast_calculation.json", result)
    verification = verify(snapshot, result)
    write(directory / "independent_verification.json", verification)
    append_stage(directory, "VERIFY", tolerance=verification["tolerance_points"],
                 absolute_difference=verification["absolute_difference_points"])
    append_stage(directory, "RECOMMEND", point_2dp=result["point_2dp"],
                 status="PENDING_APPROVAL" if mode == "FORMAL" else "SIMULATION_ONLY")
    completed = now()
    if mode == "FORMAL":
        check_clock(started, completed, FORMAL_ORIGIN)
    write(directory / "execution.json", {"mode": mode, "started_at_utc": started,
                                          "completed_at_utc": completed, "elapsed_seconds": time.monotonic()-tick,
                                          "approval_status": "PENDING" if mode == "FORMAL" else "SIMULATION_ONLY",
                                          "owner_trust_sha256": trust_sha if mode == "FORMAL" else None})
    write(directory / "candidate_manifest.json", tree_hashes(directory))
    return result


def check_formal_candidate(directory, snapshot, plan, trust, trust_sha):
    m = attest(directory / "model_approval.json", directory / "model_approval.sig",
               trust, trust_sha, "MODEL_APPROVAL")
    d = attest(directory / "data_approval.json", directory / "data_approval.sig",
               trust, trust_sha, "DATA_REVIEW")
    validate_model_approval(m, directory / "model_plan.json", plan, snapshot["model_id"], now())
    require(d["plan_sha256"] == sha(directory / "model_plan.json") and d["model_id"] == snapshot["model_id"],
            "DATA_REVIEW_WRONG_PLAN_OR_MODEL")
    replayed = prepare(directory, snapshot["origin"], "FORMAL", snapshot["model_id"], plan, d)
    require(replayed == snapshot, "SIGNED_RAW_INPUTS_DO_NOT_RECONSTRUCT_SNAPSHOT")


def inspect_candidate(directory):
    manifest = read(directory / "candidate_manifest.json")
    require(tree_hashes(directory, ("candidate_manifest.json",)) == manifest, "CANDIDATE_HASH_MISMATCH")
    snap = read(directory / "input_snapshot.json")
    require(snap["model_code_sha256"] == code_hashes(), "MODEL_CODE_CHANGED_SINCE_RUN")
    result = read(directory / "forecast_calculation.json")
    require(result["input_snapshot_sha256"] == sha(directory / "input_snapshot.json"), "INPUT_HASH_MISMATCH")
    independent = read(directory / "independent_verification.json")
    require(verify(snap, result) == independent, "INDEPENDENT_VERIFICATION_MISMATCH")
    return snap, result


def approval_binding(candidate, result):
    return {"candidate_manifest_sha256": sha(candidate / "candidate_manifest.json"),
            "forecast_sha256": sha(candidate / "forecast_calculation.json"),
            "independent_verification_sha256": sha(candidate / "independent_verification.json"),
            "model_id": result["model_id"], "point_2dp": result["point_2dp"],
            "market": result["market"], "unit": result["unit"], "target": result["target"],
            "reference_definition": result["reference_definition"],
            "cutoff": result["cutoff"]}


def simulate_approval(candidate, output):
    snapshot, result = inspect_candidate(candidate)
    require(snapshot["mode"] == "REHEARSAL", "SIMULATION_CANNOT_APPROVE_FORMAL")
    record = {"mode": "REHEARSAL", "kind": "SIMULATED_APPROVAL",
              "approved_at": now(), "approver": "SIMULATED_TEST_HARNESS",
              **approval_binding(candidate, result)}
    write(output, record)
    return record


def verify_approval_binding(record, candidate, result):
    require(all(record.get(k) == v for k, v in approval_binding(candidate, result).items()),
            "FORECAST_APPROVAL_DOES_NOT_BIND_RESULT")


def freeze(candidate, output, mode, approval=None, signature=None, trust=None, trust_sha=None):
    # An absent or simulated signature fails even if no candidate directory exists.
    if mode == "FORMAL":
        approval_data = attest(approval, signature, trust, trust_sha, "FORECAST_APPROVAL")
        require(output.resolve() == FORMAL_FREEZE, "FORMAL_FREEZE_REQUIRES_CANONICAL_DESTINATION")
        check_clock(approval_data["approved_at"], now(), FORMAL_ORIGIN)
    else:
        require(mode == "REHEARSAL" and approval is not None, "SIMULATED_APPROVAL_REQUIRED")
        approval_data = read(approval)
        require(approval_data["mode"] == "REHEARSAL"
                and approval_data["kind"] == "SIMULATED_APPROVAL", "WRONG_APPROVAL_MODE")
    snapshot, result = inspect_candidate(candidate)
    require(snapshot["mode"] == mode, "RUN_MODE_MISMATCH")
    verify_approval_binding(approval_data, candidate, result)
    execution = read(candidate / "execution.json")
    require(utc(execution["completed_at_utc"]) <= utc(approval_data["approved_at"]) <= utc(now()),
            "FORECAST_APPROVAL_OUTSIDE_ACTUAL_TIMELINE")
    if mode == "FORMAL":
        require(execution["owner_trust_sha256"] == trust_sha, "OWNER_TRUST_ANCHOR_CHANGED")
        check_clock(execution["started_at_utc"], execution["completed_at_utc"], FORMAL_ORIGIN)
        check_formal_candidate(candidate, snapshot, read(candidate / "model_plan.json"), trust, trust_sha)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(candidate, output / "candidate")
    _, copied_result = inspect_candidate(output / "candidate")
    verify_approval_binding(approval_data, output / "candidate", copied_result)
    shutil.copyfile(approval, output / "human_approval.json")
    require(read(output / "human_approval.json") == approval_data, "APPROVAL_CHANGED_DURING_COPY")
    if mode == "FORMAL":
        shutil.copyfile(signature, output / "human_approval.sig")
        require(attest(output / "human_approval.json", output / "human_approval.sig",
                       trust, trust_sha, "FORECAST_APPROVAL") == approval_data, "APPROVAL_COPY_INVALID")
    identity = {**approval_binding(output / "candidate", copied_result), "mode": mode,
                "record_type": "FORMAL_FORECAST" if mode == "FORMAL" else "SIMULATED_FREEZE",
                "official_forecast": mode == "FORMAL", "frozen_at_utc": now()}
    write(output / "forecast_record.json", identity)
    write(output / "freeze_manifest.json", tree_hashes(output))
    # The completion receipt is the publication barrier. Interrupted directories are not valid freezes.
    if mode == "FORMAL":
        require(utc(now()) < utc(FORMAL_ORIGIN)-timedelta(seconds=1), "FREEZE_TOO_CLOSE_TO_CUTOFF")
    receipt = {"mode": mode, "freeze_manifest_sha256": sha(output / "freeze_manifest.json"),
               "committed_at_utc": now()}
    write(output / "COMMITTED.json", receipt)
    if mode == "FORMAL" and utc(now()) >= utc(FORMAL_ORIGIN):
        (output / "COMMITTED.json").unlink()
        raise ValueError("FREEZE_WRITE_CROSSED_CUTOFF")
    protect_tree(output)
    return receipt


def inspect_freeze(directory, expected_manifest=None):
    receipt = read(directory / "COMMITTED.json")
    found = sha(directory / "freeze_manifest.json")
    require(found == receipt["freeze_manifest_sha256"] and (not expected_manifest or found == expected_manifest),
            "FREEZE_MANIFEST_HASH_MISMATCH")
    require(tree_hashes(directory, ("freeze_manifest.json", "COMMITTED.json")) ==
            read(directory / "freeze_manifest.json"), "FROZEN_EVIDENCE_HASH_MISMATCH")
    snapshot, result = inspect_candidate(directory / "candidate")
    identity = read(directory / "forecast_record.json")
    require(identity["mode"] == snapshot["mode"] == receipt["mode"], "FROZEN_MODE_MISMATCH")
    approval = read(directory / "human_approval.json")
    if snapshot["mode"] == "FORMAL":
        require(identity["record_type"] == "FORMAL_FORECAST" and identity["official_forecast"] is True
                and approval["kind"] == "FORECAST_APPROVAL" and approval["mode"] == "FORMAL",
                "FORMAL_RECORD_CLASSIFICATION")
        check_clock(approval["approved_at"], identity["frozen_at_utc"], FORMAL_ORIGIN)
        check_clock(identity["frozen_at_utc"], receipt["committed_at_utc"], FORMAL_ORIGIN)
    else:
        require(identity["record_type"] == "SIMULATED_FREEZE" and identity["official_forecast"] is False
                and approval["kind"] == "SIMULATED_APPROVAL" and approval["mode"] == "REHEARSAL",
                "SIMULATION_RECORD_CLASSIFICATION")
    verify_approval_binding(identity, directory / "candidate", result)
    verify_approval_binding(approval, directory / "candidate", result)
    return snapshot, result, receipt


def replay(directory, expected_manifest):
    require(expected_manifest, "EXTERNAL_FREEZE_RECEIPT_HASH_REQUIRED")
    attempts = []
    def audit(event, args):
        if event.startswith("socket."):
            attempts.append(event)
            raise RuntimeError("OFFLINE_REPLAY_NETWORK_DENIED")
        if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
            attempts.append(event)
            raise RuntimeError("OFFLINE_REPLAY_PROCESS_DENIED")
    sys.addaudithook(audit)
    snapshot, original, receipt = inspect_freeze(directory, expected_manifest)
    replayed = calculate(snapshot)
    replayed["input_snapshot_sha256"] = original["input_snapshot_sha256"]
    require(encoded(replayed) == encoded(original), "OFFLINE_REPLAY_CALCULATION_CHANGED")
    require(not attempts, "OFFLINE_REPLAY_NETWORK_OR_PROCESS_ATTEMPT")
    return {"pass": True, "frozen_calculation_sha256": sha(directory / "candidate/forecast_calculation.json"),
            "freeze_manifest_sha256": receipt["freeze_manifest_sha256"],
            "point_2dp": replayed["point_2dp"], "mode": snapshot["mode"], "network_or_process_attempts": attempts}
