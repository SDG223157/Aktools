"""
China futures trading agent built on AKShare data.

The workflow mirrors the TradingAgents paper at a smaller futures-specific
scale: analyst reports, bull/bear debate, trader proposal, risk review, and
portfolio-manager approval.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ACCOUNT_SIZE_RMB = 2_000_000

COMMODITY_KEYWORDS = {
    "IC": ["中证500 股指期货", "CSI 500 futures", "China small cap index futures"],
    "IF": ["沪深300 股指期货", "CSI 300 futures", "China A-shares index"],
    "IH": ["上证50 股指期货", "SSE 50 futures", "China blue chip index"],
    "IM": ["中证1000 股指期货", "CSI 1000 futures", "China small cap index"],
    "T": ["10年期国债期货", "China 10-year treasury futures", "China bond yields"],
    "TF": ["5年期国债期货", "China 5-year treasury futures", "China bond yields"],
    "TL": ["30年期国债期货", "China 30-year treasury futures", "China long bond"],
    "TS": ["2年期国债期货", "China 2-year treasury futures", "China bond yields"],
    "CU": ["铜", "copper", "LME copper", "China copper demand"],
    "AL": ["铝", "aluminum", "aluminium", "China aluminum"],
    "ZN": ["锌", "zinc", "LME zinc"],
    "AU": ["黄金", "gold", "Fed rates", "real yields"],
    "AG": ["白银", "silver", "precious metals"],
    "RB": ["螺纹钢", "rebar", "China steel", "property stimulus"],
    "HC": ["热卷", "hot rolled coil", "China steel"],
    "I": ["铁矿石", "iron ore", "China steel mills"],
    "J": ["焦炭", "coke", "China steel margins"],
    "JM": ["焦煤", "coking coal", "China steel"],
    "SC": ["原油", "crude oil", "OPEC", "China refinery"],
    "M": ["豆粕", "soymeal", "soybean meal", "China soybean"],
    "Y": ["豆油", "soybean oil", "vegetable oil"],
    "P": ["棕榈油", "palm oil", "Malaysia palm oil"],
    "CF": ["棉花", "cotton", "China cotton"],
    "SA": ["纯碱", "soda ash", "China glass"],
    "LC": ["碳酸锂", "lithium carbonate", "EV battery"],
}

BULLISH_NEWS_TERMS = {
    "shortage", "supply disruption", "cuts", "strike", "stimulus", "strong demand",
    "drawdown", "inventory draw", "tight supply", "短缺", "减产", "罢工", "刺激",
    "需求回升", "去库", "供应紧张",
}
BEARISH_NEWS_TERMS = {
    "surplus", "weak demand", "slowdown", "inventory build", "output rises",
    "recession", "property slump", "累库", "过剩", "需求疲弱", "放缓", "增产",
    "地产下行",
}
RISK_NEWS_TERMS = {
    "tariff", "sanction", "war", "ban", "policy", "export restriction", "rate hike",
    "关税", "制裁", "战争", "禁令", "政策", "出口限制", "加息",
}


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _fmt(value: Any, digits: int = 2) -> str:
    number = _to_float(value)
    if number is None:
        return "N/A"
    return f"{number:.{digits}f}"


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


@dataclass
class AgentConfig:
    model: str = "gpt-5.4-mini"
    api_url: str = "https://api.openai.com/v1/chat/completions"
    max_tokens: int = 5000
    temperature: float = 0.2
    output_language: str = "中文"


class NewsDataProvider:
    def fetch(self, code: str, name: str | None = None, limit: int = 8) -> dict[str, Any]:
        query = self._query(code, name)
        articles = self._interleave(
            self._fetch_gnews(query, limit),
            self._fetch_serpapi(query, limit),
        )
        articles = self._dedupe(articles)[:limit]
        score = self._score_articles(articles)
        return {
            "query": query,
            "articles": articles,
            "score": score["score"],
            "bullish_hits": score["bullish_hits"],
            "bearish_hits": score["bearish_hits"],
            "risk_hits": score["risk_hits"],
            "sources": sorted({a["provider"] for a in articles}),
        }

    def _interleave(
        self,
        first: list[dict[str, Any]],
        second: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = []
        for index in range(max(len(first), len(second))):
            if index < len(first):
                result.append(first[index])
            if index < len(second):
                result.append(second[index])
        return result

    def _query(self, code: str, name: str | None) -> str:
        terms = COMMODITY_KEYWORDS.get(code.upper(), [name or code, code])
        return " OR ".join(dict.fromkeys([x for x in terms if x]))

    def _fetch_gnews(self, query: str, limit: int) -> list[dict[str, Any]]:
        api_key = _env_value("GNEWS_API_KEY")
        if not api_key:
            return []

        import httpx

        try:
            response = httpx.get(
                "https://gnews.io/api/v4/search",
                params={
                    "q": query[:200],
                    "max": min(limit, 10),
                    "sortby": "publishedAt",
                    "apikey": api_key,
                },
                timeout=20,
            )
            response.raise_for_status()
            return [self._gnews_article(item) for item in response.json().get("articles", [])]
        except Exception as exc:
            return [{"provider": "gnews", "error": str(exc)}]

    def _fetch_serpapi(self, query: str, limit: int) -> list[dict[str, Any]]:
        api_key = _env_value("SERPAPI_API_KEY", "SERAPI_API_KEY")
        if not api_key:
            return []

        import httpx

        try:
            response = httpx.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google_news",
                    "q": query,
                    "gl": "cn",
                    "hl": "zh-CN",
                    "api_key": api_key,
                },
                timeout=25,
            )
            response.raise_for_status()
            return [
                self._serpapi_article(item)
                for item in self._flatten_serpapi(response.json().get("news_results", []))
            ][:limit]
        except Exception as exc:
            return [{"provider": "serpapi", "error": str(exc)}]

    def _gnews_article(self, item: dict[str, Any]) -> dict[str, Any]:
        source = item.get("source") or {}
        return {
            "provider": "gnews",
            "title": item.get("title"),
            "summary": item.get("description") or item.get("content"),
            "source": source.get("name"),
            "published_at": item.get("publishedAt"),
            "url": item.get("url"),
        }

    def _serpapi_article(self, item: dict[str, Any]) -> dict[str, Any]:
        source = item.get("source") or {}
        return {
            "provider": "serpapi",
            "title": item.get("title"),
            "summary": item.get("snippet") or item.get("type"),
            "source": source.get("name") or source.get("title"),
            "published_at": item.get("iso_date") or item.get("date"),
            "url": item.get("link"),
        }

    def _flatten_serpapi(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flattened = []
        for item in items:
            if item.get("title"):
                flattened.append(item)
            flattened.extend(item.get("stories") or [])
        return flattened

    def _dedupe(self, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        result = []
        for article in articles:
            if article.get("error"):
                result.append(article)
                continue
            key = (article.get("url") or article.get("title") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(article)
        return result

    def _score_articles(self, articles: list[dict[str, Any]]) -> dict[str, Any]:
        bullish = bearish = risk = 0
        for article in articles:
            text = f"{article.get('title') or ''} {article.get('summary') or ''}".lower()
            bullish += sum(term in text for term in BULLISH_NEWS_TERMS)
            bearish += sum(term in text for term in BEARISH_NEWS_TERMS)
            risk += sum(term in text for term in RISK_NEWS_TERMS)
        raw = bullish - bearish
        return {
            "score": max(-1, min(1, raw / 3)) if raw else 0,
            "bullish_hits": bullish,
            "bearish_hits": bearish,
            "risk_hits": risk,
        }


class FuturesDataProvider:
    def gather(
        self,
        code: str,
        start_date: str = "20240101",
        end_date: str | None = None,
    ) -> dict[str, Any]:
        import akshare as ak
        import numpy as np
        import pandas as pd

        upper = code.strip().upper().replace("0", "")
        end_date = end_date or dt.date.today().strftime("%Y%m%d")
        data: dict[str, Any] = {"code": upper, "symbol": f"{upper}0"}

        self._add_contract_info(ak, pd, data, upper)
        self._add_term_structure(ak, pd, data, upper)
        self._add_basis(ak, pd, np, data, upper)
        self._add_history(ak, pd, data, upper, start_date, end_date)
        self._add_inventory(ak, data, upper)
        data["news"] = NewsDataProvider().fetch(
            upper,
            data.get("contract", {}).get("name"),
        )
        return data

    def _add_contract_info(self, ak: Any, pd: Any, data: dict[str, Any], code: str) -> None:
        try:
            fees = ak.futures_fees_info()
            rows = fees[fees["品种代码"].astype(str).str.upper() == code]
            if rows.empty:
                return

            row = rows.iloc[0]
            data["contract"] = {
                "exchange": row.get("交易所"),
                "name": row.get("品种名称"),
                "code": row.get("品种代码"),
                "multiplier": _to_float(row.get("合约乘数"), 1),
                "tick": _to_float(row.get("最小跳动"), 1),
                "margin_rate": _to_float(row.get("做多保证金率"), 0),
            }
            data["contracts"] = [
                {
                    "symbol": r.get("合约代码"),
                    "price": _to_float(r.get("最新价")),
                    "volume": _to_int(r.get("成交量")),
                    "open_interest": _to_int(r.get("持仓量")),
                }
                for _, r in rows.iterrows()
            ]
        except Exception as exc:
            data["contract_error"] = str(exc)

    def _add_term_structure(self, ak: Any, pd: Any, data: dict[str, Any], code: str) -> None:
        try:
            cn_name = self._resolve_cn_name(ak, code)
            if not cn_name:
                return

            realtime = ak.futures_zh_realtime(symbol=cn_name)
            rows = realtime[~realtime["symbol"].astype(str).str.endswith("0")]
            term = [
                {
                    "symbol": r.get("symbol"),
                    "price": _to_float(r.get("trade")),
                    "volume": _to_int(r.get("volume")),
                    "open_interest": _to_int(r.get("position")),
                }
                for _, r in rows.iterrows()
            ]
            data["term_structure"] = sorted(
                [x for x in term if x["price"] is not None],
                key=lambda x: str(x["symbol"]),
            )
        except Exception as exc:
            data["term_structure_error"] = str(exc)

    def _add_basis(self, ak: Any, pd: Any, np: Any, data: dict[str, Any], code: str) -> None:
        for days_back in range(7):
            date = (dt.date.today() - dt.timedelta(days=days_back)).strftime("%Y%m%d")
            try:
                basis_df = ak.futures_spot_price(date=date)
                rows = basis_df[basis_df["symbol"].astype(str).str.upper() == code]
                if rows.empty:
                    continue

                row = rows.iloc[0]
                data["basis"] = {
                    col: (
                        float(row[col])
                        if isinstance(row[col], (int, float, np.floating)) and pd.notna(row[col])
                        else str(row[col])
                    )
                    for col in rows.columns
                }
                return
            except Exception:
                continue

    def _add_history(
        self,
        ak: Any,
        pd: Any,
        data: dict[str, Any],
        code: str,
        start_date: str,
        end_date: str,
    ) -> None:
        try:
            hist = ak.futures_main_sina(
                symbol=f"{code}0",
                start_date=start_date,
                end_date=end_date,
            )
            hist["date"] = pd.to_datetime(hist["日期"])
            hist["open"] = pd.to_numeric(hist["开盘价"], errors="coerce")
            hist["high"] = pd.to_numeric(hist["最高价"], errors="coerce")
            hist["low"] = pd.to_numeric(hist["最低价"], errors="coerce")
            hist["close"] = pd.to_numeric(hist["收盘价"], errors="coerce")
            hist["volume"] = pd.to_numeric(hist["成交量"], errors="coerce")
            hist = hist.dropna(subset=["close"]).reset_index(drop=True)
            if hist.empty:
                return

            close = hist["close"]
            latest = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) > 1 else latest
            data["history"] = {
                "latest_date": str(hist["date"].iloc[-1].date()),
                "latest_price": latest,
                "daily_change_pct": (latest / prev - 1) * 100 if prev else 0,
                "bars": len(hist),
                "min": float(close.min()),
                "max": float(close.max()),
                "percentile": float((close < latest).mean() * 100),
                "ath": float(hist["high"].max()),
                "ath_distance_pct": (latest / float(hist["high"].max()) - 1) * 100,
            }
            data["indicators"] = self._indicators(hist)
            data["recent_bars"] = [
                {
                    "date": str(r["date"].date()),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": _to_int(r["volume"]),
                }
                for _, r in hist.tail(10).iterrows()
            ]
        except Exception as exc:
            data["history_error"] = str(exc)

    def _add_inventory(self, ak: Any, data: dict[str, Any], code: str) -> None:
        name_map = {
            "CU": "沪铜",
            "AL": "沪铝",
            "ZN": "沪锌",
            "AU": "沪金",
            "AG": "沪银",
            "RB": "螺纹钢",
            "I": "铁矿石",
            "J": "焦炭",
            "M": "豆粕",
            "NI": "镍",
        }
        try:
            inv_name = name_map.get(code)
            if not inv_name:
                return

            inv = ak.futures_inventory_em(symbol=inv_name).tail(20)
            if inv.empty:
                return

            start = float(inv["库存"].iloc[0])
            current = float(inv["库存"].iloc[-1])
            change = current - start
            data["inventory"] = {
                "current": current,
                "change": change,
                "change_pct": change / start * 100 if start else 0,
                "trend": "累库" if change > 0 else "去库",
            }
        except Exception as exc:
            data["inventory_error"] = str(exc)

    def _resolve_cn_name(self, ak: Any, code: str) -> str | None:
        try:
            symbols = ak.futures_symbol_mark()
            for _, row in symbols.iterrows():
                if code.lower() in str(row.get("mark", "")).lower():
                    return str(row.get("symbol"))
        except Exception:
            pass

        fallback = {
            "CU": "沪铜",
            "AL": "沪铝",
            "ZN": "沪锌",
            "AU": "黄金",
            "AG": "白银",
            "RB": "螺纹钢",
            "I": "铁矿石",
            "J": "焦炭",
            "M": "豆粕",
            "NI": "镍",
            "SC": "原油",
        }
        return fallback.get(code)

    def _indicators(self, hist: Any) -> dict[str, Any]:
        close = hist["close"]
        high = hist["high"]
        low = hist["low"]
        volume = hist["volume"]

        indicators: dict[str, Any] = {}
        for window in (5, 20, 60, 120, 250):
            ma = close.rolling(window).mean()
            if not math.isnan(float(ma.iloc[-1])):
                indicators[f"ma{window}"] = float(ma.iloc[-1])

        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        indicators["rsi14"] = _to_float(rsi.iloc[-1])

        prev_close = close.shift(1)
        true_range = (high - low).to_frame("hl")
        true_range["hc"] = (high - prev_close).abs()
        true_range["lc"] = (low - prev_close).abs()
        atr = true_range.max(axis=1).rolling(14).mean()
        indicators["atr14"] = _to_float(atr.iloc[-1])
        indicators["volume_ratio20"] = _to_float(volume.iloc[-1] / volume.tail(20).mean())

        for days, label in ((5, "1w"), (20, "1m"), (60, "3m"), (120, "6m")):
            if len(close) > days:
                indicators[f"return_{label}"] = float((close.iloc[-1] / close.iloc[-days - 1] - 1) * 100)

        indicators["support20"] = float(low.tail(20).min())
        indicators["resistance20"] = float(high.tail(20).max())
        return indicators


class ChinaFuturesTradingAgent:
    def __init__(self, config: AgentConfig | None = None):
        self.config = config or AgentConfig()

    def run(
        self,
        code: str,
        account_size: float = DEFAULT_ACCOUNT_SIZE_RMB,
        risk_pct: float = 0.005,
        horizon: str = "swing",
        use_llm: bool = False,
        start_date: str = "20240101",
        end_date: str | None = None,
        target_lots: int | None = None,
    ) -> dict[str, Any]:
        data = FuturesDataProvider().gather(code, start_date=start_date, end_date=end_date)
        return self.run_from_data(data, account_size, risk_pct, horizon, use_llm, target_lots)

    def run_from_data(
        self,
        data: dict[str, Any],
        account_size: float = DEFAULT_ACCOUNT_SIZE_RMB,
        risk_pct: float = 0.005,
        horizon: str = "swing",
        use_llm: bool = False,
        target_lots: int | None = None,
    ) -> dict[str, Any]:
        scores = self._score(data)
        proposal = self._trader_proposal(data, scores, account_size, risk_pct, horizon, target_lots)
        risk_report = self._risk_report(data, proposal, account_size, risk_pct)
        manager_decision = self._manager_decision(data, proposal, risk_report)
        result = {
            "code": data.get("code"),
            "symbol": data.get("symbol"),
            "as_of": data.get("history", {}).get("latest_date"),
            "decision": manager_decision["final_action"],
            "confidence": proposal["confidence"],
            "reports": {
                "market_analyst": self._market_report(data, scores),
                "price_structure_analyst": self._price_structure_report(data),
                "term_structure_analyst": self._term_structure_report(data, scores),
                "fundamental_analyst": self._fundamental_report(data, scores),
                "news_analyst": self._news_report(data, scores),
                "bull_researcher": self._bull_report(data, scores),
                "bear_researcher": self._bear_report(data, scores),
                "trader": proposal,
                "risk_manager": risk_report,
                "portfolio_manager": manager_decision,
            },
            "data": data,
        }
        if use_llm:
            result["llm_report"] = self._llm_report(result)
        return result

    def _score(self, data: dict[str, Any]) -> dict[str, Any]:
        history = data.get("history", {})
        indicators = data.get("indicators", {})
        latest = _to_float(history.get("latest_price"))
        ma20 = _to_float(indicators.get("ma20"))
        ma60 = _to_float(indicators.get("ma60"))
        rsi = _to_float(indicators.get("rsi14"))
        return_1m = _to_float(indicators.get("return_1m"), 0) or 0
        return_3m = _to_float(indicators.get("return_3m"), 0) or 0
        inventory = data.get("inventory", {})

        trend = 0
        if latest and ma20:
            trend += 1 if latest > ma20 else -1
        if ma20 and ma60:
            trend += 1 if ma20 > ma60 else -1
        if return_1m > 3:
            trend += 1
        elif return_1m < -3:
            trend -= 1

        momentum = 0
        if rsi is not None:
            if 52 <= rsi <= 70:
                momentum += 1
            elif rsi > 78:
                momentum -= 1
            elif rsi < 35:
                momentum += 0.5
            elif rsi < 45:
                momentum -= 0.5
        momentum += 0.5 if return_3m > 5 else -0.5 if return_3m < -5 else 0

        structure = self._term_structure_score(data)
        news_score = _to_float(data.get("news", {}).get("score"), 0) or 0
        supply = 0
        if inventory:
            supply = -1 if inventory.get("trend") == "累库" else 1

        total = trend + momentum + structure + supply + news_score
        return {
            "trend": trend,
            "momentum": momentum,
            "term_structure": structure,
            "news": news_score,
            "supply_demand": supply,
            "total": total,
            "bias": "bullish" if total >= 2 else "bearish" if total <= -2 else "neutral",
        }

    def _term_structure_score(self, data: dict[str, Any]) -> float:
        term = data.get("term_structure") or []
        prices = [x["price"] for x in term if x.get("price") is not None]
        if len(prices) < 2:
            return 0
        slope = (prices[-1] / prices[0] - 1) * 100
        if slope < -1:
            return 1
        if slope > 1:
            return -0.5
        return 0

    def _market_report(self, data: dict[str, Any], scores: dict[str, Any]) -> dict[str, Any]:
        history = data.get("history", {})
        indicators = data.get("indicators", {})
        latest = history.get("latest_price")
        support = indicators.get("support20")
        resistance = indicators.get("resistance20")
        return {
            "role": "Market Analyst",
            "summary": (
                f"主力{data.get('symbol')}收于{_fmt(latest)}，20日支撑/阻力为"
                f"{_fmt(support)} / {_fmt(resistance)}，RSI14={_fmt(indicators.get('rsi14'), 1)}。"
            ),
            "score": scores["trend"] + scores["momentum"],
            "key_metrics": {
                "latest": latest,
                "ma20": indicators.get("ma20"),
                "ma60": indicators.get("ma60"),
                "atr14": indicators.get("atr14"),
                "support20": support,
                "resistance20": resistance,
            },
        }

    def _term_structure_report(self, data: dict[str, Any], scores: dict[str, Any]) -> dict[str, Any]:
        term = data.get("term_structure") or []
        shape = "unknown"
        if len(term) >= 2 and term[0].get("price") and term[-1].get("price"):
            slope = (term[-1]["price"] / term[0]["price"] - 1) * 100
            shape = "backwardation" if slope < -1 else "contango" if slope > 1 else "flat/mixed"
        return {
            "role": "Term Structure Analyst",
            "summary": f"期限结构为{shape}，结构得分{scores['term_structure']:+.1f}。",
            "shape": shape,
            "front_contract": term[0] if term else None,
            "far_contract": term[-1] if term else None,
        }

    def _price_structure_report(self, data: dict[str, Any]) -> dict[str, Any]:
        contract = data.get("contract", {})
        term = data.get("term_structure") or []
        basis = self._traditional_basis(data.get("basis") or {})
        spreads = self._calendar_spreads(term)
        trend = self._trend_state(data)
        tick_value = (
            (_to_float(contract.get("multiplier"), 1) or 1)
            * (_to_float(contract.get("tick"), 1) or 1)
        )
        summary = self._price_structure_markdown(
            data,
            tick_value,
            self._term_shape(term),
            basis,
            spreads,
            trend,
        )
        return {
            "role": "Futures Price Structure Analyst",
            "skill_source": "/Users/sdg223157/.claude/skills/futures-price-structure-analysis/SKILL.md",
            "summary": summary,
            "term_shape": self._term_shape(term),
            "basis": basis,
            "calendar_spreads": spreads,
            "trend_state": trend,
            "tick_value": tick_value,
        }

    def _term_shape(self, term: list[dict[str, Any]]) -> dict[str, Any]:
        prices = [_to_float(item.get("price")) for item in term]
        prices = [price for price in prices if price is not None]
        if len(prices) < 2:
            return {"type": "unknown", "slope_pct": None, "description": "期限结构数据不足"}

        diffs = [prices[index + 1] - prices[index] for index in range(len(prices) - 1)]
        slope_pct = (prices[-1] / prices[0] - 1) * 100 if prices[0] else 0
        if abs(slope_pct) < 0.3:
            shape = "flat"
            description = "整体平坦，月间价差较小"
        elif all(diff >= 0 for diff in diffs):
            shape = "contango"
            description = "正向市场，远月高于近月"
        elif all(diff <= 0 for diff in diffs):
            shape = "backwardation"
            description = "反向市场，近月高于远月"
        else:
            shape = "mixed"
            description = "混合期限结构，部分月份价差方向不一致"
        return {"type": shape, "slope_pct": slope_pct, "description": description}

    def _traditional_basis(self, basis: dict[str, Any]) -> dict[str, Any]:
        spot = _to_float(basis.get("spot_price"))
        near_price = _to_float(basis.get("near_contract_price"))
        dominant_price = _to_float(basis.get("dominant_contract_price"))
        if not spot:
            return {"available": False}

        near_basis = spot - near_price if near_price else None
        dominant_basis = spot - dominant_price if dominant_price else None
        return {
            "available": True,
            "spot_price": spot,
            "near_contract": basis.get("near_contract"),
            "near_basis": near_basis,
            "near_basis_rate": near_basis / spot * 100 if near_basis is not None else None,
            "dominant_contract": basis.get("dominant_contract"),
            "dominant_basis": dominant_basis,
            "dominant_basis_rate": dominant_basis / spot * 100 if dominant_basis is not None else None,
            "interpretation": "现货升水" if (dominant_basis or 0) > 0 else "期货升水" if (dominant_basis or 0) < 0 else "基差接近平水",
        }

    def _calendar_spreads(self, term: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        clean = [item for item in term if _to_float(item.get("price")) is not None]
        for front, back in zip(clean, clean[1:]):
            front_price = _to_float(front.get("price")) or 0
            back_price = _to_float(back.get("price")) or 0
            spread = back_price - front_price
            rows.append({
                "pair": f"{front.get('symbol')}-{back.get('symbol')}",
                "spread": spread,
                "spread_pct": spread / front_price * 100 if front_price else None,
                "signal": "正向价差" if spread > 0 else "反向价差" if spread < 0 else "平水",
            })
        return rows[:6]

    def _trend_state(self, data: dict[str, Any]) -> dict[str, Any]:
        history = data.get("history", {})
        indicators = data.get("indicators", {})
        latest = _to_float(history.get("latest_price"))
        ma5 = _to_float(indicators.get("ma5"))
        ma20 = _to_float(indicators.get("ma20"))
        ma60 = _to_float(indicators.get("ma60"))
        ma250 = _to_float(indicators.get("ma250"))
        if latest and ma5 and ma20 and ma60 and ma5 > ma20 > ma60 and latest > ma20:
            trend = "上涨趋势"
        elif latest and ma5 and ma20 and ma60 and ma5 < ma20 < ma60 and latest < ma20:
            trend = "下跌趋势"
        elif latest and ma20 and ma60 and latest > ma20 and latest > ma60:
            trend = "偏多震荡"
        elif latest and ma20 and ma60 and latest < ma20 and latest < ma60:
            trend = "偏空震荡"
        else:
            trend = "横盘/混合"
        return {
            "trend": trend,
            "price_vs_ma20_pct": (latest / ma20 - 1) * 100 if latest and ma20 else None,
            "price_vs_ma60_pct": (latest / ma60 - 1) * 100 if latest and ma60 else None,
            "price_vs_ma250_pct": (latest / ma250 - 1) * 100 if latest and ma250 else None,
            "support20": indicators.get("support20"),
            "resistance20": indicators.get("resistance20"),
            "atr14": indicators.get("atr14"),
            "rsi14": indicators.get("rsi14"),
            "volume_ratio20": indicators.get("volume_ratio20"),
        }

    def _price_structure_markdown(
        self,
        data: dict[str, Any],
        tick_value: float,
        term_shape: dict[str, Any],
        basis: dict[str, Any],
        spreads: list[dict[str, Any]],
        trend: dict[str, Any],
    ) -> str:
        contract = data.get("contract", {})
        inventory = data.get("inventory") or {}
        spread_lines = [
            f"- {row['pair']}: {row['spread']:+.2f} ({_fmt(row.get('spread_pct'), 2)}%)，{row['signal']}"
            for row in spreads[:4]
        ] or ["- 价差数据不足"]
        basis_text = "基差数据不可用"
        if basis.get("available"):
            basis_text = (
                f"现货{_fmt(basis.get('spot_price'))}；近月基差"
                f"{_fmt(basis.get('near_basis'))} ({_fmt(basis.get('near_basis_rate'), 2)}%)；"
                f"主力基差{_fmt(basis.get('dominant_basis'))} "
                f"({_fmt(basis.get('dominant_basis_rate'), 2)}%)，{basis.get('interpretation')}。"
            )

        return (
            "### 期货价格结构分析\n"
            f"**Skill:** `/Users/sdg223157/.claude/skills/futures-price-structure-analysis/SKILL.md`\n\n"
            f"**合约要素:** {contract.get('exchange', 'N/A')} {contract.get('name', data.get('code'))}，"
            f"合约乘数{_fmt(contract.get('multiplier'), 0)}，最小跳动{_fmt(contract.get('tick'), 2)}，"
            f"每跳盈亏{_fmt(tick_value, 2)}元/手，保证金率"
            f"{_fmt((_to_float(contract.get('margin_rate'), 0) or 0) * 100, 1)}%。\n\n"
            f"**期限结构:** {term_shape.get('description')}；远近月斜率"
            f"{_fmt(term_shape.get('slope_pct'), 2)}%。\n\n"
            f"**基差:** {basis_text}\n\n"
            "**关键跨月价差:**\n"
            + "\n".join(spread_lines)
            + "\n\n"
            f"**库存/供需:** {inventory.get('trend', '库存数据不足')}，20日变化"
            f"{_fmt(inventory.get('change_pct'), 1)}%。\n\n"
            f"**价格形态:** {trend.get('trend')}；价格相对MA20="
            f"{_fmt(trend.get('price_vs_ma20_pct'), 2)}%，相对MA60="
            f"{_fmt(trend.get('price_vs_ma60_pct'), 2)}%，RSI14="
            f"{_fmt(trend.get('rsi14'), 1)}，量比={_fmt(trend.get('volume_ratio20'), 2)}。\n\n"
            f"**关键价位:** 支撑{_fmt(trend.get('support20'))}，阻力"
            f"{_fmt(trend.get('resistance20'))}，ATR14={_fmt(trend.get('atr14'))}。"
        )

    def _fundamental_report(self, data: dict[str, Any], scores: dict[str, Any]) -> dict[str, Any]:
        contract = data.get("contract", {})
        inventory = data.get("inventory")
        inv_text = "库存数据缺失"
        if inventory:
            inv_text = (
                f"{inventory['trend']}，20日变化{inventory['change_pct']:+.1f}%"
            )
        return {
            "role": "Fundamental Analyst",
            "summary": (
                f"{contract.get('name', data.get('code'))}在{contract.get('exchange', 'N/A')}交易，"
                f"合约乘数{_fmt(contract.get('multiplier'), 0)}，保证金率"
                f"{_fmt((_to_float(contract.get('margin_rate'), 0) or 0) * 100, 1)}%。{inv_text}。"
            ),
            "score": scores["supply_demand"],
            "contract": contract,
            "inventory": inventory,
            "basis": data.get("basis"),
        }

    def _news_report(self, data: dict[str, Any], scores: dict[str, Any]) -> dict[str, Any]:
        news = data.get("news") or {}
        articles = [
            article for article in news.get("articles", [])
            if not article.get("error")
        ]
        errors = [
            article for article in news.get("articles", [])
            if article.get("error")
        ]
        if not articles:
            return {
                "role": "News Analyst",
                "summary": "未获取到可用新闻；事件因子不参与方向判断。",
                "score": 0,
                "errors": errors,
            }

        top_titles = [a.get("title") for a in articles[:5] if a.get("title")]
        summary = (
            f"新闻得分{scores['news']:+.1f}，多头/空头/风险词命中分别为"
            f"{news.get('bullish_hits', 0)}/{news.get('bearish_hits', 0)}/"
            f"{news.get('risk_hits', 0)}。"
        )
        return {
            "role": "News Analyst",
            "summary": summary,
            "score": scores["news"],
            "query": news.get("query"),
            "sources": news.get("sources", []),
            "top_titles": top_titles,
            "errors": errors,
        }

    def _bull_report(self, data: dict[str, Any], scores: dict[str, Any]) -> dict[str, Any]:
        reasons = []
        if scores["trend"] > 0:
            reasons.append("价格位于关键均线之上，趋势结构偏多")
        if scores["term_structure"] > 0:
            reasons.append("近月升水/反向市场显示现货或近端偏紧")
        if scores["supply_demand"] > 0:
            reasons.append("库存去化改善供需边际")
        if scores["news"] > 0:
            reasons.append("新闻事件边际偏多")
        if not reasons:
            reasons.append("多头证据不足，应等待突破或回调确认")
        return {"role": "Bull Researcher", "argument": "；".join(reasons)}

    def _bear_report(self, data: dict[str, Any], scores: dict[str, Any]) -> dict[str, Any]:
        reasons = []
        indicators = data.get("indicators", {})
        if scores["trend"] < 0:
            reasons.append("价格弱于均线，趋势结构偏空")
        if scores["term_structure"] < 0:
            reasons.append("远月升水可能反映短期供应宽松或持仓成本压力")
        if scores["supply_demand"] < 0:
            reasons.append("库存累积压制上涨弹性")
        if scores["news"] < 0:
            reasons.append("新闻事件边际偏空")
        if data.get("news", {}).get("risk_hits", 0) > 0:
            reasons.append("新闻中存在政策/地缘/贸易类风险事件")
        if (_to_float(indicators.get("rsi14")) or 0) > 78:
            reasons.append("RSI过热，追多盈亏比下降")
        if not reasons:
            reasons.append("空头证据不足，应等待跌破支撑或反弹失败")
        return {"role": "Bear Researcher", "argument": "；".join(reasons)}

    def _trader_proposal(
        self,
        data: dict[str, Any],
        scores: dict[str, Any],
        account_size: float,
        risk_pct: float,
        horizon: str,
        target_lots: int | None,
    ) -> dict[str, Any]:
        history = data.get("history", {})
        indicators = data.get("indicators", {})
        latest = _to_float(history.get("latest_price"))
        atr = _to_float(indicators.get("atr14"))
        support = _to_float(indicators.get("support20"))
        resistance = _to_float(indicators.get("resistance20"))

        if not latest or not atr:
            return {
                "role": "Trader",
                "decision": "WAIT",
                "confidence": 0.2,
                "rationale": "历史价格或ATR缺失，无法给出可执行交易计划。",
                "level_basis": {
                    "entry": "缺少最新价或ATR14，无法生成入场依据。",
                    "stop": "缺少最新价或ATR14，无法生成止损依据。",
                    "target1": "缺少最新价或ATR14，无法生成目标依据。",
                    "target2": "缺少最新价或ATR14，无法生成目标依据。",
                },
            }

        total = scores["total"]
        decision = "LONG" if total >= 2 else "SHORT" if total <= -2 else "WAIT"
        confidence = min(0.85, 0.35 + abs(total) * 0.1)
        atr_text = f"ATR14={_fmt(atr)}"
        support_text = f"20日支撑={_fmt(support)}"
        resistance_text = f"20日阻力={_fmt(resistance)}"

        if decision == "LONG":
            entry = max(latest, resistance or latest)
            stop = min(support or latest - atr * 1.5, latest - atr * 1.2)
            target1 = entry + atr * 2
            target2 = entry + atr * 3.5
            level_basis = {
                "entry": f"做多入场取当前价与20日阻力的较高值，等待有效突破确认；latest={_fmt(latest)}，{resistance_text}。",
                "stop": f"止损放在20日支撑或1.2倍ATR下方的更保守位置；{support_text}，{atr_text}。",
                "target1": f"第一目标按入场价加2倍ATR估算，用作第一段止盈；entry={_fmt(entry)}，{atr_text}。",
                "target2": f"第二目标按入场价加3.5倍ATR估算，用作趋势延伸目标；entry={_fmt(entry)}，{atr_text}。",
            }
        elif decision == "SHORT":
            entry = min(latest, support or latest)
            stop = max(resistance or latest + atr * 1.5, latest + atr * 1.2)
            target1 = entry - atr * 2
            target2 = entry - atr * 3.5
            level_basis = {
                "entry": f"做空入场取当前价与20日支撑的较低值，等待有效跌破确认；latest={_fmt(latest)}，{support_text}。",
                "stop": f"止损放在20日阻力或1.2倍ATR上方的更保守位置；{resistance_text}，{atr_text}。",
                "target1": f"第一目标按入场价减2倍ATR估算，用作第一段止盈；entry={_fmt(entry)}，{atr_text}。",
                "target2": f"第二目标按入场价减3.5倍ATR估算，用作趋势延伸目标；entry={_fmt(entry)}，{atr_text}。",
            }
        else:
            entry = latest
            stop = None
            target1 = resistance
            target2 = support
            level_basis = {
                "entry": f"等待模式下入场价仅标记当前主力价格，用于重新评估；latest={_fmt(latest)}。",
                "stop": "等待模式没有可执行方向，因此不生成合规止损；突破或跌破确认后再计算。",
                "target1": f"上方观察位取20日阻力，突破并站稳后才转为做多触发位；{resistance_text}。",
                "target2": f"下方观察位取20日支撑，跌破后才转为做空触发位；{support_text}。",
            }

        lots = 0 if decision == "WAIT" else self._position_size(data, entry, account_size, risk_pct, target_lots)
        trade_score = self._trade_score(decision, confidence, entry, stop, target1, target2)
        return {
            "role": "Trader",
            "decision": decision,
            "horizon": horizon,
            "confidence": round(confidence, 2),
            "entry": round(entry) if entry else None,
            "stop": round(stop) if stop else None,
            "target1": round(target1) if target1 else None,
            "target2": round(target2) if target2 else None,
            "lots": lots,
            "executable": decision == "WAIT" or lots > 0,
            "rationale": f"综合得分{total:+.1f}，方向偏向{scores['bias']}。",
            "level_basis": level_basis,
            "trade_score": trade_score,
        }

    def _trade_score(
        self,
        decision: str,
        confidence: float,
        entry: float | None,
        stop: float | None,
        target1: float | None,
        target2: float | None,
    ) -> dict[str, Any]:
        if decision == "WAIT" or not entry or not stop:
            return {
                "score": 0,
                "grade": "WAIT",
                "confidence": round(confidence, 2),
                "summary": "没有可执行方向或止损，交易评分为等待。",
            }

        risk = abs(entry - stop)
        if risk <= 0:
            return {"score": 0, "grade": "INVALID", "confidence": round(confidence, 2)}

        rewards = self._directional_rewards(decision, entry, target1, target2)
        rr1 = rewards["target1"] / risk if rewards["target1"] > 0 else 0
        rr2 = rewards["target2"] / risk if rewards["target2"] > 0 else 0
        blended_rr = rr1 * 0.7 + rr2 * 0.3
        expected_edge = confidence * blended_rr - (1 - confidence)
        score = max(0, min(100, round(50 + expected_edge * 25)))
        return {
            "score": score,
            "grade": self._score_grade(score),
            "confidence": round(confidence, 2),
            "risk_reward_target1": round(rr1, 2),
            "risk_reward_target2": round(rr2, 2),
            "blended_win_loss_ratio": round(blended_rr, 2),
            "expected_edge": round(expected_edge, 2),
            "formula": "score = 50 + 25 * (confidence * conservative_RR - (1 - confidence)); conservative_RR = 70% * RR1 + 30% * RR2",
            "summary": (
                f"信心{confidence:.0%}，目标1盈亏比{rr1:.2f}，目标2盈亏比{rr2:.2f}，"
                f"保守综合盈亏比{blended_rr:.2f}，评分{score}/100。"
            ),
        }

    def _directional_rewards(
        self,
        decision: str,
        entry: float,
        target1: float | None,
        target2: float | None,
    ) -> dict[str, float]:
        if decision == "SHORT":
            return {
                "target1": max(0, entry - (target1 or entry)),
                "target2": max(0, entry - (target2 or entry)),
            }
        return {
            "target1": max(0, (target1 or entry) - entry),
            "target2": max(0, (target2 or entry) - entry),
        }

    def _score_grade(self, score: int) -> str:
        if score >= 75:
            return "A"
        if score >= 60:
            return "B"
        if score >= 50:
            return "C"
        if score >= 35:
            return "D"
        return "F"

    def _risk_report(
        self,
        data: dict[str, Any],
        proposal: dict[str, Any],
        account_size: float,
        risk_pct: float,
    ) -> dict[str, Any]:
        contract = data.get("contract", {})
        multiplier = _to_float(contract.get("multiplier"), 1) or 1
        margin_rate = _to_float(contract.get("margin_rate"), 0) or 0
        entry = _to_float(proposal.get("entry"))
        stop = _to_float(proposal.get("stop"))
        lots = proposal.get("lots") or 0
        per_lot_risk = abs(entry - stop) * multiplier if entry and stop else 0
        margin_per_lot = entry * multiplier * margin_rate if entry else 0
        margin = margin_per_lot * lots
        allocation = account_size * risk_pct
        approved = proposal["decision"] == "WAIT" or (lots > 0 and margin <= account_size)
        return {
            "role": "Risk Manager",
            "approved": approved,
            "contract_multiplier": multiplier,
            "margin_rate": margin_rate,
            "sizing_mode": "target_lots",
            "allocation_budget": round(allocation),
            "max_risk_budget": round(allocation),
            "margin_per_lot": round(margin_per_lot),
            "per_lot_risk": round(per_lot_risk),
            "planned_risk": round(per_lot_risk * lots),
            "estimated_margin": round(margin),
            "risk_notes": [
                "手数由用户输入决定，不再因止损风险预算不足而否决开仓",
                "planned_risk仅为触发止损时的估算亏损，不作为开仓 veto",
                "跳空、涨跌停与夜盘流动性风险需要人工复核",
            ],
        }

    def _manager_decision(
        self,
        data: dict[str, Any],
        proposal: dict[str, Any],
        risk_report: dict[str, Any],
    ) -> dict[str, Any]:
        action = proposal["decision"]
        if not risk_report["approved"]:
            return {
                "role": "Portfolio Manager",
                "final_action": "WAIT",
                "summary": (
                    "保证金或合约参数不足，暂不执行。当前逻辑不再因止损风险预算不足否决交易；"
                    "只检查目标手数对应保证金是否能由账户覆盖。"
                ),
            }
        if action == "WAIT":
            text = "暂不交易，等待价格突破/跌破关键位后重新评估。"
        else:
            text = (
                f"批准{action}计划：入场{proposal.get('entry')}，止损{proposal.get('stop')}，"
                f"目标{proposal.get('target1')}/{proposal.get('target2')}，手数{proposal.get('lots')}。"
            )
        return {"role": "Portfolio Manager", "final_action": action, "summary": text}

    def _position_size(
        self,
        data: dict[str, Any],
        entry: float | None,
        account_size: float,
        risk_pct: float,
        target_lots: int | None,
    ) -> int:
        if target_lots is not None:
            return max(0, int(target_lots))
        if not entry:
            return 0
        multiplier = _to_float(data.get("contract", {}).get("multiplier"), 1) or 1
        margin_rate = _to_float(data.get("contract", {}).get("margin_rate"), 0) or 0
        margin_per_lot = entry * multiplier * margin_rate
        if margin_per_lot <= 0:
            return 0
        lots = int((account_size * risk_pct) // margin_per_lot)
        return max(1, lots) if account_size >= margin_per_lot else 0

    def _llm_report(self, result: dict[str, Any]) -> str:
        import httpx

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return "OPENAI_API_KEY not set; skipped LLM synthesis."

        prompt = (
            "你是中国期货交易委员会。根据以下多代理结构化结果，生成中文Markdown交易备忘录。"
            "必须包含：最终动作、核心证据、交易计划、风险边界、盘中检查清单。"
            "强调这不是投资建议，必须人工复核。\n\n"
            + json.dumps(result, ensure_ascii=False, indent=2, default=str)[:18000]
        )
        with httpx.Client(timeout=120) as client:
            response = client.post(
                self.config.api_url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": self.config.max_tokens,
                    "temperature": self.config.temperature,
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]


class OfficialFuturesTradingAgentsAdapter:
    MARKER = "__FUTURES_TRADINGAGENTS_JSON__"
    OFFICIAL_ROOT = Path("/Users/sdg223157/TradingAgent/tauric-tradingagents")

    def run(
        self,
        code: str,
        account_size: float = DEFAULT_ACCOUNT_SIZE_RMB,
        risk_pct: float = 0.005,
        horizon: str = "swing",
        target_lots: int | None = None,
    ) -> dict[str, Any]:
        baseline = ChinaFuturesTradingAgent().run(
            code,
            account_size=account_size,
            risk_pct=risk_pct,
            horizon=horizon,
            use_llm=False,
            target_lots=target_lots,
        )
        return self._run_bridge(baseline)

    def _run_bridge(self, baseline: dict[str, Any]) -> dict[str, Any]:
        python = self.OFFICIAL_ROOT / ".venv" / "bin" / "python"
        bridge = Path(__file__).with_name("futures_tradingagents_bridge.py")
        if not python.exists():
            return self._fallback(baseline, f"Official TradingAgents python not found: {python}")
        if not bridge.exists():
            return self._fallback(baseline, f"Futures TradingAgents bridge not found: {bridge}")

        temp_path = self._write_payload(baseline)
        try:
            completed = subprocess.run(
                [str(python), str(bridge), temp_path],
                cwd=str(Path(__file__).parent),
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=int(os.getenv("FUTURES_TRADINGAGENTS_TIMEOUT", "600")),
                check=False,
            )
            return self._parse_bridge_output(baseline, completed)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _write_payload(self, baseline: dict[str, Any]) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump({"baseline": baseline}, handle, ensure_ascii=False, default=str)
            return handle.name

    def _parse_bridge_output(
        self,
        baseline: dict[str, Any],
        completed: subprocess.CompletedProcess[str],
    ) -> dict[str, Any]:
        output = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(rf"{self.MARKER}(\{{.*\}})", output, re.DOTALL)
        if match:
            return json.loads(match.group(1))

        error = completed.stderr.strip() or completed.stdout.strip() or "Official bridge returned no JSON."
        return self._fallback(baseline, error[-1200:])

    def _fallback(self, baseline: dict[str, Any], error: str) -> dict[str, Any]:
        baseline["engine"] = "china_futures_agent_fallback"
        baseline["official_tradingagents_error"] = error
        baseline["framework"] = {
            "langgraph": False,
            "official_llm_client": False,
            "futures_contract_analysis": True,
        }
        return baseline


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the China futures trading agent.")
    parser.add_argument("code", help="Futures variety code, e.g. CU, AU, RB, I, IF.")
    parser.add_argument("--account-size", type=float, default=DEFAULT_ACCOUNT_SIZE_RMB)
    parser.add_argument("--risk-pct", type=float, default=0.005)
    parser.add_argument("--target-lots", type=int, default=None)
    parser.add_argument("--horizon", default="swing", choices=["intraday", "swing", "position"])
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--llm", action="store_true", help="Add OpenAI narrative synthesis.")
    args = parser.parse_args()

    result = ChinaFuturesTradingAgent().run(
        args.code,
        account_size=args.account_size,
        risk_pct=args.risk_pct,
        horizon=args.horizon,
        use_llm=args.llm,
        start_date=args.start_date,
        end_date=args.end_date,
        target_lots=args.target_lots,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
