# China Futures Trading Agent

This adds a TradingAgents-style workflow for Chinese futures:

1. Market analyst: trend, moving averages, RSI, ATR, support/resistance.
2. Term-structure analyst: contango/backwardation from active contracts.
3. Fundamental analyst: contract specs, margin, basis, inventory when available.
4. News analyst: optional GNews + SerpApi Google News event scan.
5. Bull researcher: strongest long evidence.
6. Bear researcher: strongest short/risk evidence.
7. Trader: `LONG`, `SHORT`, or `WAIT` with entry, stop, targets, and lots.
8. Risk manager: per-lot risk, account risk budget, margin estimate.
9. Portfolio manager: final action summary.

## Install

```bash
cd /Users/sdg223157/TradingAgent/Aktools
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## CLI

```bash
python china_futures_agent.py CU --account-size 2000000 --risk-pct 0.005
python china_futures_agent.py AU --horizon intraday
```

Optional OpenAI memo synthesis:

```bash
export OPENAI_API_KEY=...
python china_futures_agent.py RB --llm
```

Optional news/event enhancement:

```bash
export GNEWS_API_KEY=...
export SERPAPI_API_KEY=...
# SERAPI_API_KEY is also accepted as an alias for SERPAPI_API_KEY.
python china_futures_agent.py CU
```

## API

Run the Aktools app:

```bash
python app.py
```

Then call:

```text
GET http://localhost:8888/api/trading-agent?code=CU
GET http://localhost:8888/api/trading-agent?code=AU&account_size=2000000&risk_pct=0.005&horizon=intraday
GET http://localhost:8888/api/trading-agent?code=RB&llm=true
```

The default decision core is deterministic, so it can run without an LLM key.
`llm=true` adds a natural-language memo using OpenAI.
If `GNEWS_API_KEY` or `SERPAPI_API_KEY` is present, the agent automatically adds
a News Analyst report and event score.

This is research software, not financial advice. Futures involve leverage,
gap risk, limit moves, liquidity risk, and delivery/rollover constraints.
