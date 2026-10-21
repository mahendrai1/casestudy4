"""Ingest -> store -> hourly health engine -> alerts."""
import json
import random
from datetime import datetime, timedelta
from . import store, alerts
from .engine import compute_hour
from .ingest import ingest


def process_records(conn, records, cfg, send_alerts=True, demo_history=False):
    clean, rejected = ingest(records, cfg)
    if demo_history and clean:
        seed_synthetic_history(conn, cfg, min(r["hour_start"] for r in clean))
    store.insert_raw(conn, clean)
    windows = sorted({r["hour_start"] for r in clean})
    summary = []
    for w in windows:
        rows = store.raw_for_hour(conn, w)
        days = cfg["settings"]["baseline_days"]
        agg, per, extra, overall = compute_hour(rows, lambda k: store.history(conn, k, w, days), cfg)
        store.save_agg(conn, agg)
        new = alerts.dispatch(conn, w, alerts.build_alerts(w, per, overall, cfg)) if send_alerts else []
        summary.append({"window": w, "overall": overall, "new_alerts": new})
    return {"ingested": len(clean), "rejected": rejected, "windows": summary}


def load_file(conn, path, cfg, demo_history=False):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return process_records(
        conn, data["logs"] if isinstance(data, dict) else data, cfg,
        demo_history=demo_history,
    )


def seed_synthetic_history(conn, cfg, before_hour, seed=7):
    """DEMO ONLY: 14 days of plausible healthy same-hour aggregates so the 14-day
    baseline path can be shown. Flagged synthetic=1 and never mixed with real data."""
    rnd = random.Random(seed)
    base_lat = {"payroll": 225, "directory": 120, "esignature": 680, "ats": 200, "notifications": 138}
    h0 = datetime.fromisoformat(before_hour)
    rows = []
    for d in range(1, cfg["settings"]["baseline_days"] + 1):
        for hr in range(24):
            t = (h0 - timedelta(days=d)).replace(hour=hr)
            for key, icfg in cfg["integrations"].items():
                total = 12
                av = min(100.0, max(95.0, rnd.gauss(99.7, 0.35)))
                failed = round(total * (100 - av) / 100)
                av = round(100.0 * (total - failed) / total, 3)
                p95 = round(base_lat[key] * rnd.uniform(1.0, 1.15), 1)
                rows.append({"window_start": t.isoformat(), "integration": key, "customer_id": "*",
                             "total": total, "failed": failed, "timeouts": 0, "availability": av,
                             "p95_latency_ms": p95, "base_availability": None, "base_p95_ms": None,
                             "baseline_mode": "synthetic", "avail_score": None, "latency_score": None,
                             "score": 100.0 if failed == 0 else 90.0, "status": "Healthy", "synthetic": 1})
    store.save_agg(conn, rows)
    return len(rows)
