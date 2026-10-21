"""Alert rules + Teams webhook (deck slides 5, 7, 8). Dry-run unless TEAMS_WEBHOOK_URL is set."""
import json
import os
import urllib.request
from datetime import datetime, timezone


def build_alerts(window, per, overall, cfg):
    s = cfg["settings"]
    out = []
    for key, r in per.items():
        ic = cfg["integrations"][key]
        floor = ic["sla_target"] * (1 - s["escalate_below_sla_pct"] / 100)
        if r["availability"] < floor:
            out.append((key, "sla_breach_20pct", "critical",
                        f"{ic['display']}: availability {r['availability']:.1f}% is more than "
                        f"{s['escalate_below_sla_pct']}% below the {ic['sla_target']}% SLA target "
                        f"({ic['sla_tier']})", ic["alert_channel"]))
        elif r["status"] == "Unhealthy":
            out.append((key, "unhealthy", "high",
                        f"{ic['display']} is Unhealthy (score {r['score']}, availability "
                        f"{r['availability']:.1f}%)", ic["alert_channel"]))
    if overall and overall["colour"] == "Red":
        who = ", ".join(cfg["integrations"][k]["display"] for k in overall["red_rule_triggered_by"])
        out.append(("overall", "overall_red", "critical",
                    f"Overall health RED (score {overall['score']})"
                    + (f" - red rule: {who}" if who else ""), "teams-ops-critical"))
    return out


def teams_payload(window, sev, msg):
    colour = {"critical": "attention", "high": "warning"}.get(sev, "default")
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4",
                    "body": [{"type": "TextBlock", "text": "BrightLine HR - Integration Health",
                              "weight": "Bolder", "color": colour},
                             {"type": "TextBlock", "text": msg, "wrap": True},
                             {"type": "TextBlock", "text": f"Window {window} UTC", "isSubtle": True}]}}]}


def dispatch(conn, window, alerts):
    url = os.environ.get("TEAMS_WEBHOOK_URL")
    new = []
    for key, rule, sev, msg, channel in alerts:
        cur = conn.execute(
            """INSERT OR IGNORE INTO alerts(created_at,window_start,integration,rule,severity,
               message,channel,delivered) VALUES(?,?,?,?,?,?,?,0)""",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"), window, key, rule, sev, msg, channel))
        if cur.rowcount == 0:       # de-duplicated: already raised for this hour
            continue
        delivered = 0
        if url:
            try:
                req = urllib.request.Request(url, json.dumps(teams_payload(window, sev, msg)).encode(),
                                             {"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
                delivered = 1
            except Exception:
                delivered = 0
        conn.execute("UPDATE alerts SET delivered=? WHERE id=?", (delivered, cur.lastrowid))
        new.append(msg)
    conn.commit()
    return new
