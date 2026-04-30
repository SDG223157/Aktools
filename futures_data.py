"""
Futures Data Fetcher using AKShare
Usage: python3 futures_data.py
"""

import akshare as ak
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)


def fetch_main_contract_daily(symbol="RB0", start_date="20240101", end_date="20261231"):
    """Fetch continuous/main contract daily OHLCV data from Sina."""
    print(f"\n{'='*60}")
    print(f"1. Main Contract Daily: {symbol}")
    print(f"{'='*60}")
    df = ak.futures_main_sina(symbol=symbol, start_date=start_date, end_date=end_date)
    print(df.tail(10))
    return df


def fetch_contract_daily(symbol="RB2510"):
    """Fetch specific contract daily OHLCV data from Sina."""
    print(f"\n{'='*60}")
    print(f"2. Specific Contract Daily: {symbol}")
    print(f"{'='*60}")
    df = ak.futures_zh_daily_sina(symbol=symbol)
    print(df.tail(10))
    return df


def fetch_minute_data(symbol="RB2510", period="5"):
    """Fetch minute-level data. period: 1, 5, 15, 30, 60"""
    print(f"\n{'='*60}")
    print(f"3. Minute Data: {symbol} ({period}min)")
    print(f"{'='*60}")
    df = ak.futures_zh_minute_sina(symbol=symbol, period=period)
    print(df.tail(10))
    return df


def fetch_spot_and_basis(date="20260429"):
    """Fetch spot prices and basis data for all commodities."""
    print(f"\n{'='*60}")
    print(f"5. Spot Prices & Basis: {date}")
    print(f"{'='*60}")
    df = ak.futures_spot_price(date=date)
    print(df.head(15))
    return df


def fetch_exchange_daily(date="20260429"):
    """Fetch daily data from Shanghai Futures Exchange."""
    print(f"\n{'='*60}")
    print(f"6. SHFE Daily Data: {date}")
    print(f"{'='*60}")
    df = ak.get_shfe_daily(date=date)
    print(df.head(15))
    return df


def fetch_fees_info():
    """Fetch futures trading fees and margin info."""
    print(f"\n{'='*60}")
    print(f"7. Futures Fees & Margin Info")
    print(f"{'='*60}")
    df = ak.futures_fees_info()
    print(df[["合约名称", "合约乘数", "开仓费用/手", "做多保证金/手", "最新价"]].head(15))
    return df


if __name__ == "__main__":
    print("AKShare Futures Data Fetcher")
    print(f"akshare version: {ak.__version__}")

    # Run each fetch with error handling
    functions = [
        fetch_main_contract_daily,
        fetch_contract_daily,
        fetch_minute_data,
        fetch_spot_and_basis,
        fetch_exchange_daily,
        fetch_fees_info,
    ]

    for func in functions:
        try:
            func()
        except Exception as e:
            print(f"\n[ERROR] {func.__name__}: {e}")

    print(f"\n{'='*60}")
    print("Done.")
