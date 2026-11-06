# S&P 500 Forecast Agent

Public source for the end-to-end coursework forecast. The primary candidate is BLEND_VOL_LAD, the fixed equal-weight Huber and volatility-scaled LAD model. Core Quantile is the separately approved manual backup. The workflow captures and qualifies sources, fits models with a training-only feature screen, independently verifies the calculation, enforces owner approval and the cutoff, freezes evidence, and replays the exact calculation offline.

## Set up

Use Python 3.12.14, a POSIX shell, curl and OpenSSH ssh-keygen. From this directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-task06.txt
research/task06/run.sh -m research.task06.cli --help
```

## Supply inputs locally

Obtain the following from the project owner through an authorised channel and keep them local:

- `data/snapshots/calendar_xnys_2016_2026.csv` and its `calendar_manifest.json`
- `research/task06/matched_origins.csv`
- The authorised group Bloomberg SPX Index PX_LAST workbook and, for exact replay of an existing result, its matching frozen evidence and runtime package

The public repository excludes market data, sample schedules, local evidence and private third-party materials. After supplying the first two sets of files, generate a proposal pinned to this checkout and run the tests. A historical run and exact replay additionally require their corresponding authorised inputs.

```sh
mkdir -p evidence/task06
research/task06/run.sh -m research.task06.cli proposal --output evidence/task06/model_proposal.json
research/task06/run.sh -m unittest discover -s tests -v
```

- [Agent instructions](docs/AGENT_WORKFLOW.md)
- [Forecast-day runbook](docs/TASK06_FORECAST_DAY_RUNBOOK.md)
- [Historical rehearsal and offline replay](docs/TASK06_REPRODUCE.md)
- [Candidate model and source policies](research/task06/candidate_contracts.json)

The model proposal requires owner approval for formal execution. Keep the market data, approvals, credentials, generated evidence and forecast outputs outside public version control.
