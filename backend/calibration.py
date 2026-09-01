"""Calibrates each component's raw confidence against how often its predicted
direction has actually been right historically, using isotonic regression.

Direction never flips -- only confidence (and therefore position size) is
rescaled. A backtest reliability diagram showed technical+ML's raw confidence
was overconfident: real accuracy stayed flat around 50% across confidence
buckets from 0% to 40%, instead of rising with stated confidence as it
should for a well-calibrated signal (see backtest.py's calibration table).
Calibration corrects for that without touching the underlying direction call.

Only technical and ml can be calibrated for now -- they're the only
components backtest.py can replay against real history (see its docstring);
whale/news/orderbook stay uncalibrated (pass-through) until they accumulate
enough live resolved history of their own.
"""
from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression

CALIBRATION_PATH = Path(__file__).parent / "models" / "calibration.joblib"
MIN_RECORDS_TO_FIT = 200  # below this, an isotonic fit is more noise than signal
BIN_WIDTH = 0.05
MIN_BIN_COUNT = 30  # bins with fewer samples than this are dropped, not fit


def fit(confidences: list[float], corrects: list[bool]) -> IsotonicRegression | None:
    """confidences: raw 0..1 confidence in the predicted direction.
    corrects: whether that predicted direction was actually right.
    Fits confidence -> P(correct), floored at 0.5 since "the side we
    predicted" can never be worse than a coin flip by construction.

    Fits on binned, count-weighted averages rather than raw per-sample pairs.
    Isotonic regression is nonparametric and unregularized -- fit directly on
    individual samples, a handful of lucky/unlucky predictions in a
    thinly-populated high-confidence tail can swing that segment to 0.5 or
    1.0. A first attempt at this did exactly that: confidence>=0.35 had so
    few historical samples that the raw fit reported "100% accurate" there,
    which would have pushed position sizing to max allocation on the least
    trustworthy part of the curve. Dropping bins below MIN_BIN_COUNT and
    weighting the fit by how many samples each bin actually has avoids that."""
    if len(confidences) < MIN_RECORDS_TO_FIT:
        return None

    confidences = np.asarray(confidences, dtype=float)
    corrects = np.asarray([1.0 if c else 0.0 for c in corrects], dtype=float)

    bin_edges = np.arange(0.0, confidences.max() + BIN_WIDTH, BIN_WIDTH)
    bin_idx = np.clip(np.digitize(confidences, bin_edges) - 1, 0, len(bin_edges) - 2)

    bin_x, bin_y, bin_w = [], [], []
    for b in range(len(bin_edges) - 1):
        mask = bin_idx == b
        count = int(mask.sum())
        if count < MIN_BIN_COUNT:
            continue
        bin_x.append(float(confidences[mask].mean()))
        bin_y.append(float(corrects[mask].mean()))
        bin_w.append(count)

    if len(bin_x) < 2:
        return None  # not enough well-populated bins for a meaningful curve

    reg = IsotonicRegression(y_min=0.5, y_max=1.0, increasing=True, out_of_bounds="clip")
    reg.fit(bin_x, bin_y, sample_weight=bin_w)
    return reg


def save(calibrators: dict[str, IsotonicRegression]) -> None:
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(calibrators, CALIBRATION_PATH)


def load() -> dict[str, IsotonicRegression]:
    if not CALIBRATION_PATH.exists():
        return {}
    return joblib.load(CALIBRATION_PATH)


def apply(calibrator: IsotonicRegression | None, signal: dict) -> dict:
    """Returns `signal` with confidence (and score, kept consistent with it)
    replaced by the calibrated value. Direction and any other keys pass
    through unchanged. No-op if this component has no fitted calibrator yet."""
    if calibrator is None:
        return signal
    p_correct = float(calibrator.predict([signal["confidence"]])[0])
    calibrated_confidence = max((p_correct - 0.5) * 2, 0.0)
    sign = 1.0 if signal["direction"] == "UP" else -1.0
    return {**signal, "confidence": calibrated_confidence, "score": calibrated_confidence * sign}
