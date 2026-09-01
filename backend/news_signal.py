"""Regulatory/news sentiment signal: pulls recent XRP-tagged posts from
CryptoPanic's free API and turns community bullish/bearish votes into a
directional score, with extra weight for likely regulatory headlines (the
single biggest historical driver of XRP-specific price moves, e.g. the
SEC-Ripple case).

Requires a free CryptoPanic API key (https://cryptopanic.com/developers/api/)
in the CRYPTOPANIC_API_KEY environment variable. Fails soft to a neutral
signal if the key is missing, the API errors, or it's rate-limited -- this
must never block a prediction run.
"""
import os
from datetime import datetime, timedelta, timezone

import requests

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
TIMEOUT = 15
LOOKBACK_HOURS = 6
FULL_CONFIDENCE_NET_VOTES = 40  # net (positive - negative) vote total mapping to confidence 1.0

REGULATORY_KEYWORDS = [
    "sec", "lawsuit", "court", "ruling", "regulation", "regulatory",
    "approve", "approval", "etf", "settlement", "appeal", "ripple",
]

NEUTRAL = {"direction": "UP", "confidence": 0.0, "score": 0.0}


def _fetch_posts(api_key: str) -> list[dict]:
    params = {"auth_token": api_key, "currencies": "XRP"}
    resp = requests.get(CRYPTOPANIC_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("results", [])


def news_signal() -> dict:
    """Returns {"direction", "confidence", "score"} from recent XRP news
    sentiment. Neutral (confidence 0) if no API key is configured, the
    request fails, or no relevant posts had any votes in the lookback
    window."""
    api_key = os.environ.get("CRYPTOPANIC_API_KEY")
    if not api_key:
        return dict(NEUTRAL)

    try:
        posts = _fetch_posts(api_key)
    except (requests.RequestException, ValueError):
        return dict(NEUTRAL)

    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    net_score = 0.0
    for post in posts:
        published = post.get("published_at")
        if not published:
            continue
        try:
            when = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < since:
            continue

        votes = post.get("votes") or {}
        net_votes = float(votes.get("positive", 0)) - float(votes.get("negative", 0))
        if net_votes == 0:
            continue

        title = (post.get("title") or "").lower()
        weight = 2.0 if any(kw in title for kw in REGULATORY_KEYWORDS) else 1.0
        net_score += net_votes * weight

    if net_score == 0:
        return dict(NEUTRAL)

    score = max(-1.0, min(1.0, net_score / FULL_CONFIDENCE_NET_VOTES))
    direction = "UP" if score >= 0 else "DOWN"
    return {"direction": direction, "confidence": abs(score), "score": score}
