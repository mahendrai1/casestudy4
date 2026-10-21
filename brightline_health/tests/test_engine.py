import json, sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import pipeline, store, masking
from app.config import load_config, ROOT
from app.engine import compute_hour, percentile
from app.ingest import normalise

cfg = load_config()


def run(path=ROOT / "data" / "integration_logs.json"):
    conn = store.connect(":memory:")
    pipeline.load_file(conn, path, cfg)
    w = store.hours(conn)[0]
    rows = store.raw_for_hour(conn, w)
    return conn, compute_hour(rows, lambda k: store.history(conn, k, w, 14), cfg)


def test_all_rows_ingested_and_availability_matches_hand_count():
    _, (agg, per, extra, overall) = run()
    # hand-counted from integration_logs.json (12 requests per integration)
    assert {k: (v["total"], v["failed"]) for k, v in per.items()} == {
        "payroll": (12, 5), "directory": (12, 1), "esignature": (12, 4),
        "ats": (12, 6), "notifications": (12, 1)}
    assert per["ats"]["timeouts"] == 5 and per["ats"]["availability"] == 50.0


def test_status_and_red_rule():
    _, (_, per, _, overall) = run()
    assert per["payroll"]["status"] == "Unhealthy"
    assert per["directory"]["status"] == "At risk"
    assert overall["colour"] == "Red" and overall["red_rule_triggered_by"] == ["payroll"]


def test_weights_sum_and_weighted_score():
    _, (_, per, _, overall) = run()
    exp = sum(cfg["integrations"][k]["weight"] * v["score"] for k, v in per.items())
    assert abs(overall["score"] - exp) < 0.1


def test_timeouts_do_not_inflate_p95():
    _, (_, per, _, _) = run()
    assert per["ats"]["p95_latency_ms"] < 300


def test_red_rule_not_triggered_when_only_non_critical_unhealthy(tmp_path):
    logs = json.load(open(ROOT / "data" / "integration_logs.json"))["logs"]
    for r in logs:
        if r["integration"] in ("ADP Payroll Sync", "Workday Directory Sync"):
            r["status"], r["status_code"] = "success", 200
    p = tmp_path / "l.json"; p.write_text(json.dumps({"logs": logs}))
    _, (_, per, _, overall) = run(p)
    assert per["ats"]["status"] == "Unhealthy" and overall["colour"] != "Red"


def test_noisy_customer_guard_flags_dominant_customer():
    rows = []
    for i in range(10):
        rows.append(dict(ts=f"2026-08-04T09:{i:02d}:00", hour_start="2026-08-04T09:00:00",
                         customer_id="TNT-A" if i < 8 else "TNT-B", integration="payroll",
                         latency_ms=200, is_timeout=0, failed=1 if i in (0, 1, 2, 3, 4, 8) else 0,
                         http_status=500))
    _, per, extra, _ = compute_hour(rows, lambda k: [], cfg)
    assert extra["payroll"]["flagged"] == "TNT-A"
    assert extra["payroll"]["score_without_customer"] > per["payroll"]["score"]


def test_dynamic_baseline_used_with_history():
    conn = store.connect(":memory:")
    pipeline.seed_synthetic_history(conn, cfg, "2026-08-04T09:00:00")
    pipeline.load_file(conn, ROOT / "data" / "integration_logs.json", cfg)
    w = store.hours(conn)[0]
    rows = store.raw_for_hour(conn, w)
    _, per, _, _ = compute_hour(rows, lambda k: store.history(conn, k, w, 14), cfg)
    assert "14-day" in per["payroll"]["baseline_mode"]


def test_cli_demo_history_uses_input_window(tmp_path):
    data = json.loads((ROOT / "data" / "integration_logs.json").read_text())
    for record in data["logs"]:
        record["timestamp"] = record["timestamp"].replace(
            "2026-08-04T09:", "2026-09-05T10:"
        )
    path = tmp_path / "logs.json"
    path.write_text(json.dumps(data))

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"),
         str(path), "--demo-history"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )

    assert "Window 2026-09-05T10:00:00" in result.stdout
    assert "[14-day same-hour (synthetic demo history)]" in result.stdout


def test_schema_rejects_bad_records():
    assert normalise({"integration": "Nope", "timestamp": "2026-08-04T09:00:00",
                      "status": "success", "latency_ms": 1}, cfg)[0] is None
    assert normalise({"integration": "ADP Payroll Sync", "timestamp": "bad",
                      "status": "success", "latency_ms": 1}, cfg)[0] is None


def test_masking():
    assert "@" not in masking.mask_text("fail for jane.doe@acme.com from 10.1.2.3")
    assert masking.tokenise("C-1") == masking.tokenise("C-1") != masking.tokenise("C-2")
    assert "C-1" not in masking.tokenise("C-1")


def test_alert_dedup_and_retention():
    conn, (agg, per, extra, overall) = run()
    from app import alerts
    w = "2026-08-04T09:00:00"
    a = alerts.build_alerts(w, per, overall, cfg)
    assert len(a) == 4
    n1 = conn.execute("SELECT count(*) FROM alerts").fetchone()[0]
    assert n1 == 4          # pipeline already raised them once
    alerts.dispatch(conn, w, a)
    assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == n1
    from datetime import datetime
    assert store.purge(conn, datetime(2026, 9, 1), 60) == 0
    assert store.purge(conn, datetime(2026, 10, 19), 60) == 60


def test_percentile():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
