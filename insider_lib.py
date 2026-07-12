"""Shared logic for the Innsider dashboard: Oslo Børs API access, free-text trade
parsing, insider-name extraction, liquidity data, and aggregation into the three
dashboard views (value / liquidity / insider count).

Used by both server.py (Flask, live) and fetch_data.py (GitHub Actions, static).
"""

import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import requests

API_BASE = "https://api3.oslo.oslobors.no"
HEADERS = {"Origin": "https://newsweb.oslobors.no"}
CATEGORY = 1102

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.OL"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}

session = requests.Session()
session.headers.update(HEADERS)

yahoo_session = requests.Session()
yahoo_session.headers.update(YAHOO_HEADERS)


# ---------------------------------------------------------------------------
# Number parsers
# ---------------------------------------------------------------------------

def _clean_number(s):
    return s.strip().replace("\xa0", "").replace(" ", "").replace(" ", "")


def parse_shares(s):
    """Parse share count: always integer, commas/dots/spaces are thousands separators."""
    s = _clean_number(s).replace(",", "").replace(".", "")
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def parse_price(s):
    """Parse NOK price per share with smart EN/NO format detection.

    Handles mixed formats seen in Oslo Børs disclosures:
      83,7879  → 83.7879  (comma followed by 4 digits = decimal)
      278,5283 → 278.5283 (same)
      45.63    → 45.63    (standard decimal)
      278.5283 → 278.5283 (standard decimal)
      1,234    → 1.234    (comma + 3 digits, but result > reasonable → try decimal)
    """
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
    """Parse Norwegian-format number: 1.234.567,89 or 1 234 567,89 → float."""
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
    """Return list of {action, shares, price, value} dicts from a message body."""
    is_norwegian = bool(re.search(r"\baksjer\b|\bkjøpt\b|\bsolgt\b", body, re.I))
    trades = []
    seen = set()

    def add(action, shares_raw, price_raw):
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
            add("buy", m.group("shares"), m.group("price"))
        for m in _NO_SELL.finditer(body):
            add("sell", m.group("shares"), m.group("price"))
    else:
        for m in _EN_BUY.finditer(body):
            add("buy", m.group("shares"), m.group("price"))
        for m in _EN_SELL.finditer(body):
            add("sell", m.group("shares"), m.group("price"))

    return trades


def is_norwegian_body(body):
    return bool(re.search(r"\baksjer\b|\bkjøpt\b|\bsolgt\b|\btotalt\b", body, re.I))


# ---------------------------------------------------------------------------
# Insider-name extraction
#
# Best-effort: Oslo Børs disclosures follow a handful of common templates.
# Direct: "<Name>, <role> ... has/bought/sold ..."
# Related-party: "<Entity>, a related party to <role> in <Issuer> <Name>, has ..."
# When neither template matches, the trade is still counted for value/trade-count
# purposes, just not attributed to a named insider (excluded from the "by
# distinct insiders" view rather than guessed at).
# ---------------------------------------------------------------------------

# Deliberately case-sensitive: an initial capital is what tells a real name
# ("Erland Lønnerød") apart from boilerplate ("primary insiders", a section
# header, an email address). Only single spaces/tabs join name words, so a
# match can't stretch across a line break into an unrelated paragraph. Role
# keywords are wrapped in (?i:...) below so *they* still match any casing.
_NAME = r"[A-ZÆØÅ][a-zæøåA-ZÆØÅ'\-\.]*(?:[ \t]+[A-ZÆØÅ][a-zæøåA-ZÆØÅ'\-\.]*){1,3}"
_ROLE_EN = (
    r"(?i:board member|member of the board|primary insider|chairman|vice chairman|"
    r"director|chief\s+\w+(?:\s+\w+)?|executive vice president|senior vice president|"
    r"EVP|SVP|CEO|CFO|COO|president)"
)
_ROLE_NO = (
    r"(?i:styremedlem|styreleder|nestleder|primærinnsider|"
    r"administrerende direktør|finansdirektør|konsernsjef|daglig leder)"
)


def extract_insider_name(body, issuer_name):
    """Return the primary insider's name, or None if no known template matches."""
    esc_issuer = r"\s+".join(re.escape(w) for w in issuer_name.split())

    rel = re.search(
        r"(?i:related\s+party\s+to)[^,\n]*?(?i:\bin\s+)" + esc_issuer + r"\s+(?P<name>" + _NAME + r")\s*,",
        body,
    )
    if rel:
        return rel.group("name").strip()

    for role in (_ROLE_EN, _ROLE_NO):
        # Up to ~45 chars of qualifier text between name and role keyword
        # ("deputy employee elected board member"), but never across a comma
        # or newline, so it can't drift into an unrelated clause.
        m = re.search(
            r"(?P<name>" + _NAME + r")\s*,\s*(?:[^,\n]{0,45})?\b" + role + r"\b",
            body,
        )
        if m:
            return m.group("name").strip()

    return None


# ---------------------------------------------------------------------------
# Oslo Børs API helpers
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


def fetch_bodies_parallel(message_ids, workers=20):
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_message_body, mid): mid for mid in message_ids}
        for fut in as_completed(futures):
            mid = futures[fut]
            try:
                results[mid] = fut.result()
            except Exception:
                results[mid] = ""
    return results


# ---------------------------------------------------------------------------
# Liquidity data (Yahoo Finance chart endpoint, unauthenticated)
#
# Used to normalize trade value against how much of this stock typically
# changes hands in a day, rather than against raw NOK size. Best-effort: a
# ticker that fails to resolve (renamed, delisted, no Yahoo listing) is simply
# left out of the liquidity view rather than guessed at.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=500)
def fetch_avg_daily_value(ticker):
    """Return the average daily traded value (NOK) over the last ~1 month, or None."""
    try:
        r = yahoo_session.get(
            YAHOO_CHART.format(symbol=ticker),
            params={"range": "1mo", "interval": "1d"},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        pairs = [
            (v, c) for v, c in zip(quote["volume"], quote["close"])
            if v is not None and c is not None
        ]
        if not pairs:
            return None
        return sum(v * c for v, c in pairs) / len(pairs)
    except Exception:
        return None


def fetch_market_data_parallel(tickers, workers=10):
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_avg_daily_value, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                results[t] = fut.result()
            except Exception:
                results[t] = None
    return results


# ---------------------------------------------------------------------------
# Aggregation: build the full dataset backing all three dashboard views
# ---------------------------------------------------------------------------

def build_dataset(from_date, to_date, message_fetch_workers=20, market_fetch_workers=10):
    messages = fetch_message_list(from_date, to_date)

    all_ids = [msg["messageId"] for msg in messages]
    bodies = fetch_bodies_parallel(all_ids, workers=message_fetch_workers)

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
        if not parsed:
            continue

        insider = extract_insider_name(body, msg["issuerName"])

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
                "insider": insider,
            })

    # Aggregate by ticker
    buy_agg = defaultdict(lambda: {"ticker": "", "company": "", "value": 0, "trades": 0, "_insiders": set()})
    sell_agg = defaultdict(lambda: {"ticker": "", "company": "", "value": 0, "trades": 0, "_insiders": set()})

    for t in all_trades:
        ticker = t["ticker"]
        target = buy_agg if t["action"] == "buy" else sell_agg
        target[ticker]["ticker"] = ticker
        target[ticker]["company"] = t["company"]
        target[ticker]["value"] += t["value"]
        target[ticker]["trades"] += 1
        if t["insider"]:
            target[ticker]["_insiders"].add(t["insider"])

    all_tickers = set(buy_agg) | set(sell_agg)
    market_data = fetch_market_data_parallel(sorted(all_tickers), workers=market_fetch_workers)

    def finalize(agg):
        items = []
        for entry in agg.values():
            adv = market_data.get(entry["ticker"])
            items.append({
                "ticker": entry["ticker"],
                "company": entry["company"],
                "value": entry["value"],
                "trades": entry["trades"],
                "insiders": len(entry["_insiders"]),
                "avg_daily_value": round(adv) if adv else None,
                "pct_adv": round(entry["value"] / adv * 100, 1) if adv else None,
            })
        return items

    buys = finalize(buy_agg)
    sells = finalize(sell_agg)

    def top(items, key, n=15):
        ranked = [x for x in items if x[key]]  # excludes None and 0/empty
        return sorted(ranked, key=lambda x: x[key], reverse=True)[:n]

    recent_trades = sorted(
        [{k: v for k, v in t.items()} for t in all_trades],
        key=lambda x: x["date"], reverse=True,
    )[:200]

    return {
        "from_date": from_date,
        "to_date": to_date,
        "total_parsed": len(all_trades),
        "total_buy_value": sum(x["value"] for x in buys),
        "total_sell_value": sum(x["value"] for x in sells),
        "buys": {
            "by_value": top(buys, "value"),
            "by_liquidity": top(buys, "pct_adv"),
            "by_insiders": top(buys, "insiders"),
        },
        "sells": {
            "by_value": top(sells, "value"),
            "by_liquidity": top(sells, "pct_adv"),
            "by_insiders": top(sells, "insiders"),
        },
        "trades": recent_trades,
    }
