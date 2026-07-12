# Innsider — Oslo Børs Insider Trading Dashboard

## Running the app
```
python3 server.py
# → http://localhost:5050
```
Requires: `flask`, `requests` (both already installed).

## Architecture

Two parallel frontends share one backend module:
- **`insider_lib.py`** — shared core: Oslo Børs API access, free-text trade parsing, insider-name extraction, liquidity lookup (Yahoo Finance), and `build_dataset()` which aggregates everything into the three dashboard views (value / liquidity / insider count). Both consumers below call this — don't duplicate its logic.
- **`server.py`** — Flask app for local/live use. `GET /api/trades?days=N` calls `build_dataset()` on demand.
- **`fetch_data.py`** — run daily by `.github/workflows/fetch.yml` (cron `07:00 UTC`). Calls `build_dataset()` for a fixed 90-day window and writes `docs/data.json`, which GitHub Pages serves statically at `https://remebjornatle.github.io/innsider/`. That commit-back-to-main pattern means `git log` on this repo includes a daily `chore: update insider trade data [skip ci]` commit.
- **`templates/index.html`** and **`docs/index.html`** — near-identical Chart.js frontends (one fetches `/api/trades`, the other fetches the static `data.json`). Kept manually in sync; the templates/ version additionally has 7d/30d/90d/180d range buttons since it can re-query on demand.

## API source

The Oslo Børs newsweb at `newsweb.oslobors.no` is a React SPA. The real API base URL is fetched at runtime from `/urls.json` → field `api_large`:

```
https://api3.oslo.oslobors.no
```

Key endpoints used:
- `GET /v1/newsreader/list?category=1102&fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD`
- `GET /v1/newsreader/message?messageId=<id>`

Category **1102** = "Managers' Transaction" (mandatory insider trade notifications under MAR).

The API has CORS restricted to `Origin: newsweb.oslobors.no`, so the Flask backend acts as a proxy.

## Data parsing

Trade details (action, shares, price) live in a **free-text `body` field** — there is no structured trade API. The parser (`parse_trades()` in `server.py`) uses regex against the body text.

### Number format edge cases
Oslo Børs announcements mix English and Norwegian number formats in the same message:
- English: `1,234,567.89` (comma = thousands, dot = decimal)
- Norwegian: `1.234.567,89` (dot = thousands, comma = decimal)
- Mixed (EN message, NO decimal): `NOK 83,7879` → `83.7879`

`parse_price()` disambiguates by counting digits after the last separator. `parse_shares()` strips all separators (shares are always integers).

Trailing sentence punctuation (`.` at end of sentence captured by `[\d,.]+`) is stripped with `.rstrip(".,")` before parsing.

### Deduplication
Many announcements are filed in both Norwegian and English by the same company on the same day. The backend skips Norwegian-language bodies when an English twin exists for the same issuer+day.

## The three dashboard views

Raw NOK value naturally favors large, liquid companies (insiders there trade in bigger blocks even for routine reasons). Rather than "fix" this with one metric, the dashboard offers three ranked views side by side, each with its own bias — the UI shows a one-line pros/cons blurb per view (see `VIEWS` in the frontend `<script>` blocks):

- **By Value** — total NOK traded per ticker. Real money at stake, but large-cap-biased.
- **By Liquidity** — value as % of the stock's average daily traded value (`fetch_avg_daily_value()` in `insider_lib.py`, from Yahoo Finance's unauthenticated `/v8/finance/chart/{ticker}.OL` endpoint, 1-month average). Flags trades unusual for that specific stock; can overweight thin small caps on modest NOK amounts.
- **By Insiders** — count of distinct named insiders trading, from `extract_insider_name()`. Cluster buying/selling by multiple independent people is a stronger signal than one large trade, but name extraction is best-effort (~75% match rate) and ignores trade size entirely.

**Liquidity data caveat:** Yahoo's `quoteSummary`/`marketCap` endpoint requires a cookie+crumb handshake that's unreliable for unattended scripts (confirmed by testing — it can fail with "Invalid Cookie" on a fresh call with no code change). The `/v8/finance/chart/` endpoint needs no auth and is used instead; it doesn't give market cap, only price/volume, hence normalizing against traded volume rather than market cap.

**Insider-name extraction caveat:** regex-based, matching two templates — direct ("Name, role ... bought/sold") and related-party ("Entity, a related party to role in Issuer Name, has ..."). Deliberately case-sensitive (a capital letter is what distinguishes a real name from boilerplate like a message header) with same-line-only name spans, to avoid the false-positive garbage matches earlier case-insensitive/newline-crossing versions produced. Trades with no matched insider still count toward value/trade-count; they're just excluded from the insider-count view's denominator rather than guessed at.

**Known false positive:** two adjacent messages can represent one internal transfer reported from both sides (e.g. board member A's related entity buys from board member B's related entity, same shares/price/day) — this shows up as one ticker topping both the buy and sell value charts. Not currently filtered; visible in the value view for PROT/Protector Forsikring in April 2026 as an example.

## Performance
Body fetches run in a **20-thread pool** (`ThreadPoolExecutor`). 90 days (~250 messages) takes ~7 seconds. `lru_cache` on `fetch_message_body` makes subsequent requests instant.

## Known limitations / future work
- Parser covers the most common announcement templates. Unusual formats (PDF-only, options exercises, corrections, LTI/incentive-plan allocation tables) are silently skipped.
- No persistent cache — every new request re-fetches from Oslo Børs. Add Redis or SQLite to cache by date range.
- The `overflow` flag from the list endpoint is not yet handled (could truncate results for very wide date ranges).
- `templates/index.html` and `docs/index.html` are two separately-maintained frontends with duplicated JS/CSS (the Python parsing/aggregation side is shared via `insider_lib.py`, but the HTML/JS is not). Diverges easily — worth factoring into one template if it grows further.
- Same-day matched internal transfers (one insider's entity buying from another's, filed as two separate MAR notifications) aren't detected/flagged — see PROT/Protector example above.
- Could add: net buy/sell balance view, per-ticker detail page, alerts on large trades, filtering out matched-transfer pairs.
