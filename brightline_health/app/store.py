"""SQLite store - local stand-in for Azure Data Explorer / ClickHouse."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_logs(
  ts TEXT, hour_start TEXT, customer_id TEXT, integration TEXT, request_id TEXT,
  http_status INTEGER, app_error_code TEXT, latency_ms REAL, is_timeout INTEGER,
  failed INTEGER, error_text TEXT);
CREATE INDEX IF NOT EXISTS ix_raw_hour ON raw_logs(hour_start, integration);
CREATE TABLE IF NOT EXISTS hourly_agg(
  window_start TEXT, integration TEXT, customer_id TEXT,
  total INTEGER, failed INTEGER, timeouts INTEGER, availability REAL,
  p95_latency_ms REAL, base_availability REAL, base_p95_ms REAL, baseline_mode TEXT,
  avail_score REAL, latency_score REAL, score REAL, status TEXT,
  synthetic INTEGER DEFAULT 0,
  PRIMARY KEY(window_start, integration, customer_id));
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, window_start TEXT,
  integration TEXT, rule TEXT, severity TEXT, message TEXT, channel TEXT,
  delivered INTEGER, UNIQUE(window_start, integration, rule));
"""


def connect(path="data/health.db"):
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_raw(conn, rows):
    conn.executemany(
        """INSERT INTO raw_logs VALUES(:ts,:hour_start,:customer_id,:integration,:request_id,
        :http_status,:app_error_code,:latency_ms,:is_timeout,:failed,:error_text)""", rows)
    conn.commit()


def raw_for_hour(conn, hour_start):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM raw_logs WHERE hour_start=? ORDER BY ts", (hour_start,))]


def hours(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT window_start FROM hourly_agg WHERE synthetic=0 ORDER BY 1 DESC")]


def history(conn, integration, hour_start, days):
    """Same hour-of-day aggregates for the previous `days` days (integration level)."""
    h = datetime.fromisoformat(hour_start)
    wanted = [(h - timedelta(days=d)).isoformat() for d in range(1, days + 1)]
    q = ",".join("?" * len(wanted))
    return [dict(r) for r in conn.execute(
        f"""SELECT * FROM hourly_agg WHERE integration=? AND customer_id='*'
            AND window_start IN ({q}) AND total>0""", (integration, *wanted))]


def save_agg(conn, rows):
    conn.executemany(
        """INSERT OR REPLACE INTO hourly_agg VALUES(:window_start,:integration,:customer_id,
        :total,:failed,:timeouts,:availability,:p95_latency_ms,:base_availability,:base_p95_ms,
        :baseline_mode,:avail_score,:latency_score,:score,:status,:synthetic)""", rows)
    conn.commit()


def agg_for_hour(conn, hour_start):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM hourly_agg WHERE window_start=?", (hour_start,))]


def trend(conn, integration, upto_hour, n=24):
    return [dict(r) for r in conn.execute(
        """SELECT window_start, availability, p95_latency_ms, score, status, base_availability, synthetic
           FROM hourly_agg WHERE integration=? AND customer_id='*' AND window_start<=?
           ORDER BY window_start DESC LIMIT ?""", (integration, upto_hour, n))][::-1]


def purge(conn, now=None, days=60):
    """60-day retention job (raw logs). Aggregates are kept for trends."""
    cutoff = ((now or datetime.now(timezone.utc).replace(tzinfo=None)) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM raw_logs WHERE ts<?", (cutoff,))
    conn.commit()
    return cur.rowcount


def reset(conn):
    for t in ("raw_logs", "hourly_agg", "alerts"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
