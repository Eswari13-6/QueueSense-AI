"""
queue_manager.py
-----------------
Admin-facing queue control logic: calling the next token, completing /
skipping the current one, pausing & resuming a service's queue, and issuing
priority (VIP / emergency) tokens on behalf of walk-in cases.

This module works on top of token_manager.py's data-access helpers and never
duplicates SQL that already lives there.
"""

import logging
from datetime import datetime

from database import db, today_str
from token_manager import get_service, get_token, generate_qr_code, _next_sequence_number, _build_token_number
from prediction import predict_wait_time

logger = logging.getLogger("queuesense.queue_manager")

PRIORITY_RANK = {"emergency": 0, "vip": 1, "normal": 2}


def get_live_queue(service_id: int, queue_date: str = None):
    """Return all waiting/called tokens for a service, ordered by priority then booking time."""
    queue_date = queue_date or today_str()
    rows = db.execute(
        """SELECT t.*, u.full_name as user_name FROM tokens t
           JOIN users u ON t.user_id = u.id
           WHERE t.service_id = ? AND t.queue_date = ? AND t.status IN ('waiting', 'called')
           ORDER BY t.booked_at""",
        (service_id, queue_date),
    )
    return sorted(rows, key=lambda t: (PRIORITY_RANK.get(t["priority"], 2), t["booked_at"]))


def get_current_serving(service_id: int, queue_date: str = None):
    queue_date = queue_date or today_str()
    return db.execute_one(
        """SELECT t.*, u.full_name as user_name FROM tokens t
           JOIN users u ON t.user_id = u.id
           WHERE t.service_id = ? AND t.queue_date = ? AND t.status IN ('called', 'serving')
           ORDER BY t.called_at DESC LIMIT 1""",
        (service_id, queue_date),
    )


def call_next(service_id: int, counter_id: int = None):
    """Call the next token in line for a service, respecting priority ordering."""
    queue = get_live_queue(service_id)
    waiting_only = [t for t in queue if t["status"] == "waiting"]
    if not waiting_only:
        return None

    next_token = waiting_only[0]
    db.execute(
        """UPDATE tokens SET status = 'called', called_at = datetime('now'), counter_id = ?
           WHERE id = ?""",
        (counter_id, next_token["id"]),
        commit=True,
    )
    db.execute(
        "INSERT INTO queue_history (token_id, event, notes) VALUES (?, 'called', 'Called to counter')",
        (next_token["id"],),
        commit=True,
    )
    logger.info("Called next token id=%s (%s)", next_token["id"], next_token["token_number"])
    return get_token(next_token["id"])


def complete_token(token_id: int):
    """Mark the currently-called/serving token as completed and record actual wait time."""
    token = get_token(token_id)
    if not token or token["status"] not in ("called", "serving"):
        return None

    booked_at = datetime.strptime(token["booked_at"], "%Y-%m-%d %H:%M:%S")
    actual_wait = (datetime.now() - booked_at).total_seconds() / 60.0

    db.execute(
        """UPDATE tokens SET status = 'completed', completed_at = datetime('now'),
           actual_wait_minutes = ? WHERE id = ?""",
        (round(actual_wait, 2), token_id),
        commit=True,
    )
    db.execute(
        "INSERT INTO queue_history (token_id, event, notes) VALUES (?, 'completed', 'Service completed')",
        (token_id,),
        commit=True,
    )
    db.execute(
        "UPDATE predictions SET actual_wait_minutes = ? WHERE token_id = ?",
        (round(actual_wait, 2), token_id),
        commit=True,
    )
    logger.info("Completed token id=%s actual_wait=%.2f min", token_id, actual_wait)
    return get_token(token_id)


def skip_token(token_id: int, reason: str = "No-show"):
    token = get_token(token_id)
    if not token or token["status"] not in ("called", "serving"):
        return None

    db.execute("UPDATE tokens SET status = 'skipped' WHERE id = ?", (token_id,), commit=True)
    db.execute(
        "INSERT INTO queue_history (token_id, event, notes) VALUES (?, 'skipped', ?)",
        (token_id, reason),
        commit=True,
    )
    logger.info("Skipped token id=%s reason=%s", token_id, reason)
    return get_token(token_id)


def pause_queue(service_id: int):
    db.execute("UPDATE services SET is_paused = 1 WHERE id = ?", (service_id,), commit=True)
    logger.info("Paused queue for service_id=%s", service_id)


def resume_queue(service_id: int):
    db.execute("UPDATE services SET is_paused = 0 WHERE id = ?", (service_id,), commit=True)
    logger.info("Resumed queue for service_id=%s", service_id)


def set_max_daily_tokens(service_id: int, max_tokens: int):
    db.execute(
        "UPDATE services SET max_daily_tokens = ? WHERE id = ?",
        (max_tokens, service_id),
        commit=True,
    )
    logger.info("Set max_daily_tokens=%s for service_id=%s", max_tokens, service_id)


def issue_priority_token(user_id: int, service_id: int, priority: str):
    """
    Admin-issued VIP or emergency token. Bypasses the daily limit and pause
    state (the whole point of an emergency token) but still gets a real AI
    wait-time prediction and QR code like any other token.
    """
    from token_manager import current_queue_length  # local import avoids circularity at module load

    if priority not in ("vip", "emergency"):
        raise ValueError("priority must be 'vip' or 'emergency'")

    service = get_service(service_id)
    if not service:
        raise ValueError("Service not found")

    queue_date = today_str()
    sequence = _next_sequence_number(service_id, queue_date)
    token_number = _build_token_number(service["name"], priority, sequence)

    queue_len = current_queue_length(service_id, queue_date)
    prediction = predict_wait_time(service["name"], max(0, queue_len - 3), service["counters"])

    token_id = db.execute(
        """INSERT INTO tokens
           (token_number, user_id, service_id, priority, status,
            predicted_wait_minutes, crowd_level, queue_date)
           VALUES (?, ?, ?, ?, 'waiting', ?, ?, ?)""",
        (
            token_number, user_id, service_id, priority,
            prediction["predicted_wait_minutes"], prediction["crowd_level"], queue_date,
        ),
        commit=True,
    )
    db.execute(
        "INSERT INTO queue_history (token_id, event, notes) VALUES (?, 'booked', ?)",
        (token_id, f"{priority.upper()} token issued by admin"),
        commit=True,
    )
    generate_qr_code(token_id, token_number)

    logger.info("Issued %s token %s for user_id=%s", priority, token_number, user_id)
    return get_token(token_id)
