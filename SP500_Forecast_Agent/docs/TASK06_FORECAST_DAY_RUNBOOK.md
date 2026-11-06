# TASK 06 forecast-day operations

The formal target is the official S&P Dow Jones Indices S&P 500 price-index close on 20 November 2026 at 21:00 UTC. The point is in index points. Complete calculation, owner approval and freeze strictly before 13 November 2026 at 12:00 UTC. The coursework submission deadline is 17:00 UTC. The latest expected baseline is the 12 November cash close at 21:00 UTC, subject to calendar and source checks.

TASK 06 authorises rehearsal. Actual formal activation requires the owner's signed approval records and qualifying source inputs. The owner asked to retain BLEND_VOL_LAD as the primary candidate. CORE_QUANTILE is the manual backup. The historical median is a control. The fixed original matched training cohort, equations and parameters remain in the model plan.

## Before 13 November

1. Generate the model proposal as shown in the README and review the plan in `evidence/task06/model_proposal.json`. Confirm the selected model, fixed cohort, versions, data requirements and fallback. Record approval on the exact plan hash. Complete owner signing setup and a separate signature verification before market day.
2. Verify access to an authorised Bloomberg `SPX Index / PX_LAST` workbook export with cached values and complete daily coverage through the last cash session. Establish a permissible local-use and eventual coursework reproduction route. Keep raw market data local unless the applicable rights are established.
3. Verify the exchange calendar and UTC clock against a reliable time source. Keep time-check evidence. Confirm there are no changes to the scheduled close. Any calendar change requires review and requalification before execution.
4. Resolve historical timing and revisions evidence. The project uses the next-calendar-day 06:30 New York upper-bound assumption for historical weekly rows. The reviewed evidence must support this conservative assumption and the vintage used. Keep per-row first-publication times UNKNOWN when unknown. Preserve the 11 May 2020 conflict and legacy exclusion mask.
5. Test availability of the corroborating close at the required early-morning time. Verify the selected source at the relevant time of day. A missing fresh corroborator is a stop condition.

## Capture and source review

Run commands from the IFTE0007_SP500_AGENT root. A fresh run directory is required for each capture. Use names specific to the run.

```sh
runner=research/task06/run.sh
work=evidence/task06/formal/session_001
plan=evidence/task06/model_proposal.json

"$runner" -m research.task06.cli capture-workbook \
  --workbook /path/to/authorised_bloomberg_export.xlsx --output "$work/input"
"$runner" -m research.task06.cli capture --output "$work/input/reference"
"$runner" -m research.task06.cli source-check --capture "$work/input/reference" \
  --output "$work/source_qualification.json"
```

Keep export provenance and the qualification evidence as `$work/input/qualification_evidence`. The copy receipt captures actual local receipt time. The data reviewer records the supported export time, confirms the completed baseline and compares the reference against the workbook. Use FRED SP500 first. Yahoo ^GSPC can corroborate only if FRED is unavailable or stale and the owner signs the reason. A numeric discrepancy above 0.01 index points on an overlapping session in the latest 63-session price window stops the run, including disagreement from a stale reference. Diagnose authentication, rate limiting, transport, schema and staleness separately. S&P DJI's definition and official marking source are distinct from the available numerical feeds.

If both public requests fail, preserve their receipts and stop. A model fallback does not relax the source requirements. No earlier close or alternative asset substitutes for the required session.

## Owner records and signatures

The owner controls the signing key and checks the payload before signing. Create a trusted allowed-signers file containing the principal `product-owner` and the owner's public SSH key. Keep its SHA-256 independently with the owner. The agent accepts the file with this independently supplied pin. The code contains no real approval signing operation. All approval clocks are the actual UTC approval times.

| Signed JSON kind | Required values in addition to `kind`, `mode: FORMAL`, `approved_at` |
|---|---|
| MODEL_APPROVAL | `plan_sha256`, `model_id`, and `fallback_reason` if approving the fallback |
| DATA_REVIEW | `plan_sha256`, `model_id`, `workbook_sha256`, `workbook_capture_sha256`, `reference_manifest_sha256`, `qualification_evidence_sha256`, `baseline_date`, `workbook_exported_at_utc`, `history_qualification: ASSUMED_AVAILABLE_WITH_EVIDENCE`, `approved_history_and_vintage_review: true`, `approved_calendar_review: true`, `approved_clock_review: true`, `approved_local_use: true`, `evidence_summary`, `reference_provider`, and `downgrade_reason` for Yahoo |
| FORECAST_APPROVAL | `candidate_manifest_sha256`, `forecast_sha256`, `independent_verification_sha256`, `model_id`, `point_2dp`, `market`, `unit`, `target`, `reference_definition`, `cutoff` |

Hash the exact files with `shasum -a 256`. The owner signs each reviewed JSON outside the agent process, for example using `ssh-keygen -Y sign -f /path/to/owner_key -n sp500-task06 model_approval.json`. The generated detached signature is `model_approval.json.sig`. Provide only the signed payload, signature, public allowed-signers file and pin to the runner.

## Execute, inspect and freeze

```sh
"$runner" -m research.task06.cli run --mode FORMAL \
  --as-of 2026-11-13T12:00:00Z --model BLEND_VOL_LAD \
  --capture "$work/input" --plan "$plan" --output "$work/candidate" \
  --model-approval "$work/model_approval.json" "$work/model_approval.json.sig" \
  --data-approval "$work/data_review.json" "$work/data_review.json.sig" \
  --owner-signers "$work/owner_allowed_signers" --owner-signers-sha256 "$owner_signers_pin"

"$runner" -m research.task06.cli freeze --mode FORMAL --candidate "$work/candidate" \
  --approval "$work/forecast_approval.json" --signature "$work/forecast_approval.json.sig" \
  --owner-signers "$work/owner_allowed_signers" --owner-signers-sha256 "$owner_signers_pin" \
  --output evidence/task06/formal/freeze_v001
```

Between commands, the owner reviews source clocks and qualification, baseline, factors, fitted coefficients, return, point and independent verification. Review the exact candidate hashes before signing the forecast approval. If a model fails, preserve that directory. Before the cutoff, the owner may approve the named fallback with a reason and new signed data review bound to that selection. Use a new candidate directory. The formal freeze accepts one canonical directory and rejects an overwrite. Any correction after successful formal freeze requires separate review and an explicitly authorised version procedure before the cutoff.

A freeze is valid only with its `COMMITTED.json` receipt and matching manifest. Preserve the manifest digest separately. An interrupted directory is evidence of failure. Read-only permissions prevent ordinary writes. Integrity checks plus the separately retained receipt detect modifications. These are local controls, not a hardware WORM storage guarantee.

## Timing and replay

The public source capture uses parallel requests capped at 20 seconds each, plus a 24-second subprocess guard. Plan separately for human review, source publication and the workbook export.

Start availability checks on the prior evening after the cash close and plan a staffed review by 11:00 UTC on 13 November. Reserve at least 30 minutes for source issues and the two approval stages. Treat 11:30 UTC as a provisional operational checkpoint. If remaining time cannot cover qualification, calculation and owner review, stop. The hard cutoff gate remains 12:00 UTC with a final write margin.

After freeze, use the receipt's independently saved hash to replay. Replay uses the frozen normalized input, checks all files and blocks Python socket and subprocess operations. Keep the output outside the frozen directory.

```sh
"$runner" -m research.task06.cli replay --frozen evidence/task06/formal/freeze_v001 \
  --expected-manifest "$saved_freeze_digest" --output "$work/replay.json"
```

Prepare the technical submission evidence after a valid freeze. Match the approved two-decimal point in the final record and submission. Where source terms restrict distribution, use an authorised private reproduction route. A labelled sample demonstrates workflow only and cannot reproduce the exact real forecast. If the gates do not pass before 12:00 UTC, report BLOCKED with the actual reason.
