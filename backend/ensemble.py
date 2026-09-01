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
    """Rolling-accuracy-based weight update. Components with enough resolved
    (non-abstained) history redistribute *their own combined default weight
    budget* proportionally to relative accuracy; components without enough
    history yet (e.g. whale/news still mostly silent) simply keep their
    default weight. This way a quiet or unconfigured component never blocks
    the others from self-improving -- only components that actually have a
    track record adjust."""
    known = {c: accuracies[c] for c in COMPONENTS if accuracies.get(c) is not None}
    if not known:
        return dict(DEFAULT_WEIGHTS)

    total = sum(known.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)

    known_budget = sum(DEFAULT_WEIGHTS[c] for c in known)
    raw = {c: (known[c] / total) * known_budget for c in known}

    # Apply a floor, then renormalize so the known components' share still
    # sums to exactly known_budget (keeping the overall total at 1).
    floored = {c: max(v, MIN_WEIGHT) for c, v in raw.items()}
    scale = known_budget / sum(floored.values())

    weights = dict(DEFAULT_WEIGHTS)
    for c in known:
        weights[c] = floored[c] * scale
    return weights
