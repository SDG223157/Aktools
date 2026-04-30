"""
K-Line (Candlestick) Chart for 黄金主力连续 (AU0)
"""

import akshare as ak
import mplfinance as mpf
import pandas as pd

# Fetch data
df = ak.futures_main_sina(symbol="AU0", start_date="20240101", end_date="20261231")

# Prepare data for mplfinance (requires DatetimeIndex and OHLCV columns)
df = df.rename(columns={
    "日期": "Date",
    "开盘价": "Open",
    "最高价": "High",
    "最低价": "Low",
    "收盘价": "Close",
    "成交量": "Volume",
})
df["Date"] = pd.to_datetime(df["Date"])
df = df.set_index("Date")
df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)

# Custom style
mc = mpf.make_marketcolors(
    up="#ef5350",      # red for up (Chinese convention)
    down="#26a69a",    # green for down
    edge="inherit",
    wick="inherit",
    volume="in",
)
style = mpf.make_mpf_style(
    marketcolors=mc,
    figcolor="#1e1e1e",
    facecolor="#1e1e1e",
    edgecolor="#333333",
    gridcolor="#333333",
    gridstyle="--",
    y_on_right=True,
    rc={
        "font.family": "Arial Unicode MS",
        "font.size": 10,
        "axes.labelcolor": "#cccccc",
        "xtick.color": "#cccccc",
        "ytick.color": "#cccccc",
    },
)

# Plot full period
mpf.plot(
    df,
    type="candle",
    style=style,
    title="\nAU0 黄金主力连续 (2024-01 ~ 2026-04)",
    ylabel="Price (CNY/g)",
    ylabel_lower="Volume",
    volume=True,
    mav=(5, 20, 60),
    figsize=(16, 9),
    tight_layout=True,
    savefig=dict(fname="/Users/sdg223157/Aktools/kline_au0_full.png", dpi=150),
)
print("Saved: kline_au0_full.png")

# Plot recent 120 trading days
recent = df.tail(120)
mpf.plot(
    recent,
    type="candle",
    style=style,
    title="\nAU0 黄金主力连续 - Recent 120 Days",
    ylabel="Price (CNY/g)",
    ylabel_lower="Volume",
    volume=True,
    mav=(5, 20, 60),
    figsize=(16, 9),
    tight_layout=True,
    savefig=dict(fname="/Users/sdg223157/Aktools/kline_au0_recent.png", dpi=150),
)
print("Saved: kline_au0_recent.png")
