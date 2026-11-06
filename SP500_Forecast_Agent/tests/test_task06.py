import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from research.task06.contracts import (FORMAL_ORIGIN, attest, check_clock, rounded, sha, utc)
from research.task06.engine import dates, proposal, validate_prices
from research.task06.pipeline import freeze, tree_hashes
from research.task06.verify import core_factors


class Task06GateTests(unittest.TestCase):
    def test_november_calendar_contract(self):
        _, baseline, target = dates(FORMAL_ORIGIN, "FORMAL")
        self.assertEqual(baseline.day.isoformat(), "2026-11-12")
        self.assertEqual(target.close, utc("2026-11-20T21:00:00Z"))

    def test_dst_changes_historical_origin_utc(self):
        self.assertEqual(dates("2025-10-31T11:00:00Z", "REHEARSAL")[1].day.isoformat(), "2025-10-30")
        self.assertEqual(dates("2025-11-07T12:00:00Z", "REHEARSAL")[1].day.isoformat(), "2025-11-06")
        with self.assertRaisesRegex(ValueError, "FRIDAY_0700"):
            dates("2025-11-07T11:00:00Z", "REHEARSAL")

    def test_reserved_2026_rehearsal_rejected(self):
        with self.assertRaisesRegex(ValueError, "NO_NEW_2026"):
            dates(FORMAL_ORIGIN, "REHEARSAL")

    def test_capture_clock_requires_completion_strictly_before_cutoff(self):
        check_clock("2026-11-13T11:00:00Z", "2026-11-13T11:59:59Z", FORMAL_ORIGIN)
        with self.assertRaisesRegex(ValueError, "CROSSES_CUTOFF"):
            check_clock("2026-11-13T11:00:00Z", FORMAL_ORIGIN, FORMAL_ORIGIN)
        with self.assertRaisesRegex(ValueError, "CROSSES_CUTOFF"):
            check_clock("2026-11-13T11:50:00Z", "2026-11-13T11:49:00Z", FORMAL_ORIGIN)

    def test_stale_future_and_nonfinite(self):
        cal, base, _ = dates("2025-11-07T12:00:00Z", "REHEARSAL")
        for rows, error in [({"2025-11-05": 100.}, "STALE_BASELINE"),
                            ({"2025-11-07": 100.}, "POST_BASELINE"),
                            ({"2025-11-06": float("inf")}, "INVALID_PRICE"),
                            ({"2025-11-06": 100.}, "MISSING_OR_NONTRADING")]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                validate_prices(rows, cal, base)

    def test_two_fixed_candidate_contracts(self):
        plan = proposal("BLEND_VOL_LAD")
        self.assertEqual(plan["fallback"], "CORE_QUANTILE")
        self.assertEqual(plan["status"], "PROPOSED_NOT_LOCKED")
        self.assertEqual(len(plan["model_contracts"]["candidates"]), 3)
        with self.assertRaisesRegex(ValueError, "UNKNOWN_MODEL"):
            proposal("NEW_MODEL")

    def test_no_approval_cannot_create_formal_directory(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "never_created"
            with self.assertRaisesRegex(ValueError, "HUMAN_APPROVAL_REQUIRED"):
                freeze(p, p, "FORMAL")
            self.assertFalse(p.exists())

    def test_approval_signature_and_tamper_detection(self):
        # Ephemeral synthetic key; this does not establish an actual owner identity.
        with TemporaryDirectory() as tmp:
            p = Path(tmp)
            key = p / "test-key"
            trust = p / "allowed"
            signed = p / "data.json"
            subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                           check=True, capture_output=True)
            trust.write_text("product-owner " + (p / "test-key.pub").read_text())
            fixture = {"kind": "MODEL_APPROVAL", "mode": "FORMAL", "approved_at": "2026-11-01T00:00:00Z",
                       "classification": "SYNTHETIC_CRYPTOGRAPHIC_TEST_ONLY"}
            signed.write_text(json.dumps(fixture))
            subprocess.run(["/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "sp500-task06",
                            str(signed)], check=True, capture_output=True)
            sig = p / "data.json.sig"
            self.assertEqual(attest(signed, sig, trust, sha(trust), "MODEL_APPROVAL"), fixture)
            with self.assertRaisesRegex(ValueError, "TRUST_ANCHOR_CHANGED"):
                attest(signed, sig, trust, "0"*64, "MODEL_APPROVAL")
            signed.write_text(json.dumps({**fixture, "approved_at": "2026-11-02T00:00:00Z"}))
            with self.assertRaisesRegex(ValueError, "HUMAN_SIGNATURE_INVALID"):
                attest(signed, sig, trust, sha(trust), "MODEL_APPROVAL")

    def test_simulation_cannot_be_formal_approval(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp)
            approval = p / "simulated.json"
            approval.write_text(json.dumps({"kind": "SIMULATED_APPROVAL", "mode": "REHEARSAL"}))
            with self.assertRaisesRegex(ValueError, "APPROVAL_KIND_OR_MODE"):
                attest(approval, approval, approval, sha(approval), "FORECAST_APPROVAL")

    def test_evidence_path_and_rounding(self):
        self.assertEqual(rounded(100.125), "100.13")
        with self.assertRaisesRegex(ValueError, "INVALID_POINT"):
            rounded(float("nan"))
        with TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "outside").symlink_to(Path(__file__))
            with self.assertRaisesRegex(ValueError, "SYMLINK"):
                tree_hashes(p)

    def test_constant_prices_independent_core(self):
        self.assertEqual(core_factors([100.] * 21), {"mom20": 0., "rv20": 0.})


if __name__ == "__main__":
    unittest.main()
