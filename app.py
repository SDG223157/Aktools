"""
Futures K-Line Website
Run: python3 app.py
Open: http://localhost:8888
"""

import akshare as ak
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI(title="Futures K-Line")


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
    period: str = Query("daily", description="daily, weekly, monthly"),
):
    """Return OHLCV data for a futures main contract."""
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


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888)
