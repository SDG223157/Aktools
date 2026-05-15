"""
Futures K-Line Website + Analysis API
Run: python3 app.py
Open: http://localhost:8888
"""

import os
import json
import sys
from pathlib import Path
import akshare as ak
import pandas as pd
import numpy as np
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from china_futures_agent import (
    ChinaFuturesTradingAgent,
    DEFAULT_ACCOUNT_SIZE_RMB,
    OfficialFuturesTradingAgentsAdapter,
)

app = FastAPI(title="Futures K-Line")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

OPENAI_MODEL = "gpt-5.4"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
STRATEGY_LAB_ROOT = Path(__file__).resolve().parents[1] / "cnfutures-strategy-lab"
DEFAULT_LOCAL_DB = STRATEGY_LAB_ROOT.parent / "cnfutures" / "data" / "local_futures_ohlcv.sqlite"

if STRATEGY_LAB_ROOT.exists() and str(STRATEGY_LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(STRATEGY_LAB_ROOT))

try:
    from local_futures_data import fetch_local_ohlcv
except Exception as exc:
    fetch_local_ohlcv = None
    print(f"⚠️ Fade signal imports unavailable: {exc}")


FADE_DEFAULTS = {
    "AU0": {"lookback": 6, "label": "AU fade N6"},
    "IF0": {"lookback": 5, "label": "IF fade N5"},
}

FADE_CONTRACT_SPECS = {
    "AU": {"multiplier": 1000.0, "tick_size": 0.02},
    "IF": {"multiplier": 300.0, "tick_size": 0.2},
}


@app.get("/api/varieties")
def get_varieties():
    """Return all futures varieties grouped by exchange."""
    df = ak.futures_fees_info()
    varieties = df.groupby("品种名称").agg({
        "品种代码": "first",
        "交易所": "first",
        "合约乘数": "first",
        "最新价": "first",
    }).reset_index()
    varieties = varieties.sort_values(["交易所", "品种代码"])

    result = {}
    for _, row in varieties.iterrows():
        ex = row["交易所"]
        if ex not in result:
            result[ex] = []
        result[ex].append({
            "code": row["品种代码"],
            "name": row["品种名称"],
            "multiplier": row["合约乘数"],
            "price": float(row["最新价"]) if pd.notna(row["最新价"]) else None,
        })
    return result


@app.get("/api/kline")
def get_kline(
    symbol: str = Query(..., description="e.g. AU0, RB0, I0"),
    start_date: str = Query("20240101"),
    end_date: str = Query("20261231"),
    period: str = Query("daily", description="daily, weekly, monthly, 30, 60"),
):
    """Return OHLCV data for a futures main contract."""
    if period in ("15", "30", "60"):
        df = ak.futures_zh_minute_sina(symbol=symbol, period=period)
        records = []
        for _, row in df.iterrows():
            dt = pd.to_datetime(row["datetime"])
            records.append({
                "time": int(dt.timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            })
        return records

    df = ak.futures_main_sina(symbol=symbol, start_date=start_date, end_date=end_date)
    df["日期"] = pd.to_datetime(df["日期"])

    if period == "weekly":
        df = df.set_index("日期")
        df = df.resample("W-FRI").agg({
            "开盘价": "first",
            "最高价": "max",
            "最低价": "min",
            "收盘价": "last",
            "成交量": "sum",
        }).dropna()
        df = df.reset_index()
    elif period == "monthly":
        df = df.set_index("日期")
        df = df.resample("M").agg({
            "开盘价": "first",
            "最高价": "max",
            "最低价": "min",
            "收盘价": "last",
            "成交量": "sum",
        }).dropna()
        df = df.reset_index()

    records = []
    for _, row in df.iterrows():
        records.append({
            "time": str(row["日期"].date()) if hasattr(row["日期"], "date") else str(row["日期"]),
            "open": float(row["开盘价"]),
            "high": float(row["最高价"]),
            "low": float(row["最低价"]),
            "close": float(row["收盘价"]),
            "volume": int(row["成交量"]),
        })
    return records


def _fade_spec(symbol: str) -> dict:
    base = symbol[:-1] if symbol.endswith("0") else symbol
    return FADE_CONTRACT_SPECS.get(base, {"multiplier": 1.0, "tick_size": 1.0})


def _recent_atr(data: pd.DataFrame, index: int, period: int = 20) -> float:
    if index <= 0:
        return 0.0
    start = max(1, index - period + 1)
    ranges = []
    for i in range(start, index + 1):
        high = float(data["high"].iloc[i])
        low = float(data["low"].iloc[i])
        prev_close = float(data["close"].iloc[i - 1])
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return float(sum(ranges) / len(ranges)) if ranges else 0.0


def _range_filter_ok(data: pd.DataFrame, index: int, lookback: int, direction: int, close: float, stop: float, tick: float) -> bool:
    atr = _recent_atr(data, index, 20)
    if atr <= 0:
        atr = tick
    start = max(0, index - lookback + 1)
    channel_width = float(data["high"].iloc[start : index + 1].max() - data["low"].iloc[start : index + 1].min())
    risk = abs(close - stop)
    return channel_width >= 0.30 * atr and risk >= max(3 * tick, 0.12 * atr)


def _position_qty(equity: float, price: float, multiplier: float, leverage: float = 3.0) -> int:
    notional = price * multiplier
    return max(0, int((equity * leverage) // notional)) if notional > 0 else 0


def _close_trade(position: dict, ts: pd.Timestamp, exit_ref: float, reason: str, spec: dict) -> tuple[dict, float]:
    direction = position["direction"]
    exit_price = float(exit_ref)
    gross_pnl = (exit_price - position["entry_price"]) * direction * position["qty"] * spec["multiplier"]
    trade = {
        "symbol": position["symbol"],
        "direction": "long" if direction > 0 else "short",
        "entry_time": position["entry_time"],
        "exit_time": ts,
        "entry_price": position["entry_price"],
        "exit_price": exit_price,
        "initial_stop": position["stop_price"],
        "target_price": None,
        "qty": position["qty"],
        "gross_pnl": gross_pnl,
        "costs": 0.0,
        "net_pnl": gross_pnl,
        "return_pct": gross_pnl / 2_000_000.0,
        "hold_hours": (ts - position["entry_time"]).total_seconds() / 3600.0,
        "exit_reason": reason,
    }
    return trade, gross_pnl


def _run_fade_signals(symbol: str, bars: pd.DataFrame, lookback: int) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict | None]:
    spec = _fade_spec(symbol)
    data = bars.copy().sort_values("datetime").reset_index(drop=True)
    for column in ["high", "low", "close"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["datetime", "high", "low", "close"]).reset_index(drop=True)
    rolling_high = data["close"].rolling(lookback).max()
    rolling_low = data["close"].rolling(lookback).min()
    rolling_stop_low = data["low"].rolling(lookback).min()
    rolling_stop_high = data["high"].rolling(lookback).max()
    cash = 2_000_000.0
    position = None
    trades = []
    equity_rows = []

    for index, row in data.iterrows():
        ts = pd.Timestamp(row["datetime"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        if position is not None and ts > position["entry_time"]:
            if position["direction"] > 0 and low <= position["stop_price"]:
                trade, pnl = _close_trade(position, ts, position["stop_price"], "initial_stop", spec)
                trades.append(trade)
                cash += pnl
                position = None
            elif position["direction"] < 0 and high >= position["stop_price"]:
                trade, pnl = _close_trade(position, ts, position["stop_price"], "initial_stop", spec)
                trades.append(trade)
                cash += pnl
                position = None

        high_signal = index >= lookback - 1 and close >= float(rolling_high.iloc[index])
        low_signal = index >= lookback - 1 and close <= float(rolling_low.iloc[index])
        if high_signal and low_signal:
            high_signal = low_signal = False
        long_signal = low_signal
        short_signal = high_signal

        if long_signal:
            if position is not None and position["direction"] < 0:
                trade, pnl = _close_trade(position, ts, close, "reverse_to_long", spec)
                trades.append(trade)
                cash += pnl
                position = None
            elif position is None:
                stop = float(rolling_stop_low.iloc[index])
                if _range_filter_ok(data, index, lookback, 1, close, stop, spec["tick_size"]):
                    qty = _position_qty(cash, close, spec["multiplier"], 3.0)
                    if qty > 0:
                        position = {
                            "symbol": symbol,
                            "direction": 1,
                            "entry_time": ts,
                            "entry_price": close,
                            "stop_price": stop,
                            "qty": qty,
                        }
        elif short_signal:
            if position is not None and position["direction"] > 0:
                trade, pnl = _close_trade(position, ts, close, "reverse_to_short", spec)
                trades.append(trade)
                cash += pnl
                position = None
            elif position is None:
                stop = float(rolling_stop_high.iloc[index])
                if _range_filter_ok(data, index, lookback, -1, close, stop, spec["tick_size"]):
                    qty = _position_qty(cash, close, spec["multiplier"], 3.0)
                    if qty > 0:
                        position = {
                            "symbol": symbol,
                            "direction": -1,
                            "entry_time": ts,
                            "entry_price": close,
                            "stop_price": stop,
                            "qty": qty,
                        }

        unrealized = 0.0
        if position is not None:
            unrealized = (close - position["entry_price"]) * position["direction"] * position["qty"] * spec["multiplier"]
        equity = cash + unrealized
        equity_rows.append({"datetime": ts, "equity": equity})

    trades_frame = pd.DataFrame(trades)
    equity_frame = pd.DataFrame(equity_rows)
    if not equity_frame.empty:
        equity_frame["peak"] = equity_frame["equity"].cummax()
        equity_frame["drawdown_pct"] = equity_frame["equity"] / equity_frame["peak"] - 1.0
    wins = trades_frame[trades_frame["net_pnl"] > 0] if not trades_frame.empty else pd.DataFrame()
    losses = trades_frame[trades_frame["net_pnl"] < 0] if not trades_frame.empty else pd.DataFrame()
    gross_profit = float(wins["net_pnl"].sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses["net_pnl"].sum())) if not losses.empty else 0.0
    final_equity = float(equity_frame["equity"].iloc[-1]) if not equity_frame.empty else 2_000_000.0
    summary = {
        "return_pct": final_equity / 2_000_000.0 - 1.0,
        "max_drawdown_pct": float(equity_frame["drawdown_pct"].min()) if not equity_frame.empty else 0.0,
        "trades": len(trades_frame),
        "win_rate": len(wins) / len(trades_frame) if len(trades_frame) else pd.NA,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else pd.NA,
    }
    return trades_frame, equity_frame, summary, position


def _marker_time(value):
    return int(pd.Timestamp(value).timestamp())


def _json_safe(value):
    if value is None:
        return None
    if value is pd.NA:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def _trade_record(row: pd.Series | None) -> dict | None:
    if row is None:
        return None
    return {key: _json_safe(value) for key, value in row.to_dict().items()}


def _position_record(position: dict | None, last_close: float | None = None) -> dict | None:
    if position is None:
        return None
    row = {
        "symbol": position["symbol"],
        "direction": "long" if position["direction"] > 0 else "short",
        "entry_time": position["entry_time"],
        "entry_price": position["entry_price"],
        "initial_stop": position["stop_price"],
        "qty": position["qty"],
    }
    if last_close is not None:
        row["unrealized_pnl"] = (float(last_close) - position["entry_price"]) * position["direction"] * position["qty"] * _fade_spec(position["symbol"])["multiplier"]
    return {key: _json_safe(value) for key, value in row.items()}


def _trade_markers(trades: pd.DataFrame) -> list[dict]:
    markers = []
    if trades.empty:
        return markers
    for _, trade in trades.iterrows():
        is_long = trade["direction"] == "long"
        entry_time = _marker_time(trade["entry_time"])
        exit_time = _marker_time(trade["exit_time"])
        reason = str(trade.get("exit_reason", "exit"))
        entry_text = "开多" if is_long else "开空"
        exit_text = "止损" if reason == "initial_stop" else "平仓"
        if reason.startswith("reverse_to"):
            exit_text = "反向平"
        markers.append({
            "time": entry_time,
            "position": "belowBar" if is_long else "aboveBar",
            "shape": "arrowUp" if is_long else "arrowDown",
            "color": "#ef5350" if is_long else "#26a69a",
            "text": f"{entry_text} {float(trade['entry_price']):.2f}",
            "kind": "entry",
        })
        markers.append({
            "time": exit_time,
            "position": "aboveBar" if is_long else "belowBar",
            "shape": "circle",
            "color": "#ffd700" if reason != "initial_stop" else "#ff7043",
            "text": f"{exit_text} {float(trade['exit_price']):.2f}",
            "kind": "exit",
        })
    markers.sort(key=lambda item: item["time"])
    return markers


def _latest_signal(symbol: str, lookback: int, bars: pd.DataFrame, trades: pd.DataFrame, summary: dict, position: dict | None) -> dict:
    latest_trade = _trade_record(trades.iloc[-1]) if not trades.empty else None
    last_close = float(bars["close"].iloc[-1]) if not bars.empty else None
    open_position = _position_record(position, last_close)
    last_bar_time = str(pd.Timestamp(bars["datetime"].iloc[-1])) if not bars.empty else ""
    return {
        "symbol": symbol,
        "mode": "fade",
        "lookback": lookback,
        "bars": int(len(bars)),
        "last_bar_time": last_bar_time,
        "return_pct": float(summary.get("return_pct", 0.0)),
        "max_drawdown_pct": float(summary.get("max_drawdown_pct", 0.0)),
        "trades": int(summary.get("trades", 0)),
        "win_rate": None if pd.isna(summary.get("win_rate")) else float(summary.get("win_rate")),
        "profit_factor": None if pd.isna(summary.get("profit_factor")) else float(summary.get("profit_factor")),
        "latest_trade": latest_trade,
        "open_position": open_position,
    }


@app.get("/api/fade-signals")
def get_fade_signals(
    symbol: str = Query("AU0"),
    start_date: str = Query("20240101"),
    end_date: str = Query("20261231"),
    lookback: int | None = Query(None),
    period: str = Query("60"),
):
    """Return AU/IF fade strategy markers using the local strategy-lab backtest logic."""
    if fetch_local_ohlcv is None:
        return {"error": "Fade strategy modules are unavailable. Check cnfutures-strategy-lab imports."}
    symbol = symbol.upper()
    if period != "60":
        return {"error": "Fade signal overlay currently supports 60-minute bars only."}
    resolved_lookback = int(lookback or FADE_DEFAULTS.get(symbol, {}).get("lookback", 6))
    try:
        os.environ.setdefault("LOCAL_FUTURES_DB", str(DEFAULT_LOCAL_DB))
        bars = fetch_local_ohlcv(symbol, "60", start_date, end_date)
        if bars.empty:
            return {"error": f"No local 60m bars for {symbol} {start_date}->{end_date}."}
        bars = bars.copy()
        bars["datetime"] = pd.to_datetime(bars["datetime"])
        bars = bars.sort_values("datetime").reset_index(drop=True)
        trades, equity, summary, position = _run_fade_signals(symbol, bars, resolved_lookback)
        return {
            "ok": True,
            "symbol": symbol,
            "label": FADE_DEFAULTS.get(symbol, {}).get("label", f"{symbol} fade N{resolved_lookback}"),
            "period": "60",
            "lookback": resolved_lookback,
            "markers": _trade_markers(trades),
            "summary": _latest_signal(symbol, resolved_lookback, bars, trades, summary, position),
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/fade-dashboard")
def get_fade_dashboard(
    start_date: str = Query("20240101"),
    end_date: str = Query("20261231"),
):
    """Return compact latest fade state for AU0 and IF0."""
    rows = []
    for symbol, defaults in FADE_DEFAULTS.items():
        result = get_fade_signals(symbol=symbol, start_date=start_date, end_date=end_date, lookback=defaults["lookback"], period="60")
        if isinstance(result, dict) and result.get("ok"):
            rows.append(result["summary"])
        else:
            rows.append({"symbol": symbol, "error": result.get("error", "unknown error") if isinstance(result, dict) else "unknown error"})
    return {"ok": True, "items": rows}


# ===================== REALTIME =====================

# Variety code → exact name from futures_symbol_mark() (the ONLY names futures_zh_realtime accepts)
_CODE_TO_CN = {}

def _init_code_map():
    """Build code→name map by probing futures_zh_realtime with each symbol_mark name."""
    global _CODE_TO_CN
    if _CODE_TO_CN:
        return
    import re
    try:
        df = ak.futures_symbol_mark()
        for _, r in df.iterrows():
            cn = r["symbol"]
            try:
                rt = ak.futures_zh_realtime(symbol=cn)
                if rt.empty:
                    continue
                sym = str(rt.iloc[0].get("symbol", ""))
                m = re.match(r'^([A-Za-z]+)', sym)
                if m:
                    _CODE_TO_CN[m.group(1).lower()] = cn
            except:
                continue
        print(f"✅ Realtime map built: {len(_CODE_TO_CN)} varieties")
    except Exception as e:
        print(f"⚠️ Realtime map init failed: {e}")
        # Minimal fallback
        _CODE_TO_CN.update({"cu": "沪铜", "au": "黄金", "ag": "白银", "rb": "螺纹钢", "i": "铁矿石"})

@app.get("/api/realtime")
def get_realtime(code: str = Query(..., description="Variety code or Chinese name, e.g. cu, 沪铜")):
    """Return real-time quotes for all contracts of a variety."""
    _init_code_map()

    # Resolve to Chinese name for futures_zh_realtime
    cn_name = code
    if not any('\u4e00' <= c <= '\u9fff' for c in code):
        # English code — look up from fees_info map
        cn_name = _CODE_TO_CN.get(code.lower())
        if not cn_name:
            # Try symbol_mark as fallback
            try:
                df_map = ak.futures_symbol_mark()
                for _, r in df_map.iterrows():
                    if code.lower() in r["mark"]:
                        cn_name = r["symbol"]
                        break
            except:
                pass
        if not cn_name:
            return {"error": f"Unknown variety code: {code}"}

    try:
        df = ak.futures_zh_realtime(symbol=cn_name)
        records = []
        for _, r in df.iterrows():
            records.append({
                "symbol": r.get("symbol", ""),
                "name": r.get("name", ""),
                "price": float(r["trade"]) if pd.notna(r.get("trade")) else None,
                "open": float(r["open"]) if pd.notna(r.get("open")) else None,
                "high": float(r["high"]) if pd.notna(r.get("high")) else None,
                "low": float(r["low"]) if pd.notna(r.get("low")) else None,
                "volume": int(r["volume"]) if pd.notna(r.get("volume")) else 0,
                "oi": int(r["position"]) if pd.notna(r.get("position")) else 0,
                "settlement": float(r["settlement"]) if pd.notna(r.get("settlement")) else None,
                "prev_settlement": float(r.get("presettlement") or r.get("prevsettlement", 0)) if pd.notna(r.get("presettlement", r.get("prevsettlement"))) else None,
            })
        # Add change % calculation
        for rec in records:
            if rec["price"] and rec.get("prev_settlement"):
                rec["change_pct"] = round((rec["price"] - rec["prev_settlement"]) / rec["prev_settlement"] * 100, 2)
            elif rec["price"] and rec.get("settlement"):
                rec["change_pct"] = round((rec["price"] - rec["settlement"]) / rec["settlement"] * 100, 2)
            else:
                rec["change_pct"] = 0
        return records
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/tick")
def get_tick(symbol: str = Query(..., description="e.g. AU0, CU0")):
    """Return latest tick (price, volume, time) for a main contract."""
    try:
        # Get the Chinese name from symbol_mark
        code = symbol.replace("0", "").upper()
        df_map = ak.futures_symbol_mark()
        cn_name = None
        for _, r in df_map.iterrows():
            if code.lower() in r["mark"]:
                cn_name = r["symbol"]; break
        if not cn_name:
            return {"error": f"Unknown symbol {symbol}"}

        df = ak.futures_zh_realtime(symbol=cn_name)
        # Find the continuous contract (ends with 0)
        row = df[df["symbol"] == symbol]
        if row.empty:
            row = df.head(1)  # fallback to first
        r = row.iloc[0]
        return {
            "symbol": r.get("symbol", symbol),
            "price": float(r["trade"]) if pd.notna(r.get("trade")) else None,
            "open": float(r["open"]) if pd.notna(r.get("open")) else None,
            "high": float(r["high"]) if pd.notna(r.get("high")) else None,
            "low": float(r["low"]) if pd.notna(r.get("low")) else None,
            "volume": int(r["volume"]) if pd.notna(r.get("volume")) else 0,
            "time": pd.Timestamp.now().isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


# ===================== ANALYZE =====================

def gather_futures_data(variety_code: str) -> dict:
    """Gather all data needed for the analysis prompt."""
    data = {}
    upper = variety_code.upper()

    try:
        df_fees = ak.futures_fees_info()
        rows = df_fees[df_fees['品种代码'].str.upper() == upper]
        if not rows.empty:
            r = rows.iloc[0]
            data["contract"] = {
                "exchange": r["交易所"], "name": r["品种名称"], "code": r["品种代码"],
                "multiplier": float(r["合约乘数"]), "tick": float(r["最小跳动"]),
                "margin_rate": float(r["做多保证金率"]),
            }
            data["contracts_list"] = [
                {"code": cr["合约代码"], "price": float(cr["最新价"]) if pd.notna(cr["最新价"]) else None,
                 "volume": int(cr["成交量"]) if pd.notna(cr["成交量"]) else 0,
                 "oi": int(cr["持仓量"]) if pd.notna(cr["持仓量"]) else 0}
                for _, cr in rows.iterrows()
            ]
    except Exception as e:
        data["contract_error"] = str(e)

    try:
        symbol_map = ak.futures_symbol_mark()
        cn_name = None
        for _, r in symbol_map.iterrows():
            if upper.lower() in r["mark"]:
                cn_name = r["symbol"]; break
        if cn_name:
            df_rt = ak.futures_zh_realtime(symbol=cn_name)
            contracts = df_rt[~df_rt['symbol'].str.endswith('0')]
            data["term_structure"] = sorted([
                {"symbol": r["symbol"], "price": float(r["trade"]) if pd.notna(r["trade"]) else None,
                 "volume": int(r["volume"]) if pd.notna(r["volume"]) else 0,
                 "oi": int(r["position"]) if pd.notna(r["position"]) else 0}
                for _, r in contracts.iterrows()
            ], key=lambda x: x["symbol"])
    except Exception as e:
        data["term_structure_error"] = str(e)

    try:
        from datetime import datetime, timedelta
        for d in range(5):
            dt = (datetime.now() - timedelta(days=d)).strftime("%Y%m%d")
            try:
                df_b = ak.futures_spot_price(date=dt)
                basis = df_b[df_b['symbol'].str.upper() == upper]
                if not basis.empty:
                    r = basis.iloc[0]
                    data["basis"] = {col: (float(r[col]) if isinstance(r[col], (int, float, np.floating)) else str(r[col])) for col in basis.columns}
                    break
            except:
                continue
    except Exception as e:
        data["basis_error"] = str(e)

    try:
        df_hist = ak.futures_main_sina(symbol=f"{upper}0", start_date="20250101", end_date="20261231")
        df_hist['close'] = pd.to_numeric(df_hist['收盘价'], errors='coerce')
        df_hist['high'] = pd.to_numeric(df_hist['最高价'], errors='coerce')
        close = df_hist['close'].dropna()
        latest = close.iloc[-1]
        data["history"] = {
            "latest_price": float(latest), "latest_date": str(df_hist['日期'].iloc[-1]),
            "min": float(close.min()), "max": float(close.max()), "mean": float(close.mean()),
            "percentile": float((close < latest).mean() * 100), "bars": len(close),
        }
        perf = {}
        for days, label in [(5, "1w"), (20, "1m"), (60, "3m"), (120, "6m")]:
            if len(close) > days:
                perf[label] = float((latest / close.iloc[-days-1] - 1) * 100)
        data["performance"] = perf
        mas = {}
        for w in [5, 20, 60, 120, 250]:
            ma = close.rolling(w).mean()
            if pd.notna(ma.iloc[-1]):
                mas[f"MA{w}"] = float(ma.iloc[-1])
        data["moving_averages"] = mas
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / loss))
        data["rsi14"] = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None
        data["ath"] = float(df_hist['high'].max())
        data["ath_distance"] = float((latest - data["ath"]) / data["ath"] * 100)
        data["recent_bars"] = [
            {"date": str(r["日期"]), "O": r["开盘价"], "H": r["最高价"], "L": r["最低价"], "C": r["收盘价"], "V": r["成交量"]}
            for _, r in df_hist.tail(10).iterrows()
        ]
    except Exception as e:
        data["history_error"] = str(e)

    try:
        inv_map = {"CU": "沪铜", "AL": "沪铝", "ZN": "沪锌", "AU": "沪金", "AG": "沪银",
                    "RB": "螺纹钢", "I": "铁矿石", "J": "焦炭", "M": "豆粕", "NI": "镍"}
        inv_name = inv_map.get(upper)
        if inv_name:
            df_inv = ak.futures_inventory_em(symbol=inv_name)
            recent = df_inv.tail(20)
            s, e = float(recent['库存'].iloc[0]), float(recent['库存'].iloc[-1])
            data["inventory"] = {"current": e, "20d_ago": s, "change": e - s,
                                  "change_pct": (e - s) / s * 100 if s else 0, "trend": "累库" if e > s else "去库"}
    except Exception as e:
        data["inventory_error"] = str(e)

    return data


def build_futures_prompt(variety_code: str, data: dict) -> str:
    contract = data.get("contract", {})
    name = contract.get("name", variety_code)
    prompt = f"""你是一位资深期货分析师，使用"期限结构 x 价格形态 x 供需 x 事件驱动"的多维分析框架。

请对以下品种进行完整的价格结构分析报告：

品种: {variety_code.upper()} ({name})
交易所: {contract.get('exchange', 'N/A')}
合约乘数: {contract.get('multiplier', 'N/A')}
保证金率: {contract.get('margin_rate', 0) * 100:.1f}%
"""
    if "term_structure" in data:
        prompt += "\n=== 期限结构 ===\n"
        for t in data["term_structure"]:
            prompt += f"  {t['symbol']}: 价格={t['price']} 成交量={t['volume']} 持仓量={t['oi']}\n"
    if "basis" in data:
        prompt += f"\n=== 基差数据 ===\n{json.dumps(data['basis'], ensure_ascii=False, indent=2)}\n"
    if "history" in data:
        h = data["history"]
        prompt += f"\n=== 历史价格 ===\n最新价: {h['latest_price']}  区间: {h['min']}-{h['max']}  百分位: {h['percentile']:.1f}%\nATH: {data.get('ath')}  距ATH: {data.get('ath_distance', 0):.1f}%\n"
    if "performance" in data:
        prompt += "\n近期表现: " + " | ".join(f"{k}:{v:+.2f}%" for k, v in data["performance"].items()) + "\n"
    if "moving_averages" in data:
        prompt += "均线: " + " | ".join(f"{k}:{v:.0f}" for k, v in data["moving_averages"].items()) + "\n"
    if data.get("rsi14"):
        prompt += f"RSI(14): {data['rsi14']:.1f}\n"
    if "inventory" in data:
        inv = data["inventory"]
        prompt += f"\n=== 库存 ===\n当前: {inv['current']:.0f}  20日前: {inv['20d_ago']:.0f}  变化: {inv['change']:+.0f} ({inv['change_pct']:+.1f}%)  趋势: {inv['trend']}\n"
    if "contracts_list" in data:
        prompt += "\n=== 各合约 ===\n"
        for c in data["contracts_list"]:
            prompt += f"  {c['code']}: 价格={c['price']} 成交量={c['volume']} 持仓量={c['oi']}\n"
    if "recent_bars" in data:
        prompt += "\n=== 近10日 ===\n"
        for b in data["recent_bars"]:
            prompt += f"  {b['date']} O:{b['O']} H:{b['H']} L:{b['L']} C:{b['C']} V:{b['V']}\n"
    prompt += """
=== 输出要求 ===
生成完整中文价格结构分析报告（Markdown），包含：
## 1. 合约要素
## 2. 期限结构概览（正向/反向/混合，曲线形态）
## 3. 基差状态（基差=现货-期货）
## 4. 关键价差（跨月价差，年化率，与持仓成本对比）
## 5. 供需分析（库存、供给、需求、成本、平衡判断）
## 6. 价格形态分析（趋势、摆动结构、支撑阻力、动量、量价）
## 7. 事件驱动分析
## 8. EXTREME/CLOCK/GEO/TRENDWISE/ATH评估
## 9. 结构综合研判（多维评估表+关键含义+信号关注）
"""
    return prompt


def build_strategy_prompt(variety_code: str, data: dict) -> str:
    contract = data.get("contract", {})
    name = contract.get("name", variety_code)
    h = data.get("history", {})
    inv = data.get("inventory", {})
    basis = data.get("basis", {})
    mas = data.get("moving_averages", {})
    perf = data.get("performance", {})
    prompt = f"""基于以下{variety_code.upper()}（{name}）的价格结构数据，生成完整的交易策略报告：

核心数据：
- 当前价格：{h.get('latest_price', 'N/A')}（主力合约）
- 现货价格：{basis.get('spot_price', 'N/A')}
- 库存：{inv.get('current', 'N/A')}，20日变化：{inv.get('change_pct', 0):+.1f}%（{inv.get('trend', 'N/A')}）
- 均线：{' | '.join(f'{k}:{v:.0f}' for k, v in mas.items())}
- RSI(14): {data.get('rsi14', 'N/A')}
- ATH: {data.get('ath', 'N/A')}（距ATH: {data.get('ath_distance', 0):.1f}%）
- 历史百分位: {h.get('percentile', 0):.1f}%
- 近期表现: {' | '.join(f'{k}:{v:+.2f}%' for k, v in perf.items())}
"""
    if "term_structure" in data:
        prompt += "\n各合约价格：\n"
        for t in data["term_structure"]:
            prompt += f"  {t['symbol']}: {t['price']}  持仓量={t['oi']}\n"
    if "recent_bars" in data:
        prompt += "\n近10日走势：\n"
        for b in data["recent_bars"]:
            prompt += f"  {b['date']} O:{b['O']} H:{b['H']} L:{b['L']} C:{b['C']} V:{b['V']}\n"
    prompt += """
请输出完整中文交易策略报告（Markdown），包含：
## 一、多空情景分析（偏多/偏空/震荡各自概率%、触发条件、目标区）
## 二、方向性交易策略
### 策略1：趋势多单（入场/止损/目标位/仓位/盈亏比）
### 策略2：回调做多（入场/止损/目标位/仓位/盈亏比）
### 策略3：高位做空短线（入场/止损/目标位/仓位/风险警告）
## 三、跨期套利策略（合约组合/入场价差/目标/止损/逻辑）
## 四、风险管理（仓位原则/止损纪律/极端信号）
## 五、每日跟踪清单（关键价位/指标/信号）
## 六、策略优先级排序

所有价位精确到整数，盈亏比清晰。"""
    return prompt


def _data_block(vc, data):
    h = data.get("history", {})
    mas = data.get("moving_averages", {})
    perf = data.get("performance", {})
    b = f"品种: {vc}\n当前价格: {h.get('latest_price','N/A')}\nRSI: {data.get('rsi14','N/A')}\n"
    b += f"ATH: {data.get('ath','N/A')} ({data.get('ath_distance',0):.1f}%)\n百分位: {h.get('percentile',0):.1f}%\n"
    b += f"均线: {' | '.join(f'{k}:{v:.0f}' for k,v in mas.items())}\n"
    b += f"近期: {' | '.join(f'{k}:{v:+.2f}%' for k,v in perf.items())}\n"
    if "term_structure" in data:
        b += "期限结构:\n"
        for t in data["term_structure"]: b += f"  {t['symbol']}: {t['price']} OI={t['oi']}\n"
    if "recent_bars" in data:
        b += "近10日:\n"
        for r in data["recent_bars"]: b += f"  {r['date']} O:{r['O']} H:{r['H']} L:{r['L']} C:{r['C']} V:{r['V']}\n"
    return b

def build_table_prompt(vc, data):
    return f"基于以下数据生成{vc}的表格版交易计划（Markdown表格）：\n\n{_data_block(vc,data)}\n\n输出：\n## 表格1：多空情景概率\n| 情景 | 概率 | 触发条件 | 目标区 |\n\n## 表格2：方向性策略一览\n| 策略 | 方向 | 入场价 | 止损价 | 目标1 | 目标2 | 风险 | 盈亏比 | 仓位 |\n（突破做多/回调做多/高位做空）\n\n## 表格3：跨期套利\n| 合约组合 | 入场价差 | 目标价差 | 止损价差 | 盈亏比 |\n\n## 表格4：关键价位速查\n| 类型 | 价位 | 说明 |\n\n## 表格5：策略优先级\n| 优先级 | 策略 | 理由 |\n\n所有价位精确到整数。"

def build_intraday_prompt(vc, data):
    return f"基于以下数据生成{vc}的日内短线交易策略（中文Markdown）：\n\n{_data_block(vc,data)}\n\n输出：\n## 一、日内偏向判断\n## 二、日内关键价位（阻力/支撑/枢轴点）\n## 三、日内策略\n### A：开盘突破跟随（入场/止损/目标/持仓时间）\n### B：区间高抛低吸\n### C：尾盘趋势单\n## 四、日内风控（单笔止损/日最大亏损/不交易时段）\n## 五、执行时间表\n| 时间段 | 操作 | 注意事项 |\n（9:00-10:15/10:30-11:30/13:30-15:00/21:00-23:00）\n\n所有价位精确到整数。"

def build_swing_prompt(vc, data):
    return f"基于以下数据生成{vc}的Swing波段交易策略（中文Markdown）：\n\n{_data_block(vc,data)}\n\n输出：\n## 一、波段趋势判断（周线/日线/当前位置）\n## 二、波段策略\n### 1：趋势波段多单（分批建仓/止损/目标/持仓周期/盈亏比）\n### 2：反弹波段空单\n### 3：区间波段\n## 三、加仓与减仓规则\n## 四、波段风控\n## 五、交易日历（未来2-4周关键节点）\n## 六、与跨期套利配合\n\n所有价位精确到整数。"

def build_orders_prompt(vc, data):
    return f"基于以下数据生成{vc}的盘中挂单策略清单（中文Markdown）：\n\n{_data_block(vc,data)}\n\n输出：\n## 挂单总表\n| 编号 | 方向 | 类型 | 挂单价 | 止损价 | 目标价 | 盈亏比 | 备注 |\n（突破买入/回调买入/二次回调/反弹做空/破位做空/跨期正套，至少6笔）\n\n## 每笔挂单详解（逻辑/触发后操作/取消条件）\n## 挂单管理规则（开盘调整/盘中撤回/收盘处理）\n## 仓位汇总\n| 情景 | 持仓方向 | 总仓位 | 总风险 |\n\n所有价位精确到整数。"


def build_risk_prompt(vc, data):
    return f"基于以下数据生成{vc}的专项风险管理报告（中文Markdown）：\n\n{_data_block(vc,data)}\n\n输出：\n## 一、风险全景评估\n### 1.1 价格风险（百分位、最大回撤估算、单日最大亏损按1/5/10手）\n### 1.2 流动性风险（主力vs远月、日盘vs夜盘）\n### 1.3 期限结构风险（展期、逼仓、基差收敛）\n### 1.4 相关性风险（产业链、宏观因子）\n\n## 二、仓位管理体系\n### 2.1 单品种仓位上限（保守/标准/激进，按100万/500万/1000万账户计算）\n### 2.2 波动率调整公式：仓位 = 风险预算 / (ATR × 合约乘数)\n### 2.3 加仓规则（浮盈加仓条件、金字塔法则）\n\n## 三、止损体系\n### 技术止损（关键价位）\n### 时间止损（N天未盈利）\n### 资金止损（单笔/日/周/月限额）\n\n## 四、极端风险预案（涨跌停封板、流动性枯竭、政策突变）\n\n## 五、风险指标监控看板\n| 指标 | 当前值 | 安全区 | 警戒区 | 危险区 | 状态 |\n（8-10个指标）\n\n## 六、风险检查清单（盘前/盘中/盘后）\n| 序号 | 检查项 | 标准 | 操作 |\n\n所有金额精确到整数。"

def build_checklist_prompt(vc, data):
    return f"基于以下数据生成{vc}的今日操盘跟踪清单（中文Markdown）：\n\n{_data_block(vc,data)}\n\n输出：\n## 一、关键价位速查\n| 类型 | 价位 | 说明 | 触发后操作 |\n（阻力4个+支撑4个+枢轴点）\n\n## 二、今日方向判断（偏多/偏空/震荡+3个理由+置信度）\n\n## 三、今日交易计划\n| 策略 | 方向 | 入场条件 | 入场价 | 止损价 | 目标价 | 仓位 | 执行状态 |\n（3-5个策略）\n\n## 四、监控指标\n| 指标 | 昨日值 | 多头信号 | 空头信号 |\n\n## 五、今日事件提醒\n| 时间 | 事件 | 影响 | 应对 |\n\n## 六、盘前检查\n- [ ] 隔夜外盘确认\n- [ ] 今日数据/事件\n- [ ] 持仓状态确认\n- [ ] 最大可承受亏损\n\n## 七、盘中纪律\n- [ ] 不在开盘15分钟追涨杀跌\n- [ ] 止损不犹豫不扩大\n- [ ] 连续2次止损暂停30分钟\n\n## 八、盘后复盘\n| 项目 | 记录 |\n| 今日盈亏 | |\n| 执行纪律 | 好/一般/差 |\n| 明日关注 | |\n\n所有价位精确到整数。适合打印放在交易台旁。"


@app.get("/api/analyze")
async def analyze_futures(code: str = Query(...), mode: str = Query("analysis")):
    """Call GPT-5.4. mode: analysis/strategy/table/intraday/swing/orders/risk/checklist"""
    import httpx
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OPENAI_API_KEY not set. Export it: export OPENAI_API_KEY=sk-..."}
    upper = code.strip().upper()
    data = gather_futures_data(upper)
    builders = {"analysis": build_futures_prompt, "strategy": build_strategy_prompt,
                "table": build_table_prompt, "intraday": build_intraday_prompt,
                "swing": build_swing_prompt, "orders": build_orders_prompt,
                "risk": build_risk_prompt, "checklist": build_checklist_prompt}
    prompt = builders.get(mode, build_futures_prompt)(upper, data)
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(OPENAI_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": OPENAI_MODEL, "messages": [{"role": "user", "content": prompt}],
                      "max_completion_tokens": 8000, "temperature": 0.2})
            resp.raise_for_status()
            report = resp.json()["choices"][0]["message"]["content"]
            return {"ok": True, "code": upper, "mode": mode, "report": report}
        except Exception as e:
            return {"error": str(e)}


@app.get("/api/trading-agent")
def run_trading_agent(
    code: str = Query(..., description="Futures variety code, e.g. CU, AU, RB, I"),
    account_size: float = Query(DEFAULT_ACCOUNT_SIZE_RMB, gt=0),
    risk_pct: float = Query(0.005, gt=0, le=0.05),
    horizon: str = Query("swing", description="intraday, swing, position"),
    llm: bool = Query(False, description="Use OpenAI to synthesize the final memo"),
    engine: str = Query("official", description="official or lite"),
    target_lots: int = Query(1, ge=0, le=10000, description="Number of futures contracts to open"),
):
    """Run a paper-style multi-agent trading workflow for China futures."""
    try:
        if engine.lower() == "lite":
            return ChinaFuturesTradingAgent().run(
                code,
                account_size=account_size,
                risk_pct=risk_pct,
                horizon=horizon,
                use_llm=llm,
                target_lots=target_lots,
            )

        return OfficialFuturesTradingAgentsAdapter().run(
            code,
            account_size=account_size,
            risk_pct=risk_pct,
            horizon=horizon,
            target_lots=target_lots,
        )
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/trading-agent-lite")
def run_trading_agent_lite(
    code: str = Query(..., description="Futures variety code, e.g. CU, AU, RB, I"),
    account_size: float = Query(DEFAULT_ACCOUNT_SIZE_RMB, gt=0),
    risk_pct: float = Query(0.005, gt=0, le=0.05),
    horizon: str = Query("swing", description="intraday, swing, position"),
    llm: bool = Query(False, description="Use OpenAI to synthesize the final memo"),
    target_lots: int = Query(1, ge=0, le=10000, description="Number of futures contracts to open"),
):
    """Run the fast local futures trading workflow without the official graph."""
    try:
        return ChinaFuturesTradingAgent().run(
            code,
            account_size=account_size,
            risk_pct=risk_pct,
            horizon=horizon,
            use_llm=llm,
            target_lots=target_lots,
        )
    except Exception as e:
        return {"error": str(e)}


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888)
