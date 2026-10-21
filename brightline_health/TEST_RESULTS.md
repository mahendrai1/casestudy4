# Test Results

**Run date:** 2026-10-21
**Scope:** Local BrightLine Health prototype in this repository
**Environment:** Python 3.14.2; project dependencies available in the workspace
virtual environment

## Automated tests

| Check | Command | Result |
| --- | --- | --- |
| Project unit tests | `/workspaces/casestudy4/.venv/bin/python -m pytest -q brightline_health/tests` | **Passed** — 12 passed, 0 failed (0.23 s) |

The suite in `tests/test_engine.py` exercises:

- Sample ingestion totals, failure counts, availability, and timeout handling.
- Integration health statuses, weighted overall score, and critical red-rule
  behavior.
- P95 latency exclusion of failed requests and customer-concentration scoring.
- Dynamic baseline calculation using synthetic history.
- CLI handling of an alternate input window with demo history enabled.
- Rejection of invalid integration names and timestamps.
- Customer tokenization and PII masking.
- Alert deduplication and raw-log retention behavior.
- Percentile interpolation.

## Pipeline smoke check

**Command:** `cd brightline_health && python scripts/run_pipeline.py`
**Result:** **Passed** — ingested 60 sample records, rejected 0, and printed an
hourly scorecard for `2026-08-04T09:00:00`. The scorecard reported overall
health **49.9 / Unhealthy / Red**, with the Payroll red rule triggered, and
generated four alerts in dry-run mode.

## Coverage boundaries

These results verify the local test suite and sample CLI pipeline only. They do
not certify browser behavior, API authorization, actual Teams webhook delivery,
Docker image startup, Azure infrastructure deployment, Event Hubs processing,
or Azure Data Explorer ingestion. The cloud-facing files are documented as
incomplete/unvalidated scaffolding.

## Reproduction

From the repository root, with dependencies installed:

```bash
python -m pytest -q brightline_health/tests
cd brightline_health
python scripts/run_pipeline.py
```
