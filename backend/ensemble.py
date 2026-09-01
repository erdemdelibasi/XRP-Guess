"""Combines every signal component (technical, ml, whale, news, ...) into one
final prediction, weighted by each component's recent track record."""

COMPONENTS = ["technical", "ml", "whale", "news"]
DEFAULT_WEIGHTS = {"technical": 0.35, "ml": 0.35, "whale": 0.15, "news": 0.15}
MIN_WEIGHT = 0.05  # floor so the ensemble never fully abandons a component

# predictions-table column prefix per component. "technical" is shortened to
# "tech" there to keep column names compact; every other component's columns
# use its own name as-is.
COLUMN_PREFIX = {"technical": "tech", "ml": "ml", "whale": "whale", "news": "news"}


def combine(signals: dict[str, dict], weights: dict[str, float] | None = None) -> dict:
    weights = weights or DEFAULT_WEIGHTS
    combined_score = 0.0
    for name, signal in signals.items():
        w = weights.get(name, 0.0)
        score = signal["confidence"] if signal["direction"] == "UP" else -signal["confidence"]
        combined_score += w * score

    direction = "UP" if combined_score >= 0 else "DOWN"
    confidence = min(abs(combined_score), 1.0)

    return {
        "direction": direction,
        "confidence": confidence,
        "score": combined_score,
        "weights_used": dict(weights),
    }


def recompute_weights(accuracies: dict[str, float | None]) -> dict:
    """Rolling-accuracy-based weight update across every component in
    COMPONENTS. Falls back to defaults until *all* components have enough
    resolved (non-abstained) history to be trusted."""
    known = {c: accuracies.get(c) for c in COMPONENTS}
    if any(v is None for v in known.values()):
        return dict(DEFAULT_WEIGHTS)

    total = sum(known.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)

    raw = {c: known[c] / total for c in COMPONENTS}

    # Apply a floor, then renormalize so weights still sum to 1.
    floored = {c: max(raw[c], MIN_WEIGHT) for c in COMPONENTS}
    scale = 1.0 / sum(floored.values())
    return {c: floored[c] * scale for c in COMPONENTS}
