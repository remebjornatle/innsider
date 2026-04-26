# Innsider — Oslo Børs Insider Trading Dashboard

## Running the app
```
python3 server.py
# → http://localhost:5050
```
Requires: `flask`, `requests` (both already installed).

## Architecture

Two files:
- **`server.py`** — Flask backend. Proxies the Oslo Børs API, parses message bodies, returns aggregated JSON.
- **`templates/index.html`** — Single-page frontend. Chart.js bar charts + trades table. Calls `/api/trades?days=N`.

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

## Performance
Body fetches run in a **20-thread pool** (`ThreadPoolExecutor`). 90 days (~250 messages) takes ~7 seconds. `lru_cache` on `fetch_message_body` makes subsequent requests instant.

## Known limitations / future work
- Parser covers the most common announcement templates. Unusual formats (PDF-only, options exercises, corrections) are silently skipped.
- No persistent cache — every new request re-fetches from Oslo Børs. Add Redis or SQLite to cache by date range.
- The `overflow` flag from the list endpoint is not yet handled (could truncate results for very wide date ranges).
- Could add: insider name extraction, net buy/sell balance view, per-ticker detail page, alerts on large trades.
