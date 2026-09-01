"""XRP Ledger whale/exchange-flow signal: tracks large native-XRP transfers
into and out of known exchange-tagged wallets over a recent window, using
XRPSCAN's public API (no key required, no Binance account involved). Net
inflow to exchanges is treated as bearish (deposits often precede selling);
net outflow is treated as bullish (withdrawal to self-custody / accumulation).

Fails soft to a neutral signal on any network/data problem -- a hiccup here
must never block a prediction run.
"""
from datetime import datetime, timedelta, timezone

import requests

XRPSCAN_BASE = "https://api.xrpscan.com/api/v1"
WELL_KNOWN_URL = f"{XRPSCAN_BASE}/names/well-known"
TIMEOUT = 15

# Case-insensitive substring match against XRPSCAN's well-known account
# names -- limits tracking to major exchanges instead of every tagged wallet
# (NFT projects, gateways, etc).
EXCHANGE_NAME_HINTS = [
    "binance", "coinbase", "kraken", "bitstamp", "bitfinex", "okx", "upbit",
    "bybit", "kucoin", "huobi", "htx", "gate.io", "crypto.com", "bitso",
]

LARGE_TX_THRESHOLD_XRP = 100_000  # a commonly used on-chain "whale" cutoff
LOOKBACK_MINUTES = 120
MAX_TRACKED_ACCOUNTS = 8  # cap outbound requests per run
FULL_CONFIDENCE_NET_USD = 2_000_000  # net flow magnitude that maps to confidence 1.0

NEUTRAL = {"direction": "UP", "confidence": 0.0, "score": 0.0}


def get_exchange_accounts() -> list[str]:
    """Fetches XRPSCAN's public well-known-accounts list and returns the
    addresses whose label looks like a major exchange. Empty list on any
    failure (caller treats that as "no data available")."""
    try:
        resp = requests.get(WELL_KNOWN_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        entries = resp.json()
    except (requests.RequestException, ValueError):
        return []

    accounts = []
    for entry in entries:
        name = (entry.get("name") or "").lower()
        account = entry.get("account")
        if account and any(hint in name for hint in EXCHANGE_NAME_HINTS):
            accounts.append(account)
    return accounts[:MAX_TRACKED_ACCOUNTS]


def _xrp_amount(amount) -> float | None:
    """XRPL Payment `Amount` fields for native XRP are drops, either as a
    plain numeric string or as {"currency": "XRP", "value": <drops>}."""
    if isinstance(amount, str):
        try:
            drops = float(amount)
        except ValueError:
            return None
    elif isinstance(amount, dict):
        if amount.get("currency") != "XRP":
            return None  # issued token, not native XRP
        try:
            drops = float(amount.get("value", 0))
        except (TypeError, ValueError):
            return None
    else:
        return None
    return drops / 1_000_000


def _net_flow_for_account(account: str, since: datetime) -> float:
    """Net XRP flow INTO `account` (positive = inflow, negative = outflow)
    from large native-XRP Payments since `since`. Only inspects the most
    recent page of transactions -- for very high-volume wallets this may
    miss older large transfers within the lookback window, which is an
    accepted trade-off to keep this call cheap and resilient."""
    try:
        resp = requests.get(f"{XRPSCAN_BASE}/account/{account}/transactions", timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return 0.0

    net = 0.0
    for tx in data.get("transactions", []):
        if tx.get("TransactionType") != "Payment" or not tx.get("validated"):
            continue
        tx_date = tx.get("date")
        if not tx_date:
            continue
        try:
            when = datetime.fromisoformat(tx_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < since:
            continue

        xrp_amount = _xrp_amount(tx.get("Amount"))
        if xrp_amount is None or xrp_amount < LARGE_TX_THRESHOLD_XRP:
            continue

        if tx.get("Destination") == account:
            net += xrp_amount
        elif tx.get("Account") == account:
            net -= xrp_amount
    return net


def whale_signal(current_price: float) -> dict:
    """Returns {"direction", "confidence", "score"} from recent net XRP flow
    across known exchange wallets. Neutral (confidence 0) if no tagged
    accounts could be fetched or no qualifying large transfers were found."""
    accounts = get_exchange_accounts()
    if not accounts:
        return dict(NEUTRAL)

    since = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)
    total_net_xrp = sum(_net_flow_for_account(account, since) for account in accounts)
    if total_net_xrp == 0:
        return dict(NEUTRAL)

    net_usd = total_net_xrp * current_price
    # Net inflow to exchanges -> bearish (score < 0); net outflow -> bullish.
    score = max(-1.0, min(1.0, -(net_usd / FULL_CONFIDENCE_NET_USD)))
    direction = "UP" if score >= 0 else "DOWN"
    return {"direction": direction, "confidence": abs(score), "score": score}
