"""
analytics.py
-------------
All aggregation queries that power the admin analytics dashboard & Chart.js
visualisations: hourly/daily/monthly visitor counts, average waiting time,
counter utilisation, completed/cancelled token counts, and prediction
accuracy (comparing predicted_wait_minutes vs actual_wait_minutes).
"""

import logging
from datetime import datetime, timedelta

from database import db, today_str

logger = logging.getLogger("queuesense.analytics")


def todays_summary() -> dict:
    today = today_str()
    total = db.execute_one(
        "SELECT COUNT(*) as cnt FROM tokens WHERE queue_date = ?", (today,)
    )["cnt"]
    completed = db.execute_one(
        "SELECT COUNT(*) as cnt FROM tokens WHERE queue_date = ? AND status = 'completed'", (today,)
    )["cnt"]
    cancelled = db.execute_one(
        "SELECT COUNT(*) as cnt FROM tokens WHERE queue_date = ? AND status IN ('cancelled','skipped')",
        (today,),
    )["cnt"]
    waiting = db.execute_one(
        "SELECT COUNT(*) as cnt FROM tokens WHERE queue_date = ? AND status = 'waiting'", (today,)
    )["cnt"]
    avg_wait = db.execute_one(
        """SELECT AVG(actual_wait_minutes) as avg_w FROM tokens
           WHERE queue_date = ? AND actual_wait_minutes IS NOT NULL""",
        (today,),
    )["avg_w"]

    return {
        "total_tokens": total,
        "completed_tokens": completed,
        "cancelled_tokens": cancelled,
        "waiting_tokens": waiting,
        "avg_wait_minutes": round(avg_wait, 1) if avg_wait else 0.0,
    }


def hourly_visitors(queue_date: str = None) -> dict:
    queue_date = queue_date or today_str()
    rows = db.execute(
        """SELECT strftime('%H', booked_at) as hour, COUNT(*) as cnt
           FROM tokens WHERE queue_date = ? GROUP BY hour ORDER BY hour""",
        (queue_date,),
    )
    return {"labels": [f"{r['hour']}:00" for r in rows], "values": [r["cnt"] for r in rows]}


def daily_visitors(days: int = 7) -> dict:
    start_date = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = db.execute(
        """SELECT queue_date, COUNT(*) as cnt FROM tokens
           WHERE queue_date >= ? GROUP BY queue_date ORDER BY queue_date""",
        (start_date,),
    )
    return {"labels": [r["queue_date"] for r in rows], "values": [r["cnt"] for r in rows]}


def monthly_visitors(months: int = 6) -> dict:
    rows = db.execute(
        """SELECT strftime('%Y-%m', queue_date) as ym, COUNT(*) as cnt
           FROM tokens GROUP BY ym ORDER BY ym DESC LIMIT ?""",
        (months,),
    )
    rows = list(reversed(rows))
    return {"labels": [r["ym"] for r in rows], "values": [r["cnt"] for r in rows]}


def average_waiting_time_by_service() -> dict:
    rows = db.execute(
        """SELECT s.name as service_name, AVG(t.actual_wait_minutes) as avg_wait
           FROM tokens t JOIN services s ON t.service_id = s.id
           WHERE t.actual_wait_minutes IS NOT NULL
           GROUP BY s.name"""
    )
    return {
        "labels": [r["service_name"] for r in rows],
        "values": [round(r["avg_wait"], 1) if r["avg_wait"] else 0 for r in rows],
    }


def counter_utilization() -> dict:
    rows = db.execute(
        """SELECT s.name as service_name, s.counters,
                  COUNT(t.id) as tokens_served
           FROM services s
           LEFT JOIN tokens t ON t.service_id = s.id AND t.status = 'completed'
           GROUP BY s.id"""
    )
    labels, values = [], []
    for r in rows:
        counters = r["counters"] or 1
        utilization = round((r["tokens_served"] or 0) / counters, 1)
        labels.append(r["service_name"])
        values.append(utilization)
    return {"labels": labels, "values": values}


def completed_vs_cancelled() -> dict:
    completed = db.execute_one("SELECT COUNT(*) as cnt FROM tokens WHERE status = 'completed'")["cnt"]
    cancelled = db.execute_one(
        "SELECT COUNT(*) as cnt FROM tokens WHERE status IN ('cancelled','skipped')"
    )["cnt"]
    return {"labels": ["Completed", "Cancelled/Skipped"], "values": [completed, cancelled]}


def prediction_accuracy() -> dict:
    """
    Compare predicted vs actual wait times across all completed tokens with
    both values recorded, expressed as a percentage accuracy per day.
    """
    rows = db.execute(
        """SELECT queue_date, predicted_wait_minutes, actual_wait_minutes
           FROM tokens
           WHERE predicted_wait_minutes IS NOT NULL AND actual_wait_minutes IS NOT NULL
           ORDER BY queue_date"""
    )
    daily_errors = {}
    for r in rows:
        pred, actual = r["predicted_wait_minutes"], r["actual_wait_minutes"]
        if actual <= 0:
            continue
        error_pct = abs(pred - actual) / actual
        accuracy_pct = max(0.0, 100.0 - error_pct * 100)
        daily_errors.setdefault(r["queue_date"], []).append(accuracy_pct)

    labels = sorted(daily_errors.keys())
    values = [round(sum(daily_errors[d]) / len(daily_errors[d]), 1) for d in labels]
    overall = round(sum(values) / len(values), 1) if values else None

    return {"labels": labels, "values": values, "overall_accuracy": overall}


def service_breakdown(queue_date: str = None) -> dict:
    queue_date = queue_date or today_str()
    rows = db.execute(
        """SELECT s.name as service_name, COUNT(t.id) as cnt
           FROM services s LEFT JOIN tokens t ON t.service_id = s.id AND t.queue_date = ?
           GROUP BY s.id""",
        (queue_date,),
    )
    return {"labels": [r["service_name"] for r in rows], "values": [r["cnt"] for r in rows]}
