# Futures K-Line Project

> A web-based Chinese futures charting platform with TradingView-style drawing tools, built on AKShare data.

## Quick Start

```bash
cd ~/Aktools
python3 app.py
# Open http://localhost:8888
```

### Requirements

```
Python 3.10+
akshare >= 1.18
fastapi
uvicorn
mplfinance  (optional, for static chart generation)
```

Install: `pip3 install akshare fastapi uvicorn mplfinance`

---

## Project Structure

```
Aktools/
├── app.py                    # FastAPI backend (95 lines)
├── static/
│   └── index.html            # Frontend SPA (1168 lines)
├── futures_data.py           # CLI data fetcher script
├── futures_contracts.md      # Full reference: 90 varieties, 869 contracts
├── kline_gold.py             # Static K-line chart generator (matplotlib)
├── kline_au0_full.png        # Gold full-period chart image
├── kline_au0_recent.png      # Gold recent-120-days chart image
└── PROJECT.md                # This file
```

---

## Architecture

```
┌──────────────────────────────────────────┐
│  Browser (localhost:8888)                │
│  ┌─────────────────────────────────────┐ │
│  │  TradingView Lightweight Charts v4  │ │
│  │  + Canvas overlay (drawing tools)   │ │
│  └─────────────────────────────────────┘ │
│           ▲ fetch /api/kline             │
│           ▲ fetch /api/varieties         │
└───────────┼──────────────────────────────┘
            │
┌───────────┼──────────────────────────────┐
│  FastAPI   │  (app.py)                   │
│           ▼                              │
│  AKShare ─── Sina Finance API            │
│           ─── 99qihuo.com               │
└──────────────────────────────────────────┘
```

---

## Backend API

### `GET /api/varieties`

Returns all 90 futures varieties grouped by 6 exchanges.

```json
{
  "SHFE": [{"code": "au", "name": "黄金", "multiplier": 1000, "price": 1016.12}, ...],
  "DCE": [...],
  "CZCE": [...],
  "CFFEX": [...],
  "INE": [...],
  "GFEX": [...]
}
```

### `GET /api/kline`

Returns OHLCV candlestick data for a main contract.

| Param | Default | Description |
|-------|---------|-------------|
| `symbol` | required | Main contract code, e.g. `AU0`, `RB0`, `I0` |
| `start_date` | `20240101` | Start date (YYYYMMDD) |
| `end_date` | `20261231` | End date (YYYYMMDD) |
| `period` | `daily` | `daily`, `weekly`, or `monthly` |

```json
[
  {"time": "2024-01-02", "open": 481.24, "high": 483.80, "low": 480.82, "close": 483.32, "volume": 37141},
  ...
]
```

**Weekly**: resampled to Friday-ending weeks (W-FRI). OHLCV aggregated: first open, max high, min low, last close, sum volume.

**Monthly**: resampled to month-end. Same aggregation logic.

---

## Frontend Features

### Top Bar

| Component | Description |
|-----------|-------------|
| **Search box** | Type code (`IC`) or Chinese name (`黄金`) to get autocomplete suggestions. Matches highlighted in gold. Arrow keys + Enter to select. |
| **Exchange dropdown** | CFFEX, SHFE, INE, DCE, CZCE, GFEX (with variety count) |
| **Variety dropdown** | All varieties for selected exchange, with latest price |
| **Date pickers** | From / To date range (dark-mode native calendar) |
| **Period toggle** | `D` (daily) / `W` (weekly) / `M` (monthly) buttons |

Changing any selector auto-loads the chart (no Load button needed).

### Chart

- **TradingView Lightweight Charts v4** — professional candlestick rendering
- **Red up / Green down** — Chinese market convention
- **Volume bars** — color-coded, bottom 20% of chart
- **4 Moving Averages** — MA5 (blue), MA10 (orange), MA20 (green), MA60 (pink)
  - Click legend label to toggle on/off
  - Click gear icon to configure: period (1-500), color picker, enable/disable
- **Crosshair** — hover to see OHLC, % change, volume in info bar
- **Auto-resize** on window resize

### Drawing Tools (Left Toolbar)

| Tool | Shortcut | Usage |
|------|----------|-------|
| **Cursor** | `Esc` | Default pan/zoom mode |
| **Trend Line** | `T` | Click start, click end — line between two points |
| **Horizontal Line** | `H` | Single click — infinite horizontal at price level, with price label |
| **Ray** | `R` | Click origin, click direction — extends to infinity |
| **Rectangle** | `G` | Click corner, click opposite corner — shaded fill with border |
| **Fibonacci** | `F` | Click high, click low — draws 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100% levels with color-coded labels |
| **Measure/Ruler** | `M` | Click start, click end — shows: price change (+/-), % change, bar count, calendar days, price range. Dashed line with right-angle guides and info box. |
| **Undo** | `Ctrl+Z` | Remove last drawing |
| **Clear All** | — | Remove all drawings |

Drawing behavior:
- Drawings stored in data coordinates (price + time) — persist on scroll/zoom
- Dashed preview line while placing second point
- Right-click to cancel mid-draw
- Tool auto-resets to Cursor after each drawing
- Drawings clear when switching symbol or period

---

## Data Coverage

Data sourced from Sina Finance via AKShare. History goes back to each contract's listing date.

| Exchange | Name | Varieties | Example |
|----------|------|-----------|---------|
| CFFEX | 中国金融期货交易所 | 8 | IF (沪深300), T (10Y国债) |
| SHFE | 上海期货交易所 | 20 | AU (黄金), CU (铜), RB (螺纹钢) |
| INE | 上海国际能源交易中心 | 5 | SC (原油), BC (国际铜) |
| DCE | 大连商品交易所 | 26 | I (铁矿石), J (焦炭), M (豆粕) |
| CZCE | 郑州商品交易所 | 26 | CF (棉花), SA (纯碱), MA (甲醇) |
| GFEX | 广州期货交易所 | 5 | LC (碳酸锂), SI (工业硅) |

**Max history examples:**

| Symbol | Name | From | Bars |
|--------|------|------|------|
| CU0 | 铜 | 2005-01-04 | 5,186 |
| AU0 | 黄金 | 2008-01-09 | 4,459 |
| RB0 | 螺纹钢 | 2009-03-27 | 4,150 |
| AG0 | 白银 | 2012-05-10 | 3,400 |
| I0 | 铁矿石 | 2013-10-18 | 3,047 |

### Symbol Conventions

| Type | Format | Example |
|------|--------|---------|
| Main contract (for API) | `CODE` + `0` | `AU0`, `RB0`, `I0` |
| Specific contract | `code` + `YYMM` | `au2608`, `rb2610` |
| CZCE contracts | `CODE` + `YMM` | `CF605`, `SA609` |

---

## CLI Scripts

### futures_data.py

Standalone script to fetch futures data without the web server.

```bash
python3 futures_data.py
```

Runs 6 demo functions:
1. Main contract daily OHLCV (`futures_main_sina`)
2. Specific contract daily (`futures_zh_daily_sina`)
3. 5-minute intraday bars (`futures_zh_minute_sina`)
4. Spot prices & basis for all commodities (`futures_spot_price`)
5. SHFE exchange daily data (`get_shfe_daily`)
6. Trading fees & margin info (`futures_fees_info`)

### kline_gold.py

Generates static K-line PNG charts for gold (AU0) using mplfinance.

```bash
python3 kline_gold.py
# Outputs: kline_au0_full.png, kline_au0_recent.png
```

- Dark theme, Chinese convention (red=up, green=down)
- MA5/MA20/MA60 overlays
- Volume subplot
- CJK font support (Arial Unicode MS)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, uvicorn |
| Data | AKShare (Sina Finance, 99qihuo) |
| Frontend | Vanilla HTML/JS (single file, no build step) |
| Charting | TradingView Lightweight Charts v4.1.3 (CDN) |
| Drawing | HTML5 Canvas overlay |
| Static charts | mplfinance + matplotlib |

No Node.js, no npm, no build pipeline. One `python3 app.py` and it runs.
