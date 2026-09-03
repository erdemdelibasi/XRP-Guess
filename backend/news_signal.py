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
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
QUERY = "XRP OR Ripple"
TIMEOUT = 15
LOOKBACK_HOURS = 6

# Measured on 240 live predictions (2026-09-03): this component said UP in
# 121 of the 127 rows where it had an opinion at all -- effectively a
# constant, not a signal, and at ~15% ensemble weight that's a fixed UP bias
# injected into every blend. The word-boundary fix below was working fine;
# the bias came from two other places:
#
#   1. Score was a SUM of keyword hits across every headline in the window,
#      so signal strength scaled with news *volume*, and one clickbait title
#      stuffed with "surge/rally/bullish" counted three times (six with the
#      regulatory multiplier).
#   2. Crypto headline vocabulary is structurally promotional -- "launch",
#      "partnership", "rally", "surge" appear in routine coverage every hour,
#      while "sued"/"banned"/"fraud"/"hacked" only appear on a real event. So
#      the positive list fires near-constantly and the negative list rarely.
#
# Now: one vote per headline (not per keyword), normalized by total matched
# weight so volume drops out, and it abstains unless the tilt is genuinely
# lopsided. The component should be SILENT most of the time -- that's the
# correct behavior for it, not a malfunction. These two thresholds are
# initial estimates; there's no historical headline archive to fit them
# against, so they need live `predictions` data to validate (check the
# UP/DOWN split again before declaring this fixed -- see CLAUDE.md).
MIN_MATCHED_HEADLINES = 3  # below this, a single headline would drive the whole signal
MIN_IMBALANCE = 0.5        # |balance| under this = no clear tilt -> abstain (0.5 ~ a 3:1 split)

POSITIVE_KEYWORDS = [
    "approve", "approves", "approval", "win", "wins", "victory", "dismiss",
    "dismisses", "favor", "partnership", "adopt", "adopts", "adoption",
    "launch", "launches", "launched", "surge", "surges", "rally", "rallies",
    "bullish",
]
NEGATIVE_KEYWORDS = [
    "sue", "sues", "sued", "lawsuit", "fine", "fines", "fined", "ban",
    "bans", "banned", "reject", "rejects", "rejected", "delay", "delays",
    "delayed", "crash", "crashes", "sell-off", "bearish", "hack", "hacks",
    "hacked", "fraud",
]
REGULATORY_KEYWORDS = [
    "sec", "lawsuit", "court", "ruling", "regulation", "regulatory",
    "approve", "approves", "approval", "etf", "etfs", "settlement", "appeal",
]

# Naive substring matching (the previous approach) false-positives constantly --
# "ban" inside "banking"/"bank", "sec" inside "second"/"sector", "sue" inside
# "issue"/"pursue", "hack" inside "hackathon". Confirmed live: a neutral/bullish
# headline ("This banking giant emerges as top XRP ETF holder") was scored as
# strongly bearish purely because "ban" matched inside "banking". Word-boundary
# matching fixes that, at the cost of needing each inflected form (surges,
# launches, etfs, ...) listed explicitly above instead of falling out "for free."
_WORD_BOUNDARY_CACHE: dict[str, re.Pattern] = {}


def _keyword_hits(title: str, keywords: list[str]) -> list[str]:
    hits = []
    for kw in keywords:
        pattern = _WORD_BOUNDARY_CACHE.get(kw)
        if pattern is None:
            pattern = re.compile(rf"\b{re.escape(kw)}\b")
            _WORD_BOUNDARY_CACHE[kw] = pattern
        if pattern.search(title):
            hits.append(kw)
    return hits

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


def recent_headlines() -> list[dict]:
    """Fetches and returns raw headlines (title + published time) from the
    last LOOKBACK_HOURS, newest data only -- shared with claude_signal.py,
    which wants the actual headline text to reason over rather than just
    this module's keyword-count score. Empty list on any fetch/parse
    failure, same fail-soft contract as news_signal() itself."""
    try:
        headlines = _fetch_headlines()
    except (requests.RequestException, ET.ParseError):
        return []

    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    recent = []
    for item in headlines:
        try:
            when = parsedate_to_datetime(item["pubDate"])
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= since:
            recent.append({"title": item["title"], "published": when.isoformat()})
    return recent


def news_signal() -> dict:
    """Returns {"direction", "confidence", "score"} from recent XRP/Ripple
    headline keyword sentiment. Neutral (confidence 0) if the feed can't be
    fetched/parsed, or no headline in the lookback window matched any
    keyword."""
    headlines = recent_headlines()

    pos_weight = 0.0
    neg_weight = 0.0
    matched = 0
    for item in headlines:
        title = item["title"].lower()
        pos_hits = len(_keyword_hits(title, POSITIVE_KEYWORDS))
        neg_hits = len(_keyword_hits(title, NEGATIVE_KEYWORDS))
        if pos_hits == neg_hits:
            # No lean (either nothing matched, or positive and negative
            # keywords cancel within the same headline) -- don't vote.
            continue

        # One vote per headline, regardless of how many keywords it packs;
        # regulatory/legal headlines have historically moved XRP hardest, so
        # they carry double weight as a *headline*, not per keyword.
        weight = 2.0 if _keyword_hits(title, REGULATORY_KEYWORDS) else 1.0
        matched += 1
        if pos_hits > neg_hits:
            pos_weight += weight
        else:
            neg_weight += weight

    total = pos_weight + neg_weight
    if matched < MIN_MATCHED_HEADLINES or total == 0:
        return dict(NEUTRAL)

    # Normalized by total matched weight, so a busy news hour and a quiet one
    # with the same tilt produce the same confidence.
    balance = (pos_weight - neg_weight) / total
    if abs(balance) < MIN_IMBALANCE:
        return dict(NEUTRAL)

    score = max(-1.0, min(1.0, balance))
    direction = "UP" if score >= 0 else "DOWN"
    return {"direction": direction, "confidence": abs(score), "score": score}
