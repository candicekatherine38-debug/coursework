# Technical reproduction

Use Python and dependency versions from the model proposal generated with the complete inputs. Provision dependencies before offline replay. Run the commands from the package root.

Supply the authorised local inputs described in the README and generate a matching proposal before creating a rehearsal freeze. Use the authorised archived group workbook for the historical rehearsal. For a previously frozen calculation, use the exact code, configuration, calendar manifest and runtime that produced it.

```sh
runner=research/task06/run.sh
work=/path/to/new/local/rehearsal
plan=evidence/task06/model_proposal.json

"$runner" -m research.task06.cli capture-workbook \
  --workbook /path/to/authorised_archived_group_workbook.xlsx --output "$work/input"
"$runner" -m research.task06.cli run --mode REHEARSAL \
  --as-of 2025-11-07T12:00:00Z --model BLEND_VOL_LAD \
  --capture "$work/input" --plan "$plan" --output "$work/candidate"
"$runner" -m research.task06.cli simulate-approval \
  --candidate "$work/candidate" --output "$work/simulated_approval.json"
"$runner" -m research.task06.cli freeze --mode REHEARSAL \
  --candidate "$work/candidate" --approval "$work/simulated_approval.json" \
  --output "$work/frozen"
```

Save the freeze manifest digest from the successful freeze receipt independently. Pass that digest to the replay command.

```sh
"$runner" -m research.task06.cli replay \
  --frozen "$work/frozen" --expected-manifest SHA256_FROM_SAVED_RECEIPT \
  --output "$work/replay.json"
```

Replay refits the model from the frozen snapshot, requires a byte-identical calculation and rejects network and subprocess attempts. The workbook and frozen market data are supplied separately through an authorised route. A simulated approval applies only to the rehearsal. See the forecast-day runbook for formal inputs and approvals.
