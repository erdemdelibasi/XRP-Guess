"""Combines the technical-indicator signal and the ML signal into one final
prediction, weighted by each component's recent track record."""

DEFAULT_WEIGHTS = {"technical": 0.5, "ml": 0.5}
MIN_WEIGHT = 0.15  # floor so the ensemble never fully abandons a component


def combine(technical: dict, ml: dict, weights: dict | None = None) -> dict:
    weights = weights or DEFAULT_WEIGHTS
    tech_score = technical["confidence"] if technical["direction"] == "UP" else -technical["confidence"]
    ml_score = ml["confidence"] if ml["direction"] == "UP" else -ml["confidence"]

    combined_score = weights["technical"] * tech_score + weights["ml"] * ml_score
    direction = "UP" if combined_score >= 0 else "DOWN"
    confidence = min(abs(combined_score), 1.0)

    return {
        "direction": direction,
        "confidence": confidence,
        "score": combined_score,
        "weights_used": dict(weights),
    }


def recompute_weights(technical_accuracy: float | None, ml_accuracy: float | None) -> dict:
    """Rolling-accuracy-based weight update. Falls back to defaults until both
    components have enough resolved history to be trusted."""
    if technical_accuracy is None or ml_accuracy is None:
        return dict(DEFAULT_WEIGHTS)

    total = technical_accuracy + ml_accuracy
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)

    w_tech = technical_accuracy / total
    w_ml = ml_accuracy / total

    # Apply a floor, then renormalize so weights still sum to 1.
    w_tech = max(w_tech, MIN_WEIGHT)
    w_ml = max(w_ml, MIN_WEIGHT)
    scale = 1.0 / (w_tech + w_ml)
    return {"technical": w_tech * scale, "ml": w_ml * scale}
