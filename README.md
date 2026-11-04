# S&P 500 Forecast Review

Python implementation of the fixed equal-weight Huber and volatility-scaled LAD model. The package contains the model, feature calculations, matched evaluation calendar, tests and run instructions.


[Download the source package](./SP500_GitHub_Code.zip) and extract it before following the steps below.

## Reproduce the historical review

1. Use Python 3.12 and install dependencies with `python -m pip install -r requirements.txt`.
2. Place the group-authorised `SP500 Historical Price Table (2004-2026).xlsx` in `data/`. The runner verifies its hash and reads the cached `PX_LAST` series for `SPX Index`.
3. Run `python run_review.py --output results_reproduced` from the package root. Choose a new output directory on subsequent runs.
4. Open `results_reproduced/review.html`. The folder also contains predictions, fitted coefficients, factors and an execution record.

Run `python -m unittest discover -s tests -v` after placing the workbook in `data/`. The standalone model tests can run without the workbook using `python -m unittest discover -s tests -p test_model.py -v`. Check the packaged files with `python verify_package.py`.

The calculation is offline once dependencies are available. Market prices are supplied separately by the group. This source package includes calendar dates and the matched-origin schedule. See `MODEL_CARD.md` for the equations, availability assumption and validation limits, and `REVIEW_CHECKLIST.md` for peer review. The review covers 104 historical origins in 2024–2025.

The optional `audit/independent_workbook_audit.py` script uses the dependencies in `requirements-audit.txt`. The calendar was generated from exchange_calendars 4.13.2. The Python factor code reproduces the close-derived Alpha158/Alpha360 formula families used in the original model.
