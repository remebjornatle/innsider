# Innsider

Dashboard for Oslo Børs insider trading movements. Shows which stocks have the highest insider buy-ups and sell-outs, ranked by total NOK value.

Data source: Oslo Børs NewsWeb, category 1102 — mandatory notifications of trade by primary insiders (MAR Article 19).

## Setup

```bash
pip install flask requests
python3 server.py
```

Open http://localhost:5050.

## Usage

- Use the **7d / 30d / 90d / 180d** buttons to change the time window.
- The two bar charts show top tickers by total insider **buy value** and **sell value**.
- The table below shows individual parsed trades. Click **↗ View** to open the original Oslo Børs announcement.
- Use **All / Buys / Sells** to filter the table.

## How it works

The app fetches insider trade announcements from the Oslo Børs API (`api3.oslo.oslobors.no`) and parses the free-text announcement bodies with regex to extract the action (buy/sell), number of shares, and price per share. It then aggregates by ticker and renders the dashboard.

Because the Oslo Børs API blocks cross-origin requests, the Flask server acts as a local proxy. All data stays on your machine.

## Files

```
server.py            Flask backend — API proxy + trade parser + aggregation
templates/
  index.html         Frontend — Chart.js charts + trades table
CLAUDE.md            Developer notes (architecture, edge cases, future work)
```
