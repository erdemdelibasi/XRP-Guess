"""Simple ML classifier: predicts whether the next 1h candle closes higher.

Trains directly on Binance's own historical OHLCV data (no cold-start problem —
does not depend on our own prediction log). Retrained periodically by retrain.py.
"""
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier

from indicators import add_indicator_columns

MODEL_PATH = Path(__file__).parent / "models" / "xrp_model.joblib"

FEATURE_COLUMNS = [
    "rsi14", "macd", "macd_signal", "macd_hist",
    "ema9", "ema21", "sma50", "bb_pct",
    "volume_ratio", "return_1h", "return_6h", "return_24h",
    "taker_buy_ratio",
]


def build_feature_frame(raw_klines: pd.DataFrame) -> pd.DataFrame:
    """Adds indicator columns and a next-candle-up label. Drops warmup/NaN rows."""
    df = add_indicator_columns(raw_klines)
    # NaN > x evaluates to False (not NaN) in pandas, so shift(-1)'s empty last
    # row was silently getting a fabricated DOWN=0 label instead of being
    # dropped -- np.where makes that row NaN explicitly so dropna catches it.
    next_close = df["close"].shift(-1)
    df["label_next_up"] = np.where(next_close.notna(), (next_close > df["close"]).astype(float), np.nan)
    df = df.dropna(subset=FEATURE_COLUMNS + ["label_next_up"])
    return df


def train_model(raw_klines: pd.DataFrame) -> tuple[GradientBoostingClassifier, dict]:
    """Trains a fresh classifier on historical klines. Returns (model, metrics)."""
    # build_feature_frame() already drops the final row (its "next candle" outcome
    # isn't known yet), so every remaining row here has a real, non-leaked label.
    df = build_feature_frame(raw_klines)

    split = int(len(df) * 0.85)
    train_df, test_df = df.iloc[:split], df.iloc[split:]

    model = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.05)
    model.fit(train_df[FEATURE_COLUMNS], train_df["label_next_up"])

    metrics = {"train_rows": len(train_df), "test_rows": len(test_df)}
    if len(test_df) > 0:
        preds = model.predict(test_df[FEATURE_COLUMNS])
        metrics["holdout_accuracy"] = float((preds == test_df["label_next_up"]).mean())

    return model, metrics


def save_model(model: GradientBoostingClassifier) -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)


def load_model() -> GradientBoostingClassifier | None:
    if not MODEL_PATH.exists():
        return None
    return joblib.load(MODEL_PATH)


def ml_signal(model: GradientBoostingClassifier, raw_klines_with_indicators: pd.DataFrame) -> dict:
    """Predicts direction for the next candle using the latest available features."""
    last = raw_klines_with_indicators.iloc[-1]
    features = last[FEATURE_COLUMNS]
    if features.isna().any():
        return {"direction": "UP", "confidence": 0.0, "score": 0.0}

    x = features.to_numpy().reshape(1, -1)
    proba_up = float(model.predict_proba(x)[0][1])
    score = float(np.clip((proba_up - 0.5) * 2, -1, 1))
    direction = "UP" if proba_up >= 0.5 else "DOWN"
    confidence = abs(proba_up - 0.5) * 2
    return {"direction": direction, "confidence": confidence, "score": score, "proba_up": proba_up}
