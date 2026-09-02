"""Kanal Finans TS (YouTube @KanalFinans, Tunc Satiroglu) independent opinion
feed: watches the channel's public RSS for new videos, pulls each video's
Turkish transcript, and asks Claude to extract short, faithful summaries of
whatever the speaker said about XRP/BTC/ETH/crypto -- along with his own
stance (UP/DOWN/NEUTRAL), not ours.

This is explicitly NOT an ensemble component (see ensemble.COMPONENTS in
ensemble.py and CLAUDE.md) -- we aren't forming an opinion here, just
reporting one person's. predict.py never imports this module; it runs as its
own standalone script on its own cron (see .github/workflows/kanal_finans.yml).

Both the RSS feed and the transcript API are free/keyless, matching the
project's general preference for public, no-signup data sources. The
transcript API in particular is known to get IP-blocked from cloud/datacenter
runners from time to time -- the exact class of problem this project already
hit with api.binance.com (see CLAUDE.md), which is why data-api.binance.vision
is used there instead. There's no equivalent alternate host here, so instead
this module fails soft per-video: a video is only written to
kanal_finans_videos once BOTH the transcript fetch AND the Claude extraction
succeed. Any failure at either step leaves the video unrecorded, so the next
cron run (likely from a different GitHub Actions runner IP) retries it
automatically -- a stuck video is never lost, just delayed.
"""
import json
import os
import xml.etree.ElementTree as ET

import anthropic
import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

import db as db_module

CHANNEL_ID = "UCGBytjbMXiF1nbe6HD7iORQ"  # resolved once from youtube.com/@KanalFinans's canonical link; stable even if the handle changes
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=" + CHANNEL_ID
TIMEOUT = 15
MAX_VIDEOS_PER_RUN = 15  # RSS feed itself only ever returns ~15 entries

ASSETS = ("XRP", "BTC", "ETH", "KRIPTO")
MODEL = "claude-sonnet-5"  # same model claude_signal.py uses; see CLAUDE.md for why Sonnet over Haiku

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

SYSTEM_PROMPT = (
    "Sana Kanal Finans YouTube kanalindaki bir video icin Turkce transkript "
    "verilecek. Kanalin sunucusu Tunc Satiroglu'nun XRP, BTC, ETH veya genel "
    "kripto piyasasi hakkinda soyledigi seyleri sadakatle raporlamani "
    "istiyorum -- KENDI GORUSUNU KATMA, sadece konusmacinin ne dedigini "
    "ozetle. Sadece net bir goruse/beklentiye isaret eden yerleri yakala; "
    "sadece bir varlik ismini gecici olarak anmasi yeterli degil. Video "
    "kripto konusuna hic deginmiyorsa (orn. sadece hisse/ETF/altin/forex "
    "konusuyorsa) bos bir liste dondur. Her bahis icin: hangi varlik "
    "(XRP/BTC/ETH -- bu ucune girmeyen ama genel kripto piyasasindan "
    "bahseden yorumlar icin KRIPTO), kisa (tek cumle, Turkce) bir ozet, ve "
    "konusmacinin tonuna gore stance (UP=yukselis bekliyor/olumlu, "
    "DOWN=dusus bekliyor/olumsuz, NEUTRAL=kararsiz/net yon belirtmemis)."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "enum": list(ASSETS)},
                    "summary": {"type": "string"},
                    "stance": {"type": "string", "enum": ["UP", "DOWN", "NEUTRAL"]},
                },
                "required": ["asset", "summary", "stance"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["mentions"],
    "additionalProperties": False,
}


def get_recent_videos() -> list[dict]:
    """Returns [{"video_id", "title", "published"}] from the channel's public
    RSS feed, newest first. Empty list on any network/parse failure -- a
    fetch hiccup here must never crash the run, same fail-soft contract as
    every other signal module in this project."""
    try:
        resp = requests.get(RSS_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError):
        return []

    videos = []
    for entry in root.findall("atom:entry", _ATOM_NS)[:MAX_VIDEOS_PER_RUN]:
        video_id = entry.findtext("yt:videoId", default="", namespaces=_ATOM_NS)
        title = entry.findtext("atom:title", default="", namespaces=_ATOM_NS)
        published = entry.findtext("atom:published", default="", namespaces=_ATOM_NS)
        if video_id:
            videos.append({"video_id": video_id, "title": title, "published": published})
    return videos


def get_processed_ids(db) -> set[str]:
    resp = db.table("kanal_finans_videos").select("video_id").execute()
    return {row["video_id"] for row in resp.data}


def fetch_transcript(video_id: str) -> str | None:
    """Turkish transcript text for a video, or None if unavailable/blocked.
    Tries manually-uploaded/auto-generated Turkish first (the channel is
    Turkish-language), then falls back to whatever transcript the video
    does have -- some videos only carry an auto-translated track."""
    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["tr", "tr-TR"])
    except CouldNotRetrieveTranscript as exc:
        print(f"kanal_finans: tr transcript fetch failed for {video_id} ({type(exc).__name__}: {exc})")
        try:
            transcript_list = api.list(video_id)
            fetched = next(iter(transcript_list)).fetch()
        except (CouldNotRetrieveTranscript, StopIteration) as exc2:
            print(f"kanal_finans: fallback transcript fetch also failed for {video_id} ({type(exc2).__name__}: {exc2})")
            return None
    except Exception as exc:
        # Covers network-level failures (e.g. an IP-blocked cloud runner)
        # that aren't a CouldNotRetrieveTranscript subclass.
        print(f"kanal_finans: transcript fetch network error for {video_id} ({type(exc).__name__}: {exc})")
        return None

    text = " ".join(snippet.text for snippet in fetched)
    return text or None


def extract_mentions(video_title: str, transcript: str) -> list[dict] | None:
    """Returns a list of {"asset", "summary", "stance"} dicts (possibly
    empty, if the video never mentions crypto), or None on any API/parsing
    failure -- None (not []) so main() knows to retry rather than record an
    empty result."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    user_content = f"Video basligi: {video_title}\n\nTranskript:\n{transcript}"
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_config={
                "effort": "low",  # short extraction/classification task, not deep reasoning
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
        )
        text = next(b.text for b in response.content if b.type == "text")
        parsed = json.loads(text)
        return parsed["mentions"]
    except (anthropic.APIStatusError, anthropic.APIConnectionError, StopIteration, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"WARNING: kanal_finans extract_mentions failed for '{video_title}' ({exc}).")
        return None


def save_video_and_mentions(db, video: dict, transcript_found: bool, mentions: list[dict]) -> None:
    db.table("kanal_finans_videos").insert({
        "video_id": video["video_id"],
        "video_title": video["title"],
        "published_at": video["published"] or None,
        "transcript_found": transcript_found,
    }).execute()

    if mentions:
        rows = [
            {
                "video_id": video["video_id"],
                "video_title": video["title"],
                "published_at": video["published"] or None,
                "asset": m["asset"],
                "summary": m["summary"],
                "stance": m["stance"],
            }
            for m in mentions
        ]
        db.table("kanal_finans_mentions").insert(rows).execute()


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # No point fetching transcripts (each one an extra request against a
        # host known to IP-block cloud runners) when extract_mentions() would
        # just fail anyway -- same no-op-without-a-key contract claude_signal.py
        # has for the ensemble.
        print("kanal_finans: ANTHROPIC_API_KEY not set, skipping this run.")
        return

    db = db_module.get_client()

    videos = get_recent_videos()
    if not videos:
        print("kanal_finans: RSS feed unavailable or empty this run, nothing to do.")
        return

    processed = get_processed_ids(db)
    new_videos = [v for v in videos if v["video_id"] not in processed]
    if not new_videos:
        print("kanal_finans: no new videos since last run.")
        return

    for video in new_videos:
        transcript = fetch_transcript(video["video_id"])
        if transcript is None:
            print(f"kanal_finans: transcript unavailable for '{video['title']}' ({video['video_id']}), will retry next run.")
            continue

        mentions = extract_mentions(video["title"], transcript)
        if mentions is None:
            print(f"kanal_finans: Claude extraction failed for '{video['title']}' ({video['video_id']}), will retry next run.")
            continue

        save_video_and_mentions(db, video, transcript_found=True, mentions=mentions)
        print(f"kanal_finans: processed '{video['title']}' -- {len(mentions)} mention(s).")


if __name__ == "__main__":
    main()
