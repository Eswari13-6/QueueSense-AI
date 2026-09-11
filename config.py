"""
config.py
---------
Central configuration for QueueSense-AI.

All environment-dependent and application-wide constants live here so that
the rest of the codebase never hard-codes paths, secrets, or tunables.
"""

import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Base Flask configuration."""

    # --- Security -----------------------------------------------------
    SECRET_KEY = os.environ.get("QUEUESENSE_SECRET_KEY", "dev-secret-key-change-in-production")

    # --- Database -------------------------------------------------------
    DATABASE_PATH = os.path.join(BASE_DIR, "queuesense.db")

    # --- Sessions ---------------------------------------------------------
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # --- File paths -----------------------------------------------------
    MODEL_DIR = os.path.join(BASE_DIR, "models")
    MODEL_PATH = os.path.join(MODEL_DIR, "wait_time_model.pkl")
    ENCODERS_PATH = os.path.join(MODEL_DIR, "encoders.pkl")
    DATASET_PATH = os.path.join(MODEL_DIR, "synthetic_queue_data.csv")

    QR_CODE_DIR = os.path.join(BASE_DIR, "static", "qrcodes")
    REPORT_DIR = os.path.join(BASE_DIR, "static", "reports")

    # --- Business rules ---------------------------------------------------
    DEFAULT_MAX_DAILY_TOKENS = 150
    NEAR_TURN_THRESHOLD = 3          # Notify user when this many people are ahead
    VIP_PRIORITY_WEIGHT = 1000       # Large offset so VIP tokens sort ahead of normal ones
    EMERGENCY_PRIORITY_WEIGHT = 5000  # Emergency tokens sort ahead of VIP tokens

    # --- Working hours (used by AI dataset generation & predictions) ----
    OPEN_HOUR = 9
    CLOSE_HOUR = 18

    # --- Logging --------------------------------------------------------
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    LOG_FILE = os.path.join(LOG_DIR, "queuesense.log")


def ensure_directories():
    """Create all runtime directories the app depends on, if missing."""
    for path in (
        Config.MODEL_DIR,
        Config.QR_CODE_DIR,
        Config.REPORT_DIR,
        Config.LOG_DIR,
    ):
        os.makedirs(path, exist_ok=True)
