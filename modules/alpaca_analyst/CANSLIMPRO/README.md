# CANSLIM Screener Pro

A standalone PySide6 desktop application for screening US and Indian stocks
using William O'Neil's full 7-component CANSLIM methodology.

## Features

- **Dual market support** — US stocks (NYSE/NASDAQ) and Indian stocks (NSE: `.NS`, BSE: `.BO`)
- **Dual data source** — yFinance (free, primary) + FMP API (optional enrichment for US institutional data)
- **Full CANSLIM scoring** — all 7 components with O'Neil's original weights
- **CSV watchlist** — drag-and-drop or browse; supports mixed US + Indian watchlists
- **Live screening** — background thread with per-ticker progress log
- **Results table** — sortable, colour-coded, with expandable detail panel
- **CSV export** — full results with all 7 component scores and metrics
- **PDF report** — cover page + ranked summary table + per-stock appendix

## Installation

```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

**Python 3.9+ required.**

## Quick Start

1. **Watchlist tab** — drag `sample_watchlist_us.csv` or `sample_watchlist_india.csv`
   onto the drop zone, or type tickers manually (e.g. `NVDA`, `RELIANCE.NS`)
2. **Screener tab** — optionally enter your FMP API key, set minimum score threshold,
   then click **Run CANSLIM Screen**
3. **Results tab** — auto-opens when screening completes; click any row for full breakdown
4. **PDF Report tab** — select stocks, click **Generate PDF Report**

## Watchlist CSV Format

Minimal (one column):
```
NVDA
AAPL
RELIANCE.NS
```

With headers (auto-detected):
```
Ticker,Name,Notes
NVDA,NVIDIA Corp,AI GPU leader
RELIANCE.NS,Reliance Industries,Conglomerate
```

Accepted header names: `Ticker`, `Symbol`, `Scrip`

## Market Detection

| Ticker format | Market   | Benchmark |
|---------------|----------|-----------|
| `NVDA`, `AAPL` | US       | S&P 500   |
| `RELIANCE.NS`  | Indian   | Nifty 50  |
| `RELIANCE.BO`  | Indian   | Nifty 50  |

## CANSLIM Component Weights

| Key | Component              | Weight |
|-----|------------------------|--------|
| C   | Current Earnings       | 15%    |
| A   | Annual Growth          | 20%    |
| N   | Newness / New Highs    | 15%    |
| S   | Supply / Demand        | 15%    |
| L   | Leadership / RS Rank   | 20%    |
| I   | Institutional Sponsor  | 10%    |
| M   | Market Direction       | 5%     |

## Rating Bands

| Score | Rating         |
|-------|----------------|
| 90+   | Exceptional+   |
| 80–89 | Exceptional    |
| 70–79 | Strong         |
| 60–69 | Above Average  |
| 50–59 | Average        |
| <50   | Below Average  |

## FMP API Key (Optional)

For improved US institutional ownership data, sign up free at
https://site.financialmodelingprep.com/developer/docs (250 calls/day free tier).

Enter the key in the Screener tab → Data Sources. It is saved locally to
`~/.canslim_pro/settings.json`.

## Data Sources

- **yFinance** — price history, financials, institutional holdings
- **FMP API** — institutional holder count + ownership % (US stocks only, optional)

## Disclaimer

This tool is for educational and informational purposes only.
Not investment advice. Always verify data with live sources before trading.
Past performance does not guarantee future results.
CANSLIM methodology from William O'Neil's *How to Make Money in Stocks*.
