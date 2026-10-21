"""Health engine: aggregate -> dynamic baseline -> score -> status -> weighted roll-up.

Availability-led score (deck slide 3): 80% availability, 20% P95 latency, each
compared with a same-hour rolling baseline.  When fewer than `min_history_days`
of same-hour history exist (e.g. the first hours after go-live) we fall back to
a COLD-START baseline and say so in the output:
  availability baseline = SLA target
  latency baseline      = median latency of successful requests in the hour
P95 latency is computed over successful requests only; failures and timeouts
are already penalised through availability (avoids double-counting 30 s timeouts).
"""
import math
from statistics import mean, pstdev


def percentile(values, p):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def stats(rows):
    total = len(rows)
    failed = sum(r["failed"] for r in rows)
    ok_lat = [r["latency_ms"] for r in rows if not r["failed"]]
    return {
        "total": total,
        "failed": failed,
        "timeouts": sum(r["is_timeout"] for r in rows),
        "availability": round(100.0 * (total - failed) / total, 3) if total else None,
        "p95_latency_ms": round(percentile(ok_lat, 95), 1) if ok_lat else None,
        "median_ok_ms": percentile(ok_lat, 50),
    }


def status_for(score, s):
    if score >= s["healthy_min"]:
        return "Healthy"
    if score >= s["unhealthy_below"]:
        return "At risk"
    return "Unhealthy"


def baseline(stat, icfg, hist, s):
    """Return dict(base_availability, base_p95_ms, mode, band_lo, band_hi)."""
    if len(hist) >= s["min_history_days"]:
        av = [h["availability"] for h in hist]
        lat = [h["p95_latency_ms"] for h in hist if h["p95_latency_ms"]]
        sd = max(pstdev(av), 0.1)
        synthetic = all(h.get("synthetic") for h in hist)
        return {
            "base_availability": round(mean(av), 3),
            "base_p95_ms": round(mean(lat), 1) if lat else stat["median_ok_ms"],
            "mode": f"{len(hist)}-day same-hour" + (" (synthetic demo history)" if synthetic else ""),
            "band_lo": round(max(0, mean(av) - 2 * sd), 3),
            "band_hi": round(min(100, mean(av) + 2 * sd), 3),
        }
    return {
        "base_availability": icfg["sla_target"],
        "base_p95_ms": stat["median_ok_ms"],
        "mode": "cold-start (SLA target / in-hour median)",
        "band_lo": icfg["sla_target"],
        "band_hi": 100.0,
    }


def score_integration(stat, icfg, hist, s):
    if stat["total"] == 0:
        return None
    b = baseline(stat, icfg, hist, s)
    drop = max(0.0, b["base_availability"] - stat["availability"])
    avail_score = clamp(100 - drop * 100 / s["avail_zero_drop_pts"])
    if stat["p95_latency_ms"] is None or not b["base_p95_ms"]:
        lat_score = 0.0 if stat["p95_latency_ms"] is None else 100.0
    else:
        ratio = stat["p95_latency_ms"] / b["base_p95_ms"]
        lat_score = clamp(100 - (ratio - s["latency_ok_ratio"]) * 100
                          / (s["latency_zero_ratio"] - s["latency_ok_ratio"]))
    w = s["score_weights"]
    score = w["availability"] * avail_score + w["latency"] * lat_score
    return {
        **stat, **{k: b[k] for k in ("base_availability", "base_p95_ms")},
        "baseline_mode": b["mode"], "band_lo": b["band_lo"], "band_hi": b["band_hi"],
        "avail_score": round(avail_score, 1), "latency_score": round(lat_score, 1),
        "score": round(score, 1), "status": status_for(score, s),
    }


def noisy_customer(rows, key, icfg, hist, s):
    """Flag a customer causing > noisy_customer_share of an integration's failures."""
    total_failed = sum(r["failed"] for r in rows)
    by_c = {}
    for r in rows:
        by_c.setdefault(r["customer_id"], []).append(r)
    cust = []
    for c, crow in by_c.items():
        f = sum(r["failed"] for r in crow)
        cust.append({"customer_id": c, "failures": f,
                     "share": round(f / total_failed, 3) if total_failed else 0.0,
                     **{k: v for k, v in stats(crow).items() if k in ("total", "availability")}})
    cust.sort(key=lambda x: -x["failures"])
    known = [c for c in cust if c["customer_id"] != "UNKNOWN"]
    result = {"top_customers": cust[:5], "flagged": None, "score_without_customer": None,
              "note": None}
    if not known:
        result["note"] = "Source logs carry no customer_id; noisy-customer guard unavailable."
        return result
    top = cust[0]
    if total_failed and top["share"] > s["noisy_customer_share"] and top["customer_id"] != "UNKNOWN":
        rest = [r for r in rows if r["customer_id"] != top["customer_id"]]
        sc = score_integration(stats(rest), icfg, hist, s) if rest else None
        result["flagged"] = top["customer_id"]
        result["score_without_customer"] = sc["score"] if sc else None
    return result


def roll_up(per_integration, cfg):
    """Weighted sum of available integrations (weights re-normalised if some are missing)."""
    s = cfg["settings"]
    wsum = sum(cfg["integrations"][k]["weight"] for k in per_integration)
    overall = sum(cfg["integrations"][k]["weight"] * v["score"]
                  for k, v in per_integration.items()) / wsum if wsum else 0.0
    status = status_for(overall, s)
    red = [k for k, v in per_integration.items()
           if cfg["integrations"][k]["red_rule"] and v["status"] == "Unhealthy"]
    return {
        "score": round(overall, 1),
        "status": "Unhealthy" if red else status,
        "colour": "Red" if red else {"Healthy": "Green", "At risk": "Amber", "Unhealthy": "Red"}[status],
        "red_rule_triggered_by": red,
        "missing_integrations": [k for k in cfg["integrations"] if k not in per_integration],
    }


def compute_hour(rows, hist_lookup, cfg):
    """rows: raw rows for ONE hour. hist_lookup(integration)->history list.
    Returns (agg_rows_for_store, integration_results, overall)."""
    s = cfg["settings"]
    window = rows[0]["hour_start"] if rows else None
    per, extra, agg = {}, {}, []
    for key, icfg in cfg["integrations"].items():
        irows = [r for r in rows if r["integration"] == key]
        if not irows:
            continue
        hist = hist_lookup(key)
        res = score_integration(stats(irows), icfg, hist, s)
        per[key] = res
        extra[key] = noisy_customer(irows, key, icfg, hist, s)
        agg.append({"window_start": window, "integration": key, "customer_id": "*",
                    "synthetic": 0, **{k: res[k] for k in (
                        "total", "failed", "timeouts", "availability", "p95_latency_ms",
                        "base_availability", "base_p95_ms", "baseline_mode", "avail_score",
                        "latency_score", "score", "status")}})
        by_c = {}
        for r in irows:
            by_c.setdefault(r["customer_id"], []).append(r)
        for c, crow in by_c.items():
            st = stats(crow)
            agg.append({"window_start": window, "integration": key, "customer_id": c,
                        "total": st["total"], "failed": st["failed"], "timeouts": st["timeouts"],
                        "availability": st["availability"], "p95_latency_ms": st["p95_latency_ms"],
                        "base_availability": None, "base_p95_ms": None, "baseline_mode": None,
                        "avail_score": None, "latency_score": None, "score": None,
                        "status": None, "synthetic": 0})
    overall = roll_up(per, cfg) if per else None
    return agg, per, extra, overall
