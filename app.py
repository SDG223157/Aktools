"""
Futures K-Line Website + Analysis API
Run: python3 app.py
Open: http://localhost:8888
"""

import os
import json
import akshare as ak
import pandas as pd
import numpy as np
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="Futures K-Line")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

OPENAI_MODEL = "gpt-5.4"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


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
    if period in ("30", "60"):
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


# ===================== REALTIME =====================

# Build code→Chinese name map from fees_info (same source as /api/varieties)
# Variety code → exact Chinese name that futures_zh_realtime expects
_CODE_TO_CN = {
    # SHFE
    "cu": "沪铜", "al": "沪铝", "zn": "沪锌", "pb": "沪铅", "ni": "沪镍", "sn": "沪锡",
    "au": "黄金", "ag": "白银", "rb": "螺纹钢", "wr": "线材", "hc": "热轧卷板",
    "ss": "不锈钢", "fu": "燃油", "bu": "沥青", "ru": "橡胶", "sp": "纸浆",
    "ao": "氧化铝", "br": "丁二烯橡胶", "ad": "铝合金", "op": "胶版纸",
    # INE
    "sc": "原油", "lu": "低硫燃料油", "nr": "20号胶", "bc": "国际铜", "ec": "集运指数(欧线)期货",
    # DCE
    "a": "豆一", "b": "豆二", "m": "豆粕", "y": "豆油", "p": "棕榈油",
    "c": "玉米", "cs": "玉米淀粉", "jd": "鸡蛋", "lh": "生猪",
    "l": "聚乙烯", "pp": "聚丙烯", "v": "聚氯乙烯", "eg": "乙二醇", "eb": "苯乙烯",
    "pg": "液化石油气", "i": "铁矿石", "j": "焦炭", "jm": "焦煤",
    "rr": "粳米", "fb": "纤维板", "bb": "胶合板", "lg": "原木", "bz": "纯苯",
    # CZCE
    "cf": "棉花", "sr": "白糖", "ta": "PTA", "oi": "菜籽油", "rm": "菜粕",
    "ma": "甲醇", "fg": "玻璃", "sa": "纯碱", "ur": "尿素", "ap": "苹果",
    "sf": "硅铁", "sm": "锰硅", "zc": "动力煤", "cy": "棉纱", "px": "对二甲苯",
    "wh": "强麦", "pm": "普麦", "ri": "早籼稻", "jr": "粳稻", "lr": "晚籼稻",
    "rs": "菜籽", "cj": "红枣", "pk": "花生", "pf": "短纤", "sh": "烧碱", "pl": "丙烯", "pr": "瓶片",
    # CFFEX
    "if": "沪深300指数", "ic": "中证500指数", "ih": "上证50指数", "im": "中证1000指数",
    "t": "十年国债", "tf": "五年国债", "tl": "三十年国债", "ts": "二年国债",
    # GFEX
    "lc": "碳酸锂", "si": "工业硅", "ps": "多晶硅", "pd": "钯", "pt": "铂",
}

@app.get("/api/realtime")
def get_realtime(code: str = Query(..., description="Variety code or Chinese name, e.g. cu, 沪铜")):
    """Return real-time quotes for all contracts of a variety."""

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


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888)
