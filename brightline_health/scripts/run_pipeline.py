"""CLI: python scripts/run_pipeline.py [logs.json] [--demo-history]  -> prints the scorecard."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import pipeline, store
from app.config import load_config
from app.engine import compute_hour

cfg = load_config()
args = [a for a in sys.argv[1:] if not a.startswith("--")]
path = args[0] if args else Path(__file__).resolve().parent.parent / "data" / "integration_logs.json"
conn = store.connect(":memory:")
res = pipeline.load_file(conn, path, cfg, demo_history="--demo-history" in sys.argv)
print(f"ingested={res['ingested']} rejected={len(res['rejected'])}")
for w in res["windows"]:
    rows = store.raw_for_hour(conn, w["window"])
    _, per, _, overall = compute_hour(rows, lambda k: store.history(conn, k, w["window"], 14), cfg)
    print(f"\nWindow {w['window']}  OVERALL {overall['score']} {overall['status']} ({overall['colour']})"
          f"  red-rule: {overall['red_rule_triggered_by'] or '-'}")
    for k, r in per.items():
        print(f"  {cfg['integrations'][k]['display']:<14} score {r['score']:>5} {r['status']:<9} "
              f"avail {r['availability']:>6}%  p95 {r['p95_latency_ms']} ms  [{r['baseline_mode']}]")
    for a in w["new_alerts"]:
        print("  ALERT:", a)
