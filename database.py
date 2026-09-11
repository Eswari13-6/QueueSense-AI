"""
database.py
------------
SQLite data-access layer for QueueSense-AI.

Provides:
  * Database   -- low level connection / cursor helper (context-manager based)
  * init_db()  -- creates all tables + seeds default admin & services
  * Table-specific CRUD helper functions used by the rest of the app.

Design notes
------------
We deliberately avoid a heavyweight ORM so the project stays dependency-light
and transparent for a resume/portfolio project, while still keeping all SQL
in one place (a lightweight "Repository" pattern) so business logic modules
(queue_manager, token_manager, analytics, ...) never write raw SQL themselves.
"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime

from werkzeug.security import generate_password_hash

from config import Config

logger = logging.getLogger("queuesense.database")


class Database:
    """Thin wrapper around sqlite3 giving us safe, reusable connections."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DATABASE_PATH

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def cursor(self, commit: bool = False):
        """
        Context manager yielding a cursor. Commits on success (if commit=True),
        rolls back on exception, and always closes the connection.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Database operation failed, transaction rolled back")
            raise
        finally:
            conn.close()

    def execute(self, query: str, params: tuple = (), commit: bool = False):
        with self.cursor(commit=commit) as cur:
            cur.execute(query, params)
            if commit:
                return cur.lastrowid
            return cur.fetchall()

    def execute_one(self, query: str, params: tuple = ()):
        with self.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def execute_many(self, query: str, params: tuple = ()):
        with self.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


db = Database()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    counters INTEGER NOT NULL DEFAULT 1,
    avg_service_minutes REAL NOT NULL DEFAULT 5.0,
    max_daily_tokens INTEGER NOT NULL DEFAULT 150,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_paused INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_number TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    counter_id INTEGER,
    priority TEXT NOT NULL DEFAULT 'normal',   -- normal | vip | emergency
    status TEXT NOT NULL DEFAULT 'waiting',    -- waiting | called | serving | completed | skipped | cancelled
    predicted_wait_minutes REAL,
    actual_wait_minutes REAL,
    crowd_level TEXT,
    booked_at TEXT NOT NULL DEFAULT (datetime('now')),
    called_at TEXT,
    completed_at TEXT,
    queue_date TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (service_id) REFERENCES services (id)
);

CREATE TABLE IF NOT EXISTS queue_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL,
    event TEXT NOT NULL,      -- booked | called | serving | completed | skipped | cancelled
    event_time TEXT NOT NULL DEFAULT (datetime('now')),
    notes TEXT,
    FOREIGN KEY (token_id) REFERENCES tokens (id)
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER,
    service_id INTEGER NOT NULL,
    hour INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL,
    queue_length INTEGER NOT NULL,
    counters_open INTEGER NOT NULL,
    predicted_wait_minutes REAL NOT NULL,
    predicted_crowd_level TEXT NOT NULL,
    actual_wait_minutes REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (token_id) REFERENCES tokens (id),
    FOREIGN KEY (service_id) REFERENCES services (id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    generated_by TEXT,
    date_from TEXT,
    date_to TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db():
    """Create all tables (idempotent) and seed default admin + services."""
    with db.cursor(commit=True) as cur:
        cur.executescript(SCHEMA)

    _seed_default_admin()
    _seed_default_services()
    logger.info("Database initialized successfully")


def _seed_default_admin():
    existing = db.execute_one("SELECT id FROM admins WHERE username = ?", ("admin",))
    if existing:
        return
    db.execute(
        """INSERT INTO admins (username, password_hash, full_name, role)
           VALUES (?, ?, ?, ?)""",
        ("admin", generate_password_hash("Admin@123"), "System Administrator", "super_admin"),
        commit=True,
    )
    logger.info("Seeded default admin (username='admin', password='Admin@123')")


def _seed_default_services():
    existing = db.execute("SELECT id FROM services")
    if existing:
        return
    default_services = [
        ("General Enquiry", "General enquiries and information desk", 2, 4.0, 200),
        ("Bill Payment", "Utility and bill payment counter", 3, 3.5, 250),
        ("Account Services", "Account opening, KYC and updates", 2, 8.0, 120),
        ("Document Verification", "Document checking and verification", 2, 6.0, 150),
        ("Customer Support", "Complaints and support desk", 2, 7.0, 130),
    ]
    for name, desc, counters, avg_minutes, max_tokens in default_services:
        db.execute(
            """INSERT INTO services (name, description, counters, avg_service_minutes, max_daily_tokens)
               VALUES (?, ?, ?, ?, ?)""",
            (name, desc, counters, avg_minutes, max_tokens),
            commit=True,
        )
    logger.info("Seeded %d default services", len(default_services))


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
