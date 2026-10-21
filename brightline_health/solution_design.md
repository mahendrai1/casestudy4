# BrightLine HR Integration Health - Design

## Purpose and scope

This repository contains a working Python prototype for measuring the health of
five HR-related integrations from request logs. It ingests and normalizes events,
stores them locally, computes hourly integration and overall health, exposes the
results through an API, and presents them in a browser dashboard.

The local implementation is intended to demonstrate the proposed scoring and
operational workflow. The Azure resources and Event Hub function are production
directional scaffolding, not a deployed or validated cloud system.

## Architecture

```text
Sample JSON file or POST /api/ingest
                 |
                 v
       app.ingest (validate, normalize)
                 |
                 v
       app.masking (tokenize / redact)
                 |
                 v
         app.pipeline
           |          \
           v           v
   SQLite raw logs   app.engine
                         |
                         +--> hourly aggregates and scores --> SQLite
                         |
                         +--> app.alerts --> SQLite alert log
                                           (optional Teams webhook)

 Browser --> FastAPI (app.main) --> SQLite / engine --> JSON API
    |
    +--> app/static/index.html
```

### Main components

| Component | Responsibility |
| --- | --- |
| `app/ingest.py` | Validate required fields, map source integration names to configured keys, parse timestamps, derive failure/timeout flags, and build normalized rows. |
| `app/masking.py` | HMAC-tokenize customer identifiers and redact common PII/credential patterns from error text. |
| `app/pipeline.py` | Coordinate ingestion, persistence, hourly scoring, alert creation, and optional synthetic-history generation. |
| `app/engine.py` | Calculate statistics, baselines, integration scores/status, customer failure concentration, and overall roll-up. |
| `app/store.py` | Create and query SQLite tables for raw logs, hourly aggregates, and alerts; provide retention and trend operations. |
| `app/alerts.py` | Apply configured alert rules, deduplicate by window/integration/rule, and optionally send an Adaptive Card to a Teams webhook. |
| `app/main.py` | Serve the dashboard and FastAPI endpoints; load sample data on startup if the store has no aggregate windows. |
| `app/static/index.html` | Browser dashboard for hour and role selection, integration health, trend, request timeline, customer failures, and alerts. |
| `config/integrations.yaml` | Integration metadata, weights, SLA targets, baseline and score thresholds, role-visible data, and retention settings. |
| `scripts/run_pipeline.py` | Run the pipeline against a log file and print an hourly scorecard without requiring the API server. |

## Ingestion and data flow

1. Records come from `data/integration_logs.json` or the `POST /api/ingest`
   endpoint, which accepts a `logs` array.
2. A record must include `integration`, `timestamp`, `status`, and
   `latency_ms`. Unknown integrations, invalid timestamps, and negative
   latencies are rejected with an index and reason; valid records continue.
3. The timestamp is normalized to an ISO string and assigned to its hour.
   Source names are mapped to keys from the YAML configuration.
4. Customer IDs are converted to stable HMAC tokens; error text is checked for
   common email, card, SSN, phone, IP-address, and credential-like patterns.
   Missing customer IDs become `UNKNOWN`.
5. `app.pipeline` inserts clean records in `raw_logs`, groups them by hour,
   computes hourly integration results, writes aggregate and per-customer rows
   to `hourly_agg`, and records any new alerts.

Failure is currently derived from a timeout, any status other than `success`,
an HTTP status code of 500 or greater, or an application error code. A timeout
is a record explicitly marked `timeout` or with latency at least the configured
`timeout_ms`. In particular, statuses such as `error` are treated as failures
even if they have a non-5xx response code.

## Health calculation

- Request counts and failed counts determine availability. P95 latency is
  calculated from successful requests only; failures are accounted for in
  availability instead of inflating the latency percentile.
- With enough previous same-hour aggregates, the baseline uses up to the
  configured `baseline_days` (14 by default), their mean availability and mean
  P95 latency, and an availability band of mean plus/minus two standard
  deviations.
- If there are fewer than `min_history_days` (3 by default), the cold-start
  baseline uses the integration SLA target for availability and the current
  hour's median successful-request latency. The mode is exposed in API output.
- The integration score is 80% availability score and 20% latency score by
  default. Score curves, status thresholds, and weights are in
  `config/integrations.yaml`. Status is Healthy at 90+, At risk at 70-89, and
  Unhealthy below 70 with the default settings.
- The overall score is the weighted mean of available integration scores;
  weights are re-normalized when integrations are missing. If an integration
  configured with `red_rule` is Unhealthy, the overall status/colour is forced
  to Unhealthy/Red.
- The noisy-customer guard flags a known customer responsible for more than
  the configured share of failures and calculates the score without that
  customer's rows. If the source provides no customer ID, the guard reports
  itself unavailable.

Synthetic history is a demo feature used to illustrate the dynamic-baseline
path. It is marked in aggregate rows and in the displayed baseline mode, and
should not be interpreted as measured production history.

## API and dashboard

| Method and path | Purpose |
| --- | --- |
| `GET /` | Serve the dashboard page. |
| `GET /api/hours` | List scored windows. |
| `GET /api/config` | Return configuration, excluding the derived source-name lookup. |
| `GET /api/health` | Return an overall score and integration details for a window and configured view role. |
| `GET /api/trend/{integration}` | Return up to `n` aggregate trend points for an integration. |
| `POST /api/ingest` | Ingest and score a batch of log records. |
| `POST /api/admin/reload-sample` | Clear local data and reload the sample. |
| `POST /api/admin/seed-demo-history` | Add synthetic history and rescore loaded windows. |
| `POST /api/admin/purge` | Delete raw records older than the configured retention period. |

The API role parameter controls which data fields are included (for example,
timeline, customer breakdown, or raw sample). The roles are configuration-based
view filters only; the prototype does not authenticate callers or enforce
identity-provider roles.

## Persistence and deployment

The prototype uses SQLite in `data/health.db` by default; `HEALTH_DB` can
override the path. The raw-log table has an hour/integration index. Aggregate
rows are keyed by window, integration, and customer. Alert rows have a
uniqueness constraint to deduplicate the same rule for the same integration
and hour. Purging removes old raw rows; aggregates are retained for trends.

The Docker image runs the FastAPI app with Uvicorn and uses `/tmp/health.db`.
The Azure direction is sketched in `infra/terraform/main.tf`,
`infra/kql/baseline_and_aggregate.kql`, and
`infra/azure_function_mask/function_app.py`: Event Hubs, a masking/validation
function, Azure Data Explorer, and Container Apps. Those files explicitly
remain incomplete/unvalidated; for example, the function has no ADX or
dead-letter write implementation.

## Operational and security considerations

- Set `MASK_SECRET` to a deployment-specific secret. The built-in
  `dev-only-change-me` value is only a development default; changing it changes
  customer tokens.
- Configure `TEAMS_WEBHOOK_URL` only in a secured environment. Without it,
  alerts are stored as dry-run records. Webhook delivery and cloud alert
  routing are not covered by the local tests.
- The admin endpoints and all API routes currently lack authentication and
  authorization. Role-based response shaping is not an access-control
  boundary; do not expose this prototype as a production service.
- Masking uses regular-expression patterns and should not be considered a
  comprehensive data-loss-prevention guarantee. Validate the source schema
  and privacy controls against real data before deployment.
- SLA targets, score curves, escalation interpretation, and integration
  weights are configurable assumptions and need product/operations sign-off.
- The sample data covers only one hour. A real 14-day baseline cannot be
  established from it; synthetic demo history is not a substitute for observed
  history.
