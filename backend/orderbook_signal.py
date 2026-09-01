"""Live order-book imbalance signal: more bid-side depth than ask-side depth
near the current price is read as short-term buying pressure (and vice
versa). Uses Binance's public depth snapshot -- no key required.

This is a point-in-time market microstructure signal with no free historical
archive, so unlike the technical/ML components it cannot be backtested; it's
only ever computed live, the same way whale/news are.
"""
from fetch_data import get_order_book_imbalance

SYMBOL = "XRPUSDT"
FULL_CONFIDENCE_IMBALANCE = 0.35  # imbalance magnitude mapping to confidence 1.0


def orderbook_signal() -> dict:
    imbalance = get_order_book_imbalance(SYMBOL)
    score = max(-1.0, min(1.0, imbalance / FULL_CONFIDENCE_IMBALANCE))
    direction = "UP" if score >= 0 else "DOWN"
    return {"direction": direction, "confidence": abs(score), "score": score}
