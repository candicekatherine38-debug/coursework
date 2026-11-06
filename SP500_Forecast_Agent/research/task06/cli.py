"""Manual command line. Formal operations use real UTC and externally signed approvals."""
import argparse
from pathlib import Path

from .contracts import FORMAL_CANDIDATES, FORMAL_ORIGIN, MODELS, encoded, write
from .engine import proposal
from .pipeline import freeze, replay, run_candidate, simulate_approval
from .sources import capture, capture_workbook, check_capture


def main():
    parser = argparse.ArgumentParser(prog="forecast_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("capture", "source-check", "capture-workbook", "proposal", "run",
                 "simulate-approval", "freeze", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, required=True)
        if name == "source-check":
            p.add_argument("--capture", type=Path, required=True)
        elif name == "capture-workbook":
            p.add_argument("--workbook", type=Path, required=True)
        elif name == "proposal":
            p.add_argument("--primary", choices=FORMAL_CANDIDATES, default=FORMAL_CANDIDATES[0])
        elif name == "run":
            p.add_argument("--mode", choices=("REHEARSAL", "FORMAL"), required=True)
            p.add_argument("--as-of", default=FORMAL_ORIGIN)
            p.add_argument("--model", choices=MODELS, required=True)
            p.add_argument("--capture", type=Path, required=True)
            p.add_argument("--plan", type=Path, required=True)
            p.add_argument("--model-approval", nargs=2, type=Path)
            p.add_argument("--data-approval", nargs=2, type=Path)
            p.add_argument("--owner-signers", type=Path)
            p.add_argument("--owner-signers-sha256")
        elif name in ("simulate-approval", "freeze"):
            p.add_argument("--candidate", type=Path, required=True)
            if name == "freeze":
                p.add_argument("--mode", choices=("REHEARSAL", "FORMAL"), required=True)
                p.add_argument("--approval", type=Path)
                p.add_argument("--signature", type=Path)
                p.add_argument("--owner-signers", type=Path)
                p.add_argument("--owner-signers-sha256")
        elif name == "replay":
            p.add_argument("--frozen", type=Path, required=True)
            p.add_argument("--expected-manifest", required=True)
    a = parser.parse_args()
    if a.command == "capture":
        result = capture(a.output)
        result = [{k: x[k] for k in ("provider", "http_status", "returncode", "elapsed_seconds")} for x in result]
    elif a.command == "source-check":
        result = check_capture(a.capture)
        write(a.output, result)
    elif a.command == "capture-workbook":
        result = capture_workbook(a.workbook, a.output)
    elif a.command == "proposal":
        result = proposal(a.primary)
        write(a.output, result)
    elif a.command == "run":
        result = run_candidate(directory=a.output, capture=a.capture, plan_file=a.plan, origin=a.as_of,
                               mode=a.mode, selected=a.model, model_approval=a.model_approval,
                               data_approval=a.data_approval, trust=a.owner_signers, trust_sha=a.owner_signers_sha256)
        result = {k: result[k] for k in ("mode", "model_id", "origin", "baseline_date", "point_2dp", "unit")}
    elif a.command == "simulate-approval":
        result = simulate_approval(a.candidate, a.output)
    elif a.command == "freeze":
        result = freeze(a.candidate, a.output, a.mode, a.approval, a.signature,
                        a.owner_signers, a.owner_signers_sha256)
    else:
        result = replay(a.frozen, a.expected_manifest)
        write(a.output, result)
    print(encoded(result).decode(), end="")


if __name__ == "__main__":
    main()
