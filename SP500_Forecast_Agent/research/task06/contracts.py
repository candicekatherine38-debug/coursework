"""Execution identity, signed approval verification and local evidence integrity."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import hashlib
import importlib.metadata
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CAL = ROOT / "data/snapshots"
FORMAL_ORIGIN = "2026-11-13T12:00:00Z"
FORMAL_TARGET = "2026-11-20T21:00:00Z"
UNIT = "INDEX_POINTS"
MARKET = "SP500_PRICE_INDEX"
FORMAL_CANDIDATES = ("BLEND_VOL_LAD", "CORE_QUANTILE")
MODELS = (*FORMAL_CANDIDATES, "HISTORICAL_MEDIAN")
REFERENCE = "Official S&P Dow Jones Indices S&P 500 price-index close"
TOLERANCE = 1e-8
WORKBOOK_HASH = "1e24887e92ccf76b0513aaf66fdde1aa7173e977a904c5fa4f1a119a2bebc257"
CONFLICT_DAY = "2020-05-11"


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def utc(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(dt.tzinfo is not None, "NAIVE_TIMESTAMP")
    return dt.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def write(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encoded(value))


def read(path):
    return json.loads(Path(path).read_bytes())


def rounded(point):
    d = Decimal(str(point))
    require(d.is_finite() and d > 0, "INVALID_POINT")
    result = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    require(result > 0, "INVALID_ROUNDED_POINT")
    return str(result)


def check_clock(start, complete, cutoff):
    require(utc(start) <= utc(complete) < utc(cutoff), "CAPTURE_OR_EXECUTION_CROSSES_CUTOFF")


def versions():
    import platform
    return {"python": platform.python_version(), **{
        k: importlib.metadata.version(k) for k in ("numpy", "pandas", "scipy", "scikit-learn", "threadpoolctl")
    }}


def code_hashes():
    files = [p for p in HERE.rglob("*.py")]
    files += [HERE / "matched_origins.csv", HERE / "run.sh", HERE / "candidate_contracts.json"]
    files += list((ROOT / "src/sp500_agent").glob("*.py"))
    files += [CAL / "calendar_xnys_2016_2026.csv", CAL / "calendar_manifest.json"]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}


def attest(payload, signature, trust, trust_sha, kind):
    """Verify detached signatures from an owner-controlled external trust anchor."""
    require(payload and signature and trust and trust_sha, "HUMAN_APPROVAL_REQUIRED")
    require(sha(trust) == trust_sha, "OWNER_TRUST_ANCHOR_CHANGED")
    content = Path(payload).read_bytes()
    data = json.loads(content)
    require(data.get("kind") == kind and data.get("mode") == "FORMAL",
            "APPROVAL_KIND_OR_MODE_MISMATCH")
    result = subprocess.run(
        ["/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(trust), "-I", "product-owner",
         "-n", "sp500-task06", "-s", str(signature)],
        input=content, capture_output=True, timeout=10,
    )
    require(result.returncode == 0, "HUMAN_SIGNATURE_INVALID")
    require(utc(data["approved_at"]) < utc(FORMAL_ORIGIN), "APPROVAL_AFTER_CUTOFF")
    return data
