"""Schema check + normalisation + masking of raw request logs."""
from datetime import datetime
from .masking import tokenise, mask_text


class SchemaError(ValueError):
    pass


REQUIRED = ("integration", "timestamp", "status", "latency_ms")


def parse_ts(s):
    try:
        return datetime.fromisoformat(s)
    except Exception as e:
        raise SchemaError(f"bad timestamp {s!r}") from e


def normalise(record, cfg):
    """Return (clean_row, None) or (None, reason).

    Failure definition (deck slide 3): application error code, exception,
    timeout, or HTTP 5xx.  We additionally treat any status='error' (e.g. 429)
    as an application error because the source flagged it as one.
    """
    for k in REQUIRED:
        if k not in record or record[k] is None:
            return None, f"missing field '{k}'"
    key = cfg["by_source_name"].get(record["integration"])
    if key is None:
        return None, f"unknown integration '{record['integration']}'"
    try:
        ts = parse_ts(record["timestamp"])
        latency = float(record["latency_ms"])
    except (SchemaError, TypeError, ValueError) as e:
        return None, str(e)
    if latency < 0:
        return None, "negative latency"
    status = record["status"]
    code = record.get("status_code")
    is_timeout = status == "timeout" or latency >= cfg["settings"]["timeout_ms"]
    app_error = record.get("app_error_code")
    failed = (
        is_timeout
        or status != "success"
        or (code is not None and int(code) >= 500)
        or bool(app_error)
    )
    return {
        "ts": ts.isoformat(),
        "hour_start": ts.replace(minute=0, second=0, microsecond=0).isoformat(),
        "customer_id": tokenise(record.get("customer_id")),
        "integration": key,
        "request_id": record.get("request_id"),
        "http_status": code,
        "app_error_code": app_error,
        "latency_ms": latency,
        "is_timeout": int(is_timeout),
        "failed": int(failed),
        "error_text": mask_text(record.get("error_text")),
    }, None


def ingest(records, cfg):
    clean, rejected = [], []
    for i, r in enumerate(records):
        row, why = normalise(r, cfg)
        if row:
            clean.append(row)
        else:
            rejected.append({"index": i, "reason": why})
    return clean, rejected
