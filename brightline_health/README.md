# BrightLine HR - API Integration Health (working prototype)

Implements the solution deck ("API Integration Health", Case Study 4) against `data/integration_logs.json`.

## Run
Run these commands from the `brightline_health` directory:
```bash
cd brightline_health
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q tests                              # 12 tests
python scripts/run_pipeline.py                         # scorecard in the terminal
uvicorn app.main:app --reload                          # dashboard: http://127.0.0.1:8000
# or: docker build -t blhr-health . && docker run -p 8000:8000 blhr-health
```
Optional: `TEAMS_WEBHOOK_URL=<webhook>` sends real Teams alerts (default is dry-run, shown in the dashboard). `MASK_SECRET` sets the customer-id tokenisation key.

## How it maps to the deck
| Deck layer | Prototype | Production path (deck slide 5) |
|---|---|---|
| Ingest + schema check + PII masking | `app/ingest.py`, `app/masking.py` | Event Hubs + Azure Function (`infra/azure_function_mask`) |
| Store (60-day raw, hourly aggregates) | `app/store.py` (SQLite), `purge()` | Azure Data Explorer (`infra/kql`) |
| Health engine (baseline, score, roll-up, red rule, noisy-customer guard) | `app/engine.py` | Hourly timer job + KQL baselines |
| Serve (Health API, role views) | `app/main.py` (FastAPI) | FastAPI on Container Apps (`Dockerfile`) |
| Dashboard | `app/static/index.html` | Grafana (ops) / Power BI (execs) |
| Alerts | `app/alerts.py` (Teams adaptive card, dedupe) | Power Automate webhook |
| Config as code | `config/integrations.yaml` | same |

Scoring: 80% availability + 20% P95 latency vs same-hour 14-day baseline; Healthy 90+, At risk 70-89, Unhealthy <70; weights 40/25/15/15/5; Payroll or Directory Unhealthy forces Red.
The score maths is in the docstring of `app/engine.py`; thresholds live in the YAML.

Cost estimate: `docs/BrightLine_Cost_Estimate.xlsx` (formula-driven; change the blue cells).

## Things you should know (data gaps and interpretation choices)
* **The sample has one hour of data** (12 five-minute samples x 5 integrations). A 14-day same-hour baseline cannot exist, so the engine runs a **cold-start baseline** (availability vs SLA target, latency vs in-hour median) and labels it. The dashboard button "Demo 14-day baseline" adds **synthetic** history to show the dynamic path; it is flagged as synthetic everywhere.
* **No `customer_id`, `request_id`, `app_error_code` or `is_timeout` in the file.** Timeout is derived from `status == "timeout"`. The noisy-customer guard is implemented and unit-tested but shows "unavailable" on this data.
* **SLA targets** (99.9% HA, 99.5% BAU) and the availability/latency score curves are my assumptions - tune in YAML.
* P95 latency is computed over successful requests; failures/timeouts are penalised through availability to avoid double-counting 30 s timeouts.
* HTTP 429 (DocuSign) counts as a failure because the source marks it `error`.
* "20% below SLA target" is read as: availability < target x 0.8 (flagged on deck slide 9 as to-confirm).
* Role views (operations/support/executive/engineering) are filtered server-side but **not authenticated**; production uses Entra ID roles.
* `infra/terraform` and the Azure Function are **unvalidated skeletons**.
