"""Azure Function (Python v2 model): Event Hub trigger -> schema check + PII masking -> ADX.
SKELETON for the production path on deck slide 5. It reuses app/ingest.py + app/masking.py
unchanged. Not deployed or tested against Azure in this prototype.
Needs app settings: EVENTHUB_CONN, ADX_INGEST_URI, ADX_DATABASE, MASK_SECRET (Key Vault ref)."""
import json
import azure.functions as func
# from azure.kusto.data import KustoConnectionStringBuilder
# from azure.kusto.ingest import QueuedIngestClient, IngestionProperties
from app.config import load_config
from app.ingest import ingest

app = func.FunctionApp()
cfg = load_config()


@app.event_hub_message_trigger(arg_name="events", event_hub_name="request-logs",
                               connection="EVENTHUB_CONN", cardinality="many")
def mask_and_validate(events: list[func.EventHubEvent]):
    records = [json.loads(e.get_body().decode()) for e in events]
    clean, rejected = ingest(records, cfg)
    # TODO: queue `clean` to ADX table RawLogs (queued ingestion); send `rejected` to a dead-letter blob.
