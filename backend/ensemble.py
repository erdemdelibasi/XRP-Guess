"""Combines every signal component (technical, ml, whale, news, ...) into one
final prediction, weighted by each component's recent track record."""
import math

COMPONENTS = ["technical", "ml", "whale", "news", "orderbook", "claude"]
DEFAULT_WEIGHTS = {"technical": 0.24, "ml": 0.24, "whale": 0.12, "news": 0.10, "orderbook": 0.15, "claude": 0.15}
MIN_WEIGHT = 0.05  # floor so the ensemble never fully abandons a component
MAX_WEIGHT = 0.40  # ceiling so one component can't take over the blend on thin evidence (see recompute_weights)

# predictions-table column prefix per component. "technical" is shortened to
# "tech" there to keep column names compact; every other component's columns
# use its own name as-is.
COLUMN_PREFIX = {"technical": "tech", "ml": "ml", "whale": "whale", "news": "news", "orderbook": "orderbook", "claude": "claude"}


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


def _wilson_lower_bound(correct: int, total: int, z: float = 1.96) -> float:
    """Lower end of the 95% confidence interval for a component's true
    accuracy. Small samples pull this far below the raw hit rate, which is
    exactly the conservatism wanted here: a component earns weight only once
    its edge is actually established, not the first time it gets lucky."""
    if total <= 0:
        return 0.0
    p = correct / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return centre - margin


def recompute_weights(records: dict[str, tuple[int, int] | None]) -> dict:
    """Rolling-accuracy-based weight update. `records` maps a component to
    (correct_count, resolved_count) over the trailing window, or None if it
    doesn't have enough non-abstained history yet. Components with history
    redistribute *their own combined default weight budget*; the rest keep
    their default weight, so a quiet component never blocks the others from
    self-improving.

    Weight is proportional to each component's edge over a coin flip, using
    the Wilson lower bound rather than the raw hit rate. The previous version
    allocated proportionally to raw accuracy, which could not express "this
    component is bad": measured on 240 live predictions (2026-09-03), `claude`
    at 37% accuracy and `orderbook` at 48% still drew ~15% weight each, only
    ~3 points less than the best component -- so five at-or-below-coin-flip
    components comfortably outvoted the one with an edge, and the blend
    scored ~46% while its best single component scored ~58%.

    Two deliberate conservatism guards, because the live history is still
    short (~3 days) and the project's much larger 20k-candle backtest puts
    technical+ML at ~50%, i.e. today's apparent edges may well be noise:
      * the Wilson bound means a thin or lucky record yields edge 0, and when
        nothing clears the bar the defaults are kept rather than reading
        noise as skill;
      * MAX_WEIGHT keeps this an ensemble -- without it, a single marginally
        significant component takes the entire budget (technical would have
        gone to 0.80 on the current data off a 1.6-point Wilson edge).
    """
    known = {c: records[c] for c in COMPONENTS if records.get(c) is not None}
    if not known:
        return dict(DEFAULT_WEIGHTS)

    edges = {c: max(_wilson_lower_bound(k, n) - 0.5, 0.0) for c, (k, n) in known.items()}
    total_edge = sum(edges.values())
    if total_edge <= 0:
        # Nothing has a demonstrated edge over a coin flip yet.
        return dict(DEFAULT_WEIGHTS)

    known_budget = sum(DEFAULT_WEIGHTS[c] for c in known)
    raw = {c: (e / total_edge) * known_budget for c, e in edges.items()}

    # Clamp into [MIN_WEIGHT, MAX_WEIGHT] and renormalize back onto
    # known_budget (so the overall total stays 1). Clamping pushes the freed
    # weight onto the others, which can put them out of range in turn, so
    # repeat until it settles. The overshoot roughly halves each pass, so
    # this converges quickly; the cap is a fixed bound, not a tuning knob,
    # and this only runs once a day.
    for _ in range(100):
        clamped = {c: min(max(v, MIN_WEIGHT), MAX_WEIGHT) for c, v in raw.items()}
        scale = known_budget / sum(clamped.values())
        raw = {c: v * scale for c, v in clamped.items()}
        if all(MIN_WEIGHT - 1e-9 <= v <= MAX_WEIGHT + 1e-9 for v in raw.values()):
            break

    weights = dict(DEFAULT_WEIGHTS)
    weights.update(raw)
    return weights
