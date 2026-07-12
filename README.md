# Innsider

Dashboard for Oslo Børs insider trading movements. Ranks tickers by insider buy-ups and sell-outs three different ways, so no single metric's blind spot dominates the picture.

Live: **https://remebjornatle.github.io/innsider/** — a static build refreshed daily by GitHub Actions. Or run it locally for the on-demand version with adjustable date ranges (below).

Data source: Oslo Børs NewsWeb, category 1102 — mandatory notifications of trade by primary insiders (MAR Article 19).

## Setup (local / live version)

```bash
pip install flask requests
python3 server.py
```

Open http://localhost:5050.

## Usage

- Use the **7d / 30d / 90d / 180d** buttons to change the time window (local version only — the live static site is fixed at 90 days).
- Switch between three ranking views for the two bar charts:
  - **By Value** — total NOK traded. Real money at stake, but favors large, liquid companies whose insiders naturally trade in bigger blocks.
  - **By Liquidity** — trade value as a % of the stock's typical daily trading volume. Corrects the large-cap bias above, but can overweight modest trades in thinly-traded small caps.
  - **By Insiders** — number of distinct people trading, not trade size. Cluster buying/selling by independent insiders is a stronger signal than one big trade, but name-matching is best-effort and undercounts.
- The table below shows individual parsed trades, including the identified insider where available. Click **↗ View** to open the original Oslo Børs announcement.
- Use **All / Buys / Sells** to filter the table.

## How it works

The app fetches insider trade announcements from the Oslo Børs API (`api3.oslo.oslobors.no`) and parses the free-text announcement bodies with regex to extract the action (buy/sell), insider name, number of shares, and price per share. Liquidity data comes from Yahoo Finance's unauthenticated chart endpoint (`{ticker}.OL`). Everything is aggregated by ticker into the three views above.

Because the Oslo Børs API blocks cross-origin requests, both the Flask server (local) and the GitHub Actions job (live site) act as proxies — the browser never talks to Oslo Børs directly.

## Files

```
insider_lib.py        Shared core — API access, trade parsing, insider-name
                       extraction, liquidity lookup, aggregation
server.py              Flask app — /api/trades?days=N, for local/on-demand use
fetch_data.py           Run daily by GitHub Actions → writes docs/data.json
templates/index.html    Frontend for the Flask app
docs/index.html         Frontend for the static GitHub Pages site
CLAUDE.md              Developer notes (architecture, edge cases, future work)
```
