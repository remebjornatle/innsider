#!/usr/bin/env python3
"""Fetch insider trade data from Oslo Børs and write docs/data.json.

Run by GitHub Actions daily; output is served as a static file via GitHub Pages.
"""

import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import requests

API_BASE = "https://api3.oslo.oslobors.no"
HEADERS = {"Origin": "https://newsweb.oslobors.no"}
CATEGORY = 1102
DAYS = 90
WORKERS = 20

session = requests.Session()
session.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# Number parsers
# ---------------------------------------------------------------------------

def _clean_number(s):
    return s.strip().replace("\xa0", "").replace(" ", "").replace(" ", "")


def parse_shares(s):
    s = _clean_number(s).replace(",", "").replace(".", "")
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def parse_price(s):
    s = _clean_number(s).rstrip(".,")
    if not s:
        return None

    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        if s.rfind(".") > s.rfind(","):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif has_comma:
        after = s.rsplit(",", 1)[1]
        if len(after) != 3:
            s = s.replace(",", ".")
        else:
            as_thousands = float(s.replace(",", ""))
            as_decimal = float(s.replace(",", "."))
            s = str(as_decimal if as_thousands > 50_000 else as_thousands)
    elif has_dot:
        if s.count(".") > 1:
            s = s.replace(".", "")

    try:
        v = float(s)
        return v if 0 < v < 1_000_000 else None
    except ValueError:
        return None


def parse_number_no(s):
    s = _clean_number(s)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Trade parser
# ---------------------------------------------------------------------------

_BUY_WORDS_EN = r"(?:bought|purchased|acquired)"
_SELL_WORDS_EN = r"(?:sold|disposed\s+of)"
_BUY_WORDS_NO = r"(?:kjøpt|ervervet)"
_SELL_WORDS_NO = r"(?:solgt|avhendet)"

_EN_BUY = re.compile(
    r"(?P<action>" + _BUY_WORDS_EN + r")\s+"
    r"(?P<shares>[\d,\s]+?)\s+shares?\b"
    r"(?:[^.]{0,120}?)"
    r"(?:at|for)\s+(?:an?\s+)?(?:average\s+)?(?:share\s+)?(?:price\s+)?(?:of\s+)?(?:per\s+share\s+of\s+)?(?:NOK|nok)\s+"
    r"(?P<price>[\d,.]+)",
    re.I | re.S,
)
_EN_SELL = re.compile(
    r"(?P<action>" + _SELL_WORDS_EN + r")\s+"
    r"(?P<shares>[\d,\s]+?)\s+shares?\b"
    r"(?:[^.]{0,120}?)"
    r"(?:at|for)\s+(?:an?\s+)?(?:average\s+)?(?:share\s+)?(?:price\s+)?(?:of\s+)?(?:per\s+share\s+of\s+)?(?:NOK|nok)\s+"
    r"(?P<price>[\d,.]+)",
    re.I | re.S,
)
_NO_BUY = re.compile(
    r"(?P<shares>[\d\s.]+?)\s+aksjer"
    r"(?:[^.]{0,200}?)"
    r"(?P<action>" + _BUY_WORDS_NO + r")"
    r"(?:[^.]{0,200}?)"
    r"(?:snittpris|kurs|pris)\b(?:[^NOK]{0,50}?)(?:NOK|kr)\s*(?P<price>[\d.,\s]+)",
    re.I | re.S,
)
_NO_SELL = re.compile(
    r"(?P<shares>[\d\s.]+?)\s+aksjer"
    r"(?:[^.]{0,200}?)"
    r"(?P<action>" + _SELL_WORDS_NO + r")"
    r"(?:[^.]{0,200}?)"
    r"(?:snittpris|kurs|pris)\b(?:[^NOK]{0,50}?)(?:NOK|kr)\s*(?P<price>[\d.,\s]+)",
    re.I | re.S,
)


def parse_trades(body):
    is_norwegian = bool(re.search(r"\baksjer\b|\bkjøpt\b|\bsolgt\b", body, re.I))
    trades = []
    seen = set()

    def add(action, shares_raw, price_raw, parse_fn):
        shares = parse_shares(shares_raw)
        price = parse_price(price_raw)
        if shares and price and shares > 0 and price > 0:
            key = (action, round(shares), round(price * 100))
            if key not in seen:
                seen.add(key)
                trades.append({
                    "action": action,
                    "shares": int(shares),
                    "price": price,
                    "value": round(shares * price),
                })

    if is_norwegian:
        for m in _NO_BUY.finditer(body):
            add("buy", m.group("shares"), m.group("price"), parse_number_no)
        for m in _NO_SELL.finditer(body):
            add("sell", m.group("shares"), m.group("price"), parse_number_no)
    else:
        for m in _EN_BUY.finditer(body):
            add("buy", m.group("shares"), m.group("price"), parse_shares)
        for m in _EN_SELL.finditer(body):
            add("sell", m.group("shares"), m.group("price"), parse_shares)

    return trades


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def fetch_message_list(from_date, to_date):
    params = {"category": CATEGORY, "fromDate": from_date, "toDate": to_date}
    r = session.get(f"{API_BASE}/v1/newsreader/list", params=params, timeout=20)
    r.raise_for_status()
    return r.json()["data"]["messages"]


@lru_cache(maxsize=2000)
def fetch_message_body(message_id):
    r = session.get(
        f"{API_BASE}/v1/newsreader/message",
        params={"messageId": message_id},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["data"]["message"].get("body", "")


def fetch_bodies_parallel(message_ids):
    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fetch_message_body, mid): mid for mid in message_ids}
        for fut in as_completed(futures):
            mid = futures[fut]
            try:
                results[mid] = fut.result()
            except Exception:
                results[mid] = ""
    return results


def is_norwegian_body(body):
    return bool(re.search(r"\baksjer\b|\bkjøpt\b|\bsolgt\b|\btotalt\b", body, re.I))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=DAYS)
    from_date = from_dt.strftime("%Y-%m-%d")
    to_date = to_dt.strftime("%Y-%m-%d")

    print(f"Fetching messages {from_date} → {to_date} …")
    messages = fetch_message_list(from_date, to_date)
    print(f"  {len(messages)} messages found")

    all_ids = [msg["messageId"] for msg in messages]
    bodies = fetch_bodies_parallel(all_ids)

    issuer_day_messages = defaultdict(list)
    for msg in messages:
        day = msg["publishedTime"][:10]
        issuer_day_messages[(msg["issuerSign"], day)].append(msg)

    all_trades = []
    seen_trade_keys = set()

    for msg in messages:
        day = msg["publishedTime"][:10]
        key = (msg["issuerSign"], day)
        twins = issuer_day_messages[key]
        body = bodies.get(msg["messageId"], "")

        if len(twins) > 1 and is_norwegian_body(body):
            continue

        parsed = parse_trades(body)
        for t in parsed:
            tkey = (msg["issuerSign"], day, t["action"], t["shares"], round(t["price"] * 10))
            if tkey in seen_trade_keys:
                continue
            seen_trade_keys.add(tkey)
            all_trades.append({
                "messageId": msg["messageId"],
                "ticker": msg["issuerSign"],
                "company": msg["issuerName"],
                "date": day,
                "action": t["action"],
                "shares": t["shares"],
                "price": t["price"],
                "value": t["value"],
            })

    buy_agg = defaultdict(lambda: {"ticker": "", "company": "", "value": 0, "trades": 0})
    sell_agg = defaultdict(lambda: {"ticker": "", "company": "", "value": 0, "trades": 0})

    for t in all_trades:
        ticker = t["ticker"]
        target = buy_agg if t["action"] == "buy" else sell_agg
        target[ticker]["ticker"] = ticker
        target[ticker]["company"] = t["company"]
        target[ticker]["value"] += t["value"]
        target[ticker]["trades"] += 1

    top_buys = sorted(buy_agg.values(), key=lambda x: x["value"], reverse=True)[:15]
    top_sells = sorted(sell_agg.values(), key=lambda x: x["value"], reverse=True)[:15]
    recent_trades = sorted(all_trades, key=lambda x: x["date"], reverse=True)[:200]

    result = {
        "generated_at": to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from_date": from_date,
        "to_date": to_date,
        "total_parsed": len(all_trades),
        "top_buys": top_buys,
        "top_sells": top_sells,
        "trades": recent_trades,
    }

    os.makedirs("docs", exist_ok=True)
    out_path = os.path.join("docs", "data.json")
    with open(out_path, "w") as f:
        json.dump(result, f, separators=(",", ":"))

    print(f"  {len(all_trades)} trades parsed → {out_path}")


if __name__ == "__main__":
    main()
