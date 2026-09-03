"""Combines every signal component (technical, ml, whale, news, ...) into one
final prediction, weighted by each component's recent track record."""
import math

COMPONENTS = ["technical", "ml", "whale", "news", "orderbook", "claude"]
DEFAULT_WEIGHTS = {"technical": 0.24, "ml": 0.24, "whale": 0.12, "news": 0.10, "orderbook": 0.15, "claude": 0.15}
# Log-odds pooling (see combine()). All three were measured on live data
# rather than picked -- the reasoning and the numbers are in combine()'s
# docstring, and re-measuring them is the right move once the live history is
# a few weeks long instead of a few days.
SHRINK_ALPHA = 30.0        # pseudo-observations pulling a component's P(correct) toward 0.5
LOGODDS_CAP = 1.5          # per-component ceiling, so no single record dominates the pool
CORRELATION_DAMPING = 0.7  # components aren't independent; undamped the pool is overconfident

# predictions-table column prefix per component. "technical" is shortened to
# "tech" there to keep column names compact; every other component's columns
# use its own name as-is.
COLUMN_PREFIX = {"technical": "tech", "ml": "ml", "whale": "whale", "news": "news", "orderbook": "orderbook", "claude": "claude"}


def reliability(correct: int, total: int) -> float:
    """Shrunk estimate of P(this component's call is right), pulled toward 0.5
    by SHRINK_ALPHA pseudo-observations. Thin evidence therefore lands near
    0.5 -> log-odds ~0 -> the component is automatically silent, and only
    earns a voice as its record accumulates."""
    return (correct + SHRINK_ALPHA) / (total + 2 * SHRINK_ALPHA)


def _component_logodds(correct: int, total: int) -> float:
    p = reliability(correct, total)
    return max(-LOGODDS_CAP, min(LOGODDS_CAP, math.log(p / (1 - p))))


def _legacy_combine(signals: dict[str, dict], weights: dict[str, float]) -> dict:
    """Confidence-weighted vote -- the original rule. Only used as a cold-start
    fallback (no reliability history yet) and by backtest.py, which replays
    technical+ml with fixed weights and has no live record to draw on."""
    combined_score = 0.0
    for name, signal in signals.items():
        w = weights.get(name, 0.0)
        score = signal["confidence"] if signal["direction"] == "UP" else -signal["confidence"]
        combined_score += w * score
    return {
        "direction": "UP" if combined_score >= 0 else "DOWN",
        "confidence": min(abs(combined_score), 1.0),
        "score": combined_score,
        "weights_used": dict(weights),
    }


def combine(signals: dict[str, dict], weights: dict[str, float] | None = None,
            reliabilities: dict[str, tuple[int, int]] | None = None) -> dict:
    """Pools the components into one directional call.

    `reliabilities` maps a component to its live (correct, resolved) record.
    When given, components are pooled in LOG-ODDS space by how reliable each
    has actually proven to be -- their stated confidence is deliberately
    ignored for direction. Without it (cold start, or backtest.py) this falls
    back to the original confidence-weighted vote.

    Why log-odds rather than weighting each component's stated confidence:
    the stated confidences were never on a comparable scale. calibration.py
    is applied only to technical and ml and shrinks them hard, while
    whale/news/orderbook/claude kept raw confidences 5-10x larger. Measured
    2026-09-03 over 242 live predictions, effective influence (weight x
    confidence) was whale 26.5%, news 22.3%, claude 18.1%, orderbook 15.6%,
    ml 11.5% and technical just 6.0% -- i.e. the only component with an edge
    (57.4%) had the smallest voice and the worst (whale, 42.6%) the largest.
    Fixing the weights alone could not repair that, because the contribution
    is weight x confidence and confidence was the dominant term.

    Log-odds pooling fixes it structurally: every component enters on one
    scale, P(correct). A component at 0.5 contributes exactly nothing, and
    one that is reliably WRONG contributes negatively -- it gets inverted,
    which is the mathematically right thing to do with an anti-predictive
    signal rather than merely down-weighting it. That also makes a separate
    accuracy-based weight scheme redundant: the log-odds *is* the weight.

    Measured walk-forward, out-of-sample (n=162): this rule scores 55.6% vs
    42.0% for the old confidence-weighted vote, 45.7% for a plain majority
    and 53.1% for always calling DOWN. Scaling each contribution by the
    component's stated confidence was tried and was worse (53.7%), which is
    consistent with those stated confidences not meaning much.

    CORRELATION_DAMPING is applied to the summed log-odds because the
    components are not conditionally independent (technical, ml and orderbook
    all derive from price), so a naive Bayes sum double-counts evidence and
    comes out overconfident. Undamped, this claimed 58.0% while delivering
    55.6%; at 0.7 it claims 55.7% against 55.6% delivered, i.e. calibrated.
    Damping does NOT change direction or accuracy -- only the magnitude of
    the confidence, which is what trading.py scales position size by, so it
    is purely a bet-sizing correction.
    """
    weights = weights or DEFAULT_WEIGHTS
    usable = {c: reliabilities[c] for c in signals
              if reliabilities and c in reliabilities and reliabilities[c][1] > 0}
    if not usable:
        return _legacy_combine(signals, weights)

    total_logodds = 0.0
    for name, signal in signals.items():
        if name not in usable:
            continue
        if signal["confidence"] <= 0:
            continue  # abstaining this cycle -- contributes no evidence
        lo = _component_logodds(*usable[name])
        total_logodds += lo if signal["direction"] == "UP" else -lo

    total_logodds *= CORRELATION_DAMPING
    p_up = 1.0 / (1.0 + math.exp(-total_logodds))
    # Signed edge in -1..1: |score| is the probability edge over a coin flip,
    # and indicators.estimate_pct_change() expects exactly that range.
    score = 2.0 * p_up - 1.0

    return {
        "direction": "UP" if score >= 0 else "DOWN",
        "confidence": abs(score),
        "score": score,
        "weights_used": dict(weights),
    }


def influence_weights(reliabilities: dict[str, tuple[int, int] | None]) -> dict:
    """Each component's share of the total evidence the pool draws on, for
    display only (`model_state.weight`, the UI, the daily mail) -- combine()
    does not consume these; it pools the records directly.

    **Signed**: the magnitudes sum to 1, and the sign says which way the
    component is being read. Negative means it has been reliably wrong and is
    therefore inverted -- it still carries real influence (that's why the
    magnitude can be large), just in the opposite direction to what it says.
    Displaying only magnitudes would put `claude` at the top of the list right
    now purely because it is the most anti-predictive, which reads as "most
    trusted" and is exactly backwards.
    """
    signed = {}
    for c in COMPONENTS:
        rec = reliabilities.get(c)
        if not rec or rec[1] <= 0:
            continue
        signed[c] = _component_logodds(*rec)
    total = sum(abs(v) for v in signed.values())
    if not signed or total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {c: signed.get(c, 0.0) / total for c in COMPONENTS}
