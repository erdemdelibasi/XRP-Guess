"""Regulatory/news sentiment signal: pulls recent XRP/Ripple headlines from
Google News' free RSS search (no key, no signup, no rate-limit paywall) and
scores them by keyword match. Regulatory/legal headlines (SEC, lawsuit,
court, approval, etc.) are the single biggest historical driver of
XRP-specific price moves, so they get extra weight.

This is a weaker signal than a community-vote-based sentiment API (there's
no vote data here, just headline text) -- that's exactly why it stays a
small slice of the ensemble weight, and why retrain.py only starts trusting
it once it has built up enough resolved history of its own.

Fails soft to a neutral signal on any network/parsing problem -- a hiccup
here must never block a prediction run.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
QUERY = "XRP OR Ripple"
TIMEOUT = 15
LOOKBACK_HOURS = 6
FULL_CONFIDENCE_HIT_COUNT = 6  # net keyword-weighted headline score mapping to confidence 1.0

POSITIVE_KEYWORDS = [
    "approve", "approval", "win", "wins", "victory", "dismiss", "favor",
    "partnership", "adopt", "adoption", "launch", "surge", "rally", "bullish",
]
NEGATIVE_KEYWORDS = [
    "sue", "sues", "sued", "lawsuit", "fine", "fined", "ban", "reject",
    "delay", "delayed", "crash", "sell-off", "bearish", "hack", "hacked", "fraud",
]
REGULATORY_KEYWORDS = [
    "sec", "lawsuit", "court", "ruling", "regulation", "regulatory",
    "approve", "approval", "etf", "settlement", "appeal",
]

NEUTRAL = {"direction": "UP", "confidence": 0.0, "score": 0.0}


def _fetch_headlines() -> list[dict]:
    params = {"q": QUERY, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    resp = requests.get(GOOGLE_NEWS_RSS_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return [
        {"title": item.findtext("title") or "", "pubDate": item.findtext("pubDate") or ""}
        for item in root.iter("item")
    ]


def news_signal() -> dict:
    """Returns {"direction", "confidence", "score"} from recent XRP/Ripple
    headline keyword sentiment. Neutral (confidence 0) if the feed can't be
    fetched/parsed, or no headline in the lookback window matched any
    keyword."""
    try:
        headlines = _fetch_headlines()
    except (requests.RequestException, ET.ParseError):
        return dict(NEUTRAL)

    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    net_score = 0.0
    for item in headlines:
        try:
            when = parsedate_to_datetime(item["pubDate"])
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < since:
            continue

        title = item["title"].lower()
        pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in title)
        neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in title)
        if pos_hits == 0 and neg_hits == 0:
            continue

        weight = 2.0 if any(kw in title for kw in REGULATORY_KEYWORDS) else 1.0
        net_score += (pos_hits - neg_hits) * weight

    if net_score == 0:
        return dict(NEUTRAL)

    score = max(-1.0, min(1.0, net_score / FULL_CONFIDENCE_HIT_COUNT))
    direction = "UP" if score >= 0 else "DOWN"
    return {"direction": direction, "confidence": abs(score), "score": score}
