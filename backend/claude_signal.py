"""Claude API opinion signal: gives Claude the same technical/whale numbers
the other components see, plus the actual recent news headlines (not just
news_signal.py's keyword-count score), and asks for an independent
direction/confidence judgment via one Messages API call per prediction
cycle.

Unlike every other component, this one costs real money -- a paid Anthropic
API call every 15 minutes -- and needs its own ANTHROPIC_API_KEY (a repo
secret, separate from the Supabase/Binance access used elsewhere; see
CLAUDE.md). Returns neutral (confidence 0) if that key isn't set yet, so the
rest of the system is unaffected while it's being provisioned.

Fails soft to neutral on any API/parsing problem, same contract as
whale_signal.py/news_signal.py/orderbook_signal.py -- a hiccup here must
never block a prediction run. Also like those three, this can't be
backtested (no cheap way to replay a paid LLM call against history), so its
real value can only be judged from several weeks of live results.
"""
import json
import os

import anthropic

import news_signal

MODEL = "claude-sonnet-5"  # stronger reasoning than Haiku, ~$5-6/mo at 96 calls/day; see CLAUDE.md
MAX_HEADLINES = 8
NEUTRAL = {"direction": "UP", "confidence": 0.0, "score": 0.0}

SYSTEM_PROMPT = (
    "You are a short-horizon (15-45 minute) XRP/USDT price direction "
    "forecaster for a paper-trading experiment. You'll see the current "
    "technical-indicator signal, an XRPL exchange whale-flow signal, and "
    "recent XRP/Ripple news headlines. Weigh that evidence and reach your own "
    "judgment. Agreeing with the technical signal is a perfectly good answer "
    "when the evidence supports it -- judge it on the merits rather than "
    "either deferring to it or avoiding it. Most of the time "
    "there is no real edge over a coin flip; only report confidence above "
    "roughly 0.15 when you see a genuinely strong, specific reason. Respond "
    "only via the given schema."
)
# The previous wording here was "Form your own independent judgment -- do not
# just restate the technical signal's direction." Intent was to stop this
# component from parroting, but it reads as an instruction to *diverge*, and
# `technical` is the one component with a measured edge. Live data was
# consistent with that backfiring: over its first 73 resolved predictions
# `claude` matched `technical` only 48% of the time, called DOWN in 56 of 73,
# and scored 37% accuracy -- the worst of the six, and anti-correlated rather
# than merely uninformative. That's a hypothesis on a small sample, not a
# proven cause; recheck the direction split and accuracy once a few hundred
# more rows have accumulated before concluding this rewording helped.

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["UP", "DOWN"]},
        # output_config.format's json_schema doesn't support "minimum"/
        # "maximum" on a number property (rejected with a 400) -- the
        # 0..1 range is clamped at runtime instead, below.
        "confidence": {"type": "number"},
    },
    "required": ["direction", "confidence"],
    "additionalProperties": False,
}


def _format_context(current_price: float, tech: dict, whale: dict, headlines: list[dict]) -> str:
    lines = [
        f"Current XRP/USDT price: ${current_price:.4f}",
        f"Technical indicator component: {tech['direction']}, raw confidence {tech['confidence']:.2f}",
        f"XRPL exchange whale-flow component: {whale['direction']}, raw confidence {whale['confidence']:.2f}",
    ]
    if headlines:
        lines.append("Recent XRP/Ripple headlines:")
        lines += [f"- {h['title']}" for h in headlines[:MAX_HEADLINES]]
    else:
        lines.append("No XRP/Ripple headlines in the last few hours.")
    lines.append("Predict XRP/USDT's direction over the next 15-45 minutes.")
    return "\n".join(lines)


def claude_signal(current_price: float, tech: dict, whale: dict) -> dict:
    """Returns {"direction", "confidence", "score"}, matching every other
    signal module's shape. Neutral if ANTHROPIC_API_KEY isn't set, or on any
    API/parsing failure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return dict(NEUTRAL)

    headlines = news_signal.recent_headlines()

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _format_context(current_price, tech, whale, headlines)}],
            output_config={
                # A per-cycle classification-style judgment, not long-horizon
                # reasoning -- low effort keeps the recurring cost down
                # without materially hurting quality for a task this simple.
                # (Sonnet 5 supports `effort`; Haiku 4.5 does not -- drop
                # this key if MODEL is ever switched back to Haiku.)
                "effort": "low",
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
        )
        text = next(b.text for b in response.content if b.type == "text")
        parsed = json.loads(text)
        direction = parsed["direction"]
        confidence = max(0.0, min(1.0, float(parsed["confidence"])))
    except (anthropic.APIStatusError, anthropic.APIConnectionError, StopIteration, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"WARNING: claude_signal failed ({exc}) -- falling back to neutral.")
        return dict(NEUTRAL)

    score = confidence if direction == "UP" else -confidence
    return {"direction": direction, "confidence": confidence, "score": score}
