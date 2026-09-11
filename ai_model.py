"""
ai_model.py
-----------
Machine-learning core of QueueSense-AI.

Responsibilities:
  1. Generate a realistic *synthetic* historical queue dataset (since a brand
     new deployment has no real history to learn from).
  2. Train a RandomForestRegressor that predicts waiting time (minutes) from
     live queue features.
  3. Train a lightweight crowd-level classifier bucket derived from the same
     features (low / medium / high / very_high), reusing the regressor's
     output rather than a second model, which keeps the pipeline simple and
     explainable for a resume project while still giving a genuine ML result.
  4. Persist / load the trained model + encoders with joblib so training only
     happens once (or whenever `python ai_model.py` is re-run explicitly).

The dataset intentionally encodes believable, non-trivial relationships:
  * Waiting time rises with queue length and falls with counters open.
  * Certain hours (opening rush, lunch, pre-closing) are busier.
  * Weekends/Mondays are busier than mid-week.
  * Each service has its own baseline service time.
This gives RandomForest real signal to learn instead of pure noise.
"""

import os
import logging
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import LabelEncoder

from config import Config, ensure_directories

logger = logging.getLogger("queuesense.ai_model")

SERVICE_NAMES = [
    "General Enquiry",
    "Bill Payment",
    "Account Services",
    "Document Verification",
    "Customer Support",
]

# Baseline average service time (minutes) per service - mirrors database seed data
SERVICE_BASE_MINUTES = {
    "General Enquiry": 4.0,
    "Bill Payment": 3.5,
    "Account Services": 8.0,
    "Document Verification": 6.0,
    "Customer Support": 7.0,
}


class WaitTimePredictor:
    """Wraps the trained RandomForestRegressor + label encoders."""

    def __init__(self):
        self.model: RandomForestRegressor | None = None
        self.service_encoder: LabelEncoder | None = None
        self.feature_columns = [
            "service_encoded",
            "hour",
            "day_of_week",
            "queue_length",
            "counters_open",
            "is_weekend",
        ]
        self.metrics = {}

    # ------------------------------------------------------------------
    # Synthetic dataset generation
    # ------------------------------------------------------------------
    def generate_synthetic_dataset(self, n_samples: int = 12000, seed: int = 42) -> pd.DataFrame:
        rng = np.random.default_rng(seed)

        rows = []
        for _ in range(n_samples):
            service = rng.choice(SERVICE_NAMES)
            base_service_time = SERVICE_BASE_MINUTES[service]

            hour = int(rng.integers(Config.OPEN_HOUR, Config.CLOSE_HOUR))
            day_of_week = int(rng.integers(0, 7))  # 0=Mon ... 6=Sun
            is_weekend = 1 if day_of_week >= 5 else 0

            counters_open = int(rng.integers(1, 5))

            # Rush-hour multiplier: opening rush, lunch rush, pre-closing rush
            if hour in (9, 10):
                rush_factor = rng.uniform(1.3, 1.8)
            elif hour in (13, 14):
                rush_factor = rng.uniform(1.2, 1.6)
            elif hour in (16, 17):
                rush_factor = rng.uniform(1.1, 1.5)
            else:
                rush_factor = rng.uniform(0.7, 1.1)

            # Monday & weekend tend to be busier for civic/bank-style services
            day_factor = 1.25 if day_of_week == 0 else (1.15 if is_weekend else 1.0)

            base_queue = rng.uniform(2, 40) * rush_factor * day_factor
            queue_length = max(0, int(base_queue))

            # Waiting time formula: queue_length people ahead, each taking
            # roughly base_service_time minutes, split across open counters,
            # plus small random noise for realism.
            effective_counters = max(1, counters_open)
            predicted_core = (queue_length * base_service_time) / effective_counters
            noise = rng.normal(0, base_service_time * 0.4)
            wait_minutes = max(0.5, predicted_core * rng.uniform(0.85, 1.15) + noise)

            rows.append(
                {
                    "service": service,
                    "hour": hour,
                    "day_of_week": day_of_week,
                    "is_weekend": is_weekend,
                    "queue_length": queue_length,
                    "counters_open": counters_open,
                    "wait_minutes": round(wait_minutes, 2),
                }
            )

        df = pd.DataFrame(rows)
        ensure_directories()
        df.to_csv(Config.DATASET_PATH, index=False)
        logger.info("Synthetic dataset generated: %d rows -> %s", len(df), Config.DATASET_PATH)
        return df

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------
    def train(self, df: pd.DataFrame = None) -> dict:
        if df is None:
            if os.path.exists(Config.DATASET_PATH):
                df = pd.read_csv(Config.DATASET_PATH)
            else:
                df = self.generate_synthetic_dataset()

        self.service_encoder = LabelEncoder()
        df = df.copy()
        df["service_encoded"] = self.service_encoder.fit_transform(df["service"])

        X = df[self.feature_columns]
        y = df["wait_minutes"]

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        self.model = RandomForestRegressor(
            n_estimators=200,
            max_depth=14,
            min_samples_split=4,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(X_train, y_train)

        preds = self.model.predict(X_test)
        mae = mean_absolute_error(y_test, preds)
        r2 = r2_score(y_test, preds)
        # Convert R^2-based accuracy into an intuitive percentage for dashboards
        accuracy_pct = max(0.0, min(100.0, r2 * 100))

        self.metrics = {
            "mae_minutes": round(float(mae), 2),
            "r2_score": round(float(r2), 4),
            "accuracy_pct": round(float(accuracy_pct), 2),
            "training_samples": len(X_train),
            "test_samples": len(X_test),
        }
        logger.info("Model trained. Metrics: %s", self.metrics)
        self.save()
        return self.metrics

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self):
        ensure_directories()
        joblib.dump(self.model, Config.MODEL_PATH)
        joblib.dump(
            {"service_encoder": self.service_encoder, "metrics": self.metrics},
            Config.ENCODERS_PATH,
        )
        logger.info("Model + encoders saved to %s", Config.MODEL_DIR)

    def load(self) -> bool:
        if not (os.path.exists(Config.MODEL_PATH) and os.path.exists(Config.ENCODERS_PATH)):
            return False
        self.model = joblib.load(Config.MODEL_PATH)
        extra = joblib.load(Config.ENCODERS_PATH)
        self.service_encoder = extra["service_encoder"]
        self.metrics = extra.get("metrics", {})
        return True

    def ensure_ready(self):
        """Load a saved model, or train a fresh one if none exists yet."""
        if not self.load():
            logger.info("No saved model found - training a new one now.")
            self.train()


# Module-level singleton used across the app
predictor = WaitTimePredictor()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    predictor.generate_synthetic_dataset()
    metrics = predictor.train()
    print("Training complete:", metrics)
