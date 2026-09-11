"""
prediction.py
-------------
Turns the trained RandomForestRegressor (ai_model.py) into actionable,
live predictions for the running Flask app:

  * predict_wait_time()   -> minutes + crowd level for a live queue state
  * predict_peak_hours()  -> which hours of the day are historically busiest
  * generate_suggestions()-> plain-English operational recommendations

This module never talks to the database directly for training data (that is
ai_model.py's job); it only *consumes* the trained model plus current queue
counts handed to it by queue_manager.py / analytics.py.
"""

import logging
from datetime import datetime

import pandas as pd

from ai_model import predictor, SERVICE_BASE_MINUTES

logger = logging.getLogger("queuesense.prediction")


def _crowd_level_from_wait(wait_minutes: float, queue_length: int) -> str:
    """Bucket a numeric prediction into a human-friendly crowd level."""
    if queue_length <= 5 and wait_minutes <= 10:
        return "Low"
    elif queue_length <= 15 and wait_minutes <= 30:
        return "Medium"
    elif queue_length <= 30 and wait_minutes <= 60:
        return "High"
    return "Very High"


def predict_wait_time(service_name: str, queue_length: int, counters_open: int,
                       when: datetime = None) -> dict:
    """
    Predict waiting time (minutes) and crowd level for a given service and
    live queue snapshot.
    """
    predictor.ensure_ready()
    when = when or datetime.now()

    if service_name not in list(predictor.service_encoder.classes_):
        # Unseen service name - fall back to a simple heuristic so the app
        # never crashes even if an admin adds a brand-new service.
        base = SERVICE_BASE_MINUTES.get(service_name, 5.0)
        wait_minutes = round((queue_length * base) / max(1, counters_open), 2)
        return {
            "predicted_wait_minutes": wait_minutes,
            "crowd_level": _crowd_level_from_wait(wait_minutes, queue_length),
            "source": "heuristic",
        }

    service_encoded = predictor.service_encoder.transform([service_name])[0]
    hour = when.hour
    day_of_week = when.weekday()
    is_weekend = 1 if day_of_week >= 5 else 0

    features = pd.DataFrame(
        [[service_encoded, hour, day_of_week, queue_length, counters_open, is_weekend]],
        columns=predictor.feature_columns,
    )
    wait_minutes = float(predictor.model.predict(features)[0])
    wait_minutes = max(0.5, round(wait_minutes, 2))

    crowd_level = _crowd_level_from_wait(wait_minutes, queue_length)

    return {
        "predicted_wait_minutes": wait_minutes,
        "crowd_level": crowd_level,
        "source": "model",
        "hour": hour,
        "day_of_week": day_of_week,
    }


def predict_peak_hours(service_name: str, counters_open: int = 2) -> list:
    """
    Sweep every open hour of the business day at a fixed moderate queue length
    to identify which hours the model considers busiest for this service.
    Returns a list of dicts sorted by predicted wait time, descending.
    """
    predictor.ensure_ready()
    from config import Config

    results = []
    sample_queue_length = 15
    today_dow = datetime.now().weekday()

    for hour in range(Config.OPEN_HOUR, Config.CLOSE_HOUR):
        probe_time = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
        result = predict_wait_time(service_name, sample_queue_length, counters_open, probe_time)
        results.append({"hour": hour, "predicted_wait_minutes": result["predicted_wait_minutes"]})

    results.sort(key=lambda r: r["predicted_wait_minutes"], reverse=True)
    return results


def generate_suggestions(service_name: str, queue_length: int, counters_open: int,
                          max_counters: int, predicted_wait: float, crowd_level: str) -> list:
    """
    Produce plain-English, actionable suggestions for admins based on the
    current predicted state of a service's queue.
    """
    suggestions = []

    if crowd_level in ("High", "Very High") and counters_open < max_counters:
        suggestions.append(
            f"Queue is {crowd_level.lower()} — consider opening another counter "
            f"for {service_name} ({counters_open}/{max_counters} currently open)."
        )

    if predicted_wait > 45:
        suggestions.append(
            "Predicted waiting time exceeds 45 minutes. Reducing average service time "
            "(e.g. pre-verifying documents) would meaningfully cut waits."
        )

    if queue_length > 25:
        suggestions.append(
            "Queue length is unusually high. Consider temporarily lowering the daily "
            "token limit or redirecting new tokens to a less busy time slot."
        )

    peak_hours = predict_peak_hours(service_name, counters_open)
    if peak_hours:
        quiet_hours = sorted(peak_hours, key=lambda r: r["predicted_wait_minutes"])[:2]
        quiet_hour_labels = ", ".join(f"{h['hour']}:00" for h in quiet_hours)
        suggestions.append(f"Best visiting hours today for shorter waits: {quiet_hour_labels}.")

    if not suggestions:
        suggestions.append(f"{service_name} is running smoothly — no action needed right now.")

    return suggestions
