# Futures K-Line Platform — Feature Documentation

## Overview

A Chinese futures K-line charting and analysis platform with three deployments:
- **Aktools** (localhost:8888) — Standalone FastAPI + HTML app
- **watchinglist-app** (watchinglist.app) — Next.js app with futures module on Coolify
- **trendwise.biz** — Flask app with Chinese futures integration on Coolify

---

## 1. K-Line Charting

### Timeframes
| Button | Period | Data Source |
|--------|--------|------------|
| 15m | 15-minute bars | `futures_zh_minute_sina(period="15")` |
| 30m | 30-minute bars | `futures_zh_minute_sina(period="30")` |
| 1H | 60-minute bars | `futures_zh_minute_sina(period="60")` |
| D | Daily bars | `futures_main_sina()` |
| W | Weekly bars | Daily resampled to W-FRI |
| M | Monthly bars | Daily resampled to M |

### Chart Features
- **Candlestick chart** with volume histogram (TradingView lightweight-charts v4.1.3)
- **Chinese convention**: Red = up, Green = down
- **5 Moving Averages** (configurable period + color):
  - MA5 (blue), MA20 (orange), MA60 (green), MA120 (pink), MA250 (purple)
  - Toggle on/off by clicking legend
  - Gear icon opens config panel to change period/color
- **Crosshair** with OHLCV info bar (updates on hover)
- **Date range picker** (From/To)
- **End date defaults to today**

### Drawing Tools
| Tool | Shortcut | Description |
|------|----------|------------|
| Cursor | Esc | Default mode |
| Trend Line | T | Two-click line between points |
| H-Line | H | Horizontal line at price level |
| Ray | R | Line extending from point through second point |
| Rectangle | G | Two-click rectangle zone |
| Fibonacci | F | Fibonacci retracement levels (0, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%) |
| Measure | M | Price diff, %, bars, days between two points |
| Undo | Ctrl+Z | Remove last drawing |
| Clear | — | Remove all drawings |

### Variety Selection
- **Exchange dropdown**: CFFEX, CZCE, DCE, GFEX, INE, SHFE (90 varieties, 869 contracts)
- **Variety dropdown**: Filtered by exchange, shows code + name + price
- **Search box**: Type code or Chinese name, autocomplete dropdown with exchange tag

---

## 2. Real-Time Data

### Live Quotes Panel
- **Toggle**: "Live" button in info bar
- **Display**: Scrollable card strip showing all contract months
- **Each card shows**: Symbol, Price, Change%, Volume, Open Interest
- **Color**: Red = up, Green = down
- **Auto-refresh**: Every 10 seconds
- **Opens by default** after first chart loads
- **Switches** when you change variety

### Live Candle Updates
- **Always on** after chart loads
- **Polls** `/api/tick` every 5 seconds
- **Updates** last candlestick: high extends up, low extends down, close follows price
- **Volume** updates
- **Info bar** shows live price
- **Works** on all timeframes (15m/30m/1H/D/W/M)

### Data Source
- AKShare → Sina Finance API
- `futures_zh_realtime()` for live quotes
- Snapshot data (not WebSocket stream)
- During market hours: live prices
- After hours: last settlement

---

## 3. GPT-5.4 Analysis (8 Modes)

All modes gather AKShare data (contract specs, term structure, basis, history, MAs, RSI, inventory, recent bars) then call GPT-5.4 to generate a markdown report.

| Button | Color | Mode | Content |
|--------|-------|------|---------|
| **Analyze** | Amber | `analysis` | Full 9-section price structure report: contract specs, term structure, basis, spreads, supply/demand, price patterns, events, EXTREME/CLOCK/GEO/TRENDWISE/ATH, structural synthesis |
| **Strategy** | Blue | `strategy` | Multi-scenario trading plan: bullish/bearish/range probabilities, 3 directional strategies (trend long, pullback long, short), spread trades, risk management, daily checklist, priority ranking. All with entry/stop/target/R:R |
| **Table** | Purple | `table` | Pure Markdown tables: scenario probabilities, strategy overview, spread trades, key levels, checklist, priority ranking. Printable format |
| **Intraday** | Green | `intraday` | Day trading plan: session-by-session (9:00-10:15, 10:30-11:30, 13:30-15:00, 21:00-23:00), pivot points, opening breakout, range trading, end-of-day trend, intraday risk rules |
| **Swing** | Cyan | `swing` | 3-20 day wave trading: weekly/daily trend, wave position, scaling in/out rules, moving stops, 2-4 week calendar, cross-period spread integration |
| **Orders** | Pink | `orders` | Ready-to-execute order sheet: 6+ limit/stop orders with exact prices, stop/target per order, R:R, cancellation conditions, position summary for each fill scenario |
| **Risk** | Red | `risk` | Risk management handbook: price/liquidity/structure/correlation risks, position sizing (by account size: 100万/500万/1000万), ATR-based sizing formula, technical/time/capital stop system, extreme scenarios (limit-locked, liquidity dry-up, policy shock), risk indicator dashboard, pre/mid/post-market check routines |
| **Checklist** | Orange | `checklist` | Printable daily trading plan: key levels table (4 resistance + 4 support + pivot), direction judgment with 3 reasons, trade plan table (3-5 strategies), indicator monitor, event alerts, pre-market checklist, discipline reminders, post-market review template, weekly calendar |

### Where Available
- **localhost:8888**: 8 buttons in topbar, renders in overlay panel (marked.js)
- **watchinglist.app**: 8 buttons on analysis page, renders as React Markdown, stores in PostgreSQL
- **trendwise.biz**: Analysis via Aktools API (bundled in Docker)

### API Endpoint
```
GET /api/analyze?code=CU&mode=analysis
GET /api/analyze?code=CU&mode=strategy
GET /api/analyze?code=CU&mode=table
GET /api/analyze?code=CU&mode=intraday
GET /api/analyze?code=CU&mode=swing
GET /api/analyze?code=CU&mode=orders
GET /api/analyze?code=CU&mode=risk
GET /api/analyze?code=CU&mode=checklist
```

---

## 4. Futures Analysis Skill (Claude Code)

**Skill file**: `~/.claude/skills/futures-price-structure-analysis/SKILL.md` (2057 lines)

### 13-Step Analysis Framework

| Step | Content |
|------|---------|
| 1 | Contract specifications (multiplier, tick, margin, fees, delivery dates) |
| 2 | Fetch all active contracts |
| 3 | Build term structure curve (contango/backwardation/mixed) |
| 4 | Basis analysis (spot - futures, traditional definition) |
| 5 | Inter-month spread analysis (adjacent, main-sub, near-far, annualized) |
| 6 | Supply/demand analysis (6a: inventory, 6b: supply side, 6c: demand side, 6d: cost support, 6e: policy/events, 6f: balance sheet) |
| 7 | Historical structure comparison |
| 8 | Price pattern analysis (8a: trend/MA, 8b: swing structure, 8c: support/resistance, 8d: chart patterns, 8e: momentum/volatility, 8f: volume-price, 8g: seasonality) |
| 9 | Quantitative state analysis (9a: HMM 3-state, 9b: Shannon entropy, 9c: Transfer entropy, 9d: combined) |
| 10 | Multi-dimensional framework (10a: EXTREME 0-20, 10b: CLOCK Phase 1-4, 10c: GEO Order 0-3, 10d: EXTREME×CLOCK×GEO matrix, 10e: TRENDWISE Open/Closed, 10f: ATH distance) |
| 11 | Event-driven analysis (11a: recent events, 11b: classification framework, 11c: impact quantification, 11d: event calendar, 11e: sensitivity rating, 11f: structure linkage) |
| 12 | Structural synthesis (multi-dimensional assessment table, implications for hedgers/speculators/arbitrageurs) |
| 13 | Save report to Obsidian (`obsidian-research/trading-ops/`) |

### Invoke
```
/futures-price-structure-analysis CU
```

---

## 5. Watchlist App — Futures Module

### Pages
| URL | Content |
|-----|---------|
| `/futures` | Watchlist dashboard: stats, search/add varieties, table with Chart/Analyze/Report/Remove per row |
| `/futures/chart` | Full-screen K-line chart (no pre-selected variety) |
| `/futures/{CODE}` | K-line chart with variety pre-selected |
| `/futures/{CODE}/analysis` | Analysis report page with 8 mode buttons |

### Navigation
- Main dashboard has amber **Futures** button
- Futures pages have full nav bar (Dashboard, Heatmap, PCA, Matrix, etc.)
- Chart pages have slim breadcrumb strip

### Database
- Table: `futures_watchlist` (PostgreSQL/Neon)
- Fields: variety_code, variety_name, exchange, multiplier, latest_price, analysis_report, analysis_date
- Case-insensitive queries (`UPPER(variety_code)`)
- UPSERT for analysis storage

### API Routes
| Route | Methods | Description |
|-------|---------|-------------|
| `/api/futures/varieties` | GET | All varieties by exchange |
| `/api/futures/kline` | GET | OHLCV data (15m/30m/1H/D/W/M) |
| `/api/futures/watchlist` | GET/POST/DELETE | User's watchlist CRUD |
| `/api/futures/analysis` | GET/POST | Store/retrieve/trigger GPT analysis |
| `/api/futures/realtime` | GET | Live quotes for all contracts |
| `/api/futures/tick` | GET | Latest price for live candle |

---

## 6. TrendWise Integration

### Chinese Futures Symbols (60+)
- SHFE: SHCU, SHAL, SHZN, SHPB, SHNI, SHSN, SHAU, SHAG, SHRB, SHHC, SHSS, SHBU, SHFU, SHRU, SHSP, SHAO
- INE: INSC, INLU, INNR, INBC, INEC
- DCE: DCI, DCJ, DCJM, DCM, DCY, DCP, DCC, DCJD, DCLH, DCL, DCPP, DCV, DCEG, DCEB, DCPG
- CZCE: ZCTA, ZCMA, ZCCF, ZCSR, ZCOI, ZCRM, ZCFG, ZCSA, ZCUR, ZCAP, ZCSF, ZCSM, ZCPX
- CFFEX: CFIF, CFIC, CFIH, CFIM, CFT, CFTF, CFTL
- GFEX: GFLC, GFSI

### Auto-Suggest
Search by symbol code, Chinese name, or English name. Returns instantly without yfinance verification.

### Data Flow
`SHCU` → `normalize_ticker()` → `CNFUT:CU0` → `data_service._get_data_from_aktools()` → Aktools API → yfinance-compatible DataFrame

### Cache Pre-warm
All 58 Chinese futures in `_SEED_TICKERS` — pre-warmed every 20 minutes alongside US/CN/HK stocks.

---

## 7. Architecture

### Docker Deployment (Coolify)
Both watchinglist-app and trendwise.biz bundle `futures-api.py` in their Docker containers:
- `start.sh` / startup script launches `python3 futures-api.py` on port 8888 (background)
- Main app runs on port 3000 (foreground)
- `localhost:8888` works inside the container — no external dependency

### Data Sources
| Source | Function | Data |
|--------|----------|------|
| AKShare `futures_fees_info()` | Contract specs | Multiplier, tick, margin, fees |
| AKShare `futures_zh_realtime()` | Live quotes | All contracts OHLCV + OI |
| AKShare `futures_main_sina()` | Daily history | Main contract OHLCV |
| AKShare `futures_zh_minute_sina()` | Intraday | 15m/30m/60m bars |
| AKShare `futures_spot_price()` | Basis | Spot vs futures prices |
| AKShare `futures_inventory_em()` | Inventory | Exchange warehouse stocks |
| AKShare `futures_symbol_mark()` | Symbol map | Code ↔ Chinese name |
| AKShare `futures_contract_info_*()` | Delivery dates | Listing/expiry/delivery |
| OpenAI GPT-5.4 | Analysis | 8 report modes |

### Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Required for analysis |
| `AKTOOLS_URL` | `http://localhost:8888` | Futures API base URL |
| `DATABASE_URL` | — | PostgreSQL connection (watchlist-app) |

---

## 8. Report Storage

| App | Storage | Persistence |
|-----|---------|-------------|
| watchinglist.app | PostgreSQL `futures_watchlist` table | Permanent |
| trendwise.biz | Redis cache | 1 hour TTL |
| Obsidian | Markdown files in `obsidian-research/trading-ops/` | Permanent |
| localhost:8888 | Browser overlay only | Session only |

---

## 9. Variety Code Reference

| Exchange | Count | Varieties |
|----------|-------|-----------|
| SHFE | 20 | CU AL ZN PB NI SN AU AG RB WR HC SS FU BU RU SP AO BR AD OP |
| INE | 5 | SC LU NR BC EC |
| DCE | 26 | A B M Y P FB BB JD L V PP J JM I EG RR EB PG LH CS C LG BZ |
| CZCE | 26 | WH PM CF SR TA OI RI MA FG RS RM ZC JR LR SF SM CY AP UR CJ SA PK PF PX SH PR PL |
| CFFEX | 8 | IF IC IH IM T TF TL TS |
| GFEX | 5 | LC SI PS PD PT |

**Total**: 90 varieties, 869+ contracts across 6 exchanges.
