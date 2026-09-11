"""
token_manager.py
-----------------
Everything related to the lifecycle of an individual token:
  * Generating human-friendly token numbers (e.g. GEN-014, VIP-002)
  * Creating a token row (with an initial AI wait-time prediction)
  * Generating & saving a QR code image for the token
  * Cancelling a token
  * Fetching a user's token history

queue_manager.py builds on top of this module for the *admin-side* queue
operations (call next, skip, pause, etc.).
"""

import os
import logging
import qrcode

from config import Config
from database import db, today_str
from prediction import predict_wait_time

logger = logging.getLogger("queuesense.token_manager")

SERVICE_PREFIXES = {
    "General Enquiry": "GEN",
    "Bill Payment": "BIL",
    "Account Services": "ACC",
    "Document Verification": "DOC",
    "Customer Support": "SUP",
}

PRIORITY_PREFIX = {
    "normal": "",
    "vip": "VIP-",
    "emergency": "EMG-",
}


def _next_sequence_number(service_id: int, queue_date: str) -> int:
    row = db.execute_one(
        "SELECT COUNT(*) as cnt FROM tokens WHERE service_id = ? AND queue_date = ?",
        (service_id, queue_date),
    )
    return (row["cnt"] if row else 0) + 1


def _build_token_number(service_name: str, priority: str, sequence: int) -> str:
    prefix = SERVICE_PREFIXES.get(service_name, service_name[:3].upper())
    priority_tag = PRIORITY_PREFIX.get(priority, "")
    return f"{priority_tag}{prefix}-{sequence:03d}"


def get_service(service_id: int):
    return db.execute_one("SELECT * FROM services WHERE id = ?", (service_id,))


def get_all_services(active_only: bool = True):
    if active_only:
        return db.execute("SELECT * FROM services WHERE is_active = 1 ORDER BY name")
    return db.execute("SELECT * FROM services ORDER BY name")


def current_queue_length(service_id: int, queue_date: str = None) -> int:
    queue_date = queue_date or today_str()
    row = db.execute_one(
        """SELECT COUNT(*) as cnt FROM tokens
           WHERE service_id = ? AND queue_date = ? AND status IN ('waiting','called')""",
        (service_id, queue_date),
    )
    return row["cnt"] if row else 0


def daily_token_count(service_id: int, queue_date: str = None) -> int:
    queue_date = queue_date or today_str()
    row = db.execute_one(
        "SELECT COUNT(*) as cnt FROM tokens WHERE service_id = ? AND queue_date = ?",
        (service_id, queue_date),
    )
    return row["cnt"] if row else 0


class TokenLimitExceeded(Exception):
    pass


class ServicePausedError(Exception):
    pass


def book_token(user_id: int, service_id: int, priority: str = "normal") -> dict:
    """
    Book a new token for a user, run an AI wait-time prediction, persist it,
    and generate its QR code. Returns the created token row as a dict.
    """
    service = get_service(service_id)
    if not service:
        raise ValueError("Service not found")
    if not service["is_active"]:
        raise ValueError("Service is not currently active")
    if service["is_paused"] and priority == "normal":
        raise ServicePausedError(f"{service['name']} queue is currently paused by admin")

    queue_date = today_str()
    daily_count = daily_token_count(service_id, queue_date)
    if daily_count >= service["max_daily_tokens"] and priority == "normal":
        raise TokenLimitExceeded(
            f"Daily token limit ({service['max_daily_tokens']}) reached for {service['name']}"
        )

    sequence = _next_sequence_number(service_id, queue_date)
    token_number = _build_token_number(service["name"], priority, sequence)

    queue_len = current_queue_length(service_id, queue_date)
    prediction = predict_wait_time(
        service["name"], queue_len, service["counters"]
    )

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
        (token_id, f"Token {token_number} booked ({priority})"),
        commit=True,
    )

    db.execute(
        """INSERT INTO predictions
           (token_id, service_id, hour, day_of_week, queue_length, counters_open,
            predicted_wait_minutes, predicted_crowd_level)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            token_id, service_id, prediction.get("hour", 0), prediction.get("day_of_week", 0),
            queue_len, service["counters"], prediction["predicted_wait_minutes"],
            prediction["crowd_level"],
        ),
        commit=True,
    )

    generate_qr_code(token_id, token_number)

    logger.info("Token booked: %s for user_id=%s service=%s", token_number, user_id, service["name"])
    return get_token(token_id)


def generate_qr_code(token_id: int, token_number: str) -> str:
    os.makedirs(Config.QR_CODE_DIR, exist_ok=True)
    filename = f"token_{token_id}.png"
    filepath = os.path.join(Config.QR_CODE_DIR, filename)

    qr_data = f"QueueSense-AI Token: {token_number} | ID: {token_id}"
    img = qrcode.make(qr_data)
    img.save(filepath)

    return filename


def get_token(token_id: int):
    return db.execute_one(
        """SELECT t.*, s.name as service_name, s.counters, s.avg_service_minutes,
                  u.full_name as user_name, u.email as user_email
           FROM tokens t
           JOIN services s ON t.service_id = s.id
           JOIN users u ON t.user_id = u.id
           WHERE t.id = ?""",
        (token_id,),
    )


def get_user_active_tokens(user_id: int):
    return db.execute(
        """SELECT t.*, s.name as service_name FROM tokens t
           JOIN services s ON t.service_id = s.id
           WHERE t.user_id = ? AND t.status IN ('waiting','called')
           ORDER BY t.booked_at""",
        (user_id,),
    )


def get_user_history(user_id: int):
    return db.execute(
        """SELECT t.*, s.name as service_name FROM tokens t
           JOIN services s ON t.service_id = s.id
           WHERE t.user_id = ?
           ORDER BY t.booked_at DESC""",
        (user_id,),
    )


def cancel_token(token_id: int, user_id: int) -> bool:
    token = db.execute_one(
        "SELECT * FROM tokens WHERE id = ? AND user_id = ?", (token_id, user_id)
    )
    if not token or token["status"] not in ("waiting",):
        return False

    db.execute(
        "UPDATE tokens SET status = 'cancelled' WHERE id = ?", (token_id,), commit=True
    )
    db.execute(
        "INSERT INTO queue_history (token_id, event, notes) VALUES (?, 'cancelled', 'Cancelled by user')",
        (token_id,),
        commit=True,
    )
    return True


def queue_position(token_id: int) -> dict:
    """
    Compute a token's live position: people ahead, estimated wait, and
    expected call time, honoring priority ordering (emergency > vip > normal).
    """
    token = get_token(token_id)
    if not token:
        return {}

    priority_rank = {"emergency": 0, "vip": 1, "normal": 2}

    waiting_tokens = db.execute(
        """SELECT * FROM tokens
           WHERE service_id = ? AND queue_date = ? AND status IN ('waiting','called')
           ORDER BY booked_at""",
        (token["service_id"], token["queue_date"]),
    )

    ordered = sorted(
        waiting_tokens,
        key=lambda t: (priority_rank.get(t["priority"], 2), t["booked_at"]),
    )

    people_ahead = 0
    for t in ordered:
        if t["id"] == token_id:
            break
        if t["status"] in ("waiting", "called"):
            people_ahead += 1

    prediction = predict_wait_time(
        token["service_name"], people_ahead, token["counters"]
    )

    return {
        "token_number": token["token_number"],
        "status": token["status"],
        "people_ahead": people_ahead,
        "estimated_wait_minutes": prediction["predicted_wait_minutes"],
        "crowd_level": prediction["crowd_level"],
        "notify_near_turn": people_ahead <= Config.NEAR_TURN_THRESHOLD,
    }
