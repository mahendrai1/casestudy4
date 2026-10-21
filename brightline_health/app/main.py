"""Health API + dashboard (FastAPI). Run: uvicorn app.main:app --reload"""
import os
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import store, pipeline
from .config import ROOT, load_config
from .engine import compute_hour

cfg = load_config()
DB_PATH = os.environ.get("HEALTH_DB", str(ROOT / "data" / "health.db"))
conn = store.connect(DB_PATH)
app = FastAPI(title="BrightLine HR - API Integration Health", version="0.1-prototype")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
def _seed():
    if not store.hours(conn):
        pipeline.load_file(conn, ROOT / "data" / "integration_logs.json", cfg)


def _resolve(hour):
    hs = store.hours(conn)
    if not hs:
        raise HTTPException(404, "No data ingested yet")
    if hour is None:
        return hs[0]
    if hour not in hs:
        raise HTTPException(404, f"No data for {hour}")
    return hour


def _view(hour):
    rows = store.raw_for_hour(conn, hour)
    days = cfg["settings"]["baseline_days"]
    agg, per, extra, overall = compute_hour(rows, lambda k: store.history(conn, k, hour, days), cfg)
    return rows, per, extra, overall


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/hours")
def hours():
    return store.hours(conn)


@app.get("/api/config")
def get_config():
    return {k: v for k, v in cfg.items() if k != "by_source_name"}


@app.get("/api/health")
def health(hour: str | None = None, role: str = Query("operations")):
    if role not in cfg["roles"]:
        raise HTTPException(400, f"unknown role {role}")
    see = cfg["roles"][role]["see"]
    hour = _resolve(hour)
    rows, per, extra, overall = _view(hour)
    out = {"window_start": hour, "role": role, "overall": overall, "integrations": []}
    for key, icfg in cfg["integrations"].items():
        if key not in per:
            continue
        r = per[key]
        item = {"key": key, "display": icfg["display"], "weight": icfg["weight"],
                "sla_tier": icfg["sla_tier"], "sla_target": icfg["sla_target"],
                "red_rule": icfg["red_rule"], "score": r["score"], "status": r["status"],
                "availability": r["availability"], "p95_latency_ms": r["p95_latency_ms"]}
        if "baseline" in see:
            item.update(baseline_mode=r["baseline_mode"], base_availability=r["base_availability"],
                        base_p95_ms=r["base_p95_ms"], avail_score=r["avail_score"],
                        latency_score=r["latency_score"], band=[r["band_lo"], r["band_hi"]])
        if "customers" in see:
            item["failure_breakdown"] = {"total": r["total"], "failed": r["failed"],
                                         "timeouts": r["timeouts"], **extra[key]}
        if "timeline" in see:
            item["timeline"] = [{"ts": x["ts"], "latency_ms": x["latency_ms"],
                                 "failed": x["failed"], "http_status": x["http_status"],
                                 "timeout": x["is_timeout"]} for x in rows if x["integration"] == key]
        if "raw" in see:
            item["raw_sample"] = [x for x in rows if x["integration"] == key][:5]
        out["integrations"].append(item)
    if "alerts" in see:
        out["alerts"] = [dict(r) for r in conn.execute(
            "SELECT created_at,window_start,integration,rule,severity,message,channel,delivered "
            "FROM alerts WHERE window_start=? ORDER BY id", (hour,))]
    return out


@app.get("/api/trend/{integration}")
def trend(integration: str, hour: str | None = None, n: int = 24):
    if integration not in cfg["integrations"]:
        raise HTTPException(404, "unknown integration")
    return store.trend(conn, integration, _resolve(hour), n)


@app.post("/api/ingest")
def ingest_logs(payload: dict = Body(...)):
    """Accepts {"logs":[...]} in the sample schema. Schema-checked, masked, scored, alerted."""
    return pipeline.process_records(conn, payload.get("logs", []), cfg)


@app.post("/api/admin/reload-sample")
def reload_sample():
    store.reset(conn)
    return pipeline.load_file(conn, ROOT / "data" / "integration_logs.json", cfg)


@app.post("/api/admin/seed-demo-history")
def seed_history():
    """DEMO: add synthetic 14-day history, then re-score the loaded hour(s) with a dynamic baseline."""
    hs = store.hours(conn)
    if not hs:
        raise HTTPException(404, "load data first")
    n = pipeline.seed_synthetic_history(conn, cfg, hs[-1])
    days = cfg["settings"]["baseline_days"]
    for w in hs:
        rows = store.raw_for_hour(conn, w)
        agg, *_ = compute_hour(rows, lambda k: store.history(conn, k, w, days), cfg)
        store.save_agg(conn, agg)
    return {"synthetic_rows": n, "rescored_windows": hs}


@app.post("/api/admin/purge")
def purge():
    return {"deleted_raw_rows": store.purge(conn, datetime.now(timezone.utc).replace(tzinfo=None), cfg["settings"]["retention_days"])}
