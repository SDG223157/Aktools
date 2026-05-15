from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from tradingagents.llm_clients import create_llm_client
from typing_extensions import TypedDict


MARKER = "__FUTURES_TRADINGAGENTS_JSON__"
OFFICIAL_ROOT = Path("/Users/sdg223157/TradingAgent/tauric-tradingagents")


class FuturesState(TypedDict, total=False):
    baseline: dict[str, Any]
    data: dict[str, Any]
    market_report: str
    price_structure_report: str
    term_report: str
    fundamental_report: str
    news_report: str
    bull_argument: str
    bear_argument: str
    research_plan: str
    trader_rationale: str
    risk_debate: dict[str, str]
    final_summary: str


def load_payload(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def compact_json(value: Any, limit: int = 14000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]


def fallback_summary(report: dict[str, Any], key: str = "summary") -> str:
    return str(report.get(key) or report.get("argument") or "")


def make_llm():
    if not os.getenv("OPENAI_API_KEY"):
        return None

    provider = os.getenv("TRADINGAGENTS_PROVIDER", "openai")
    model = os.getenv("TRADINGAGENTS_FUTURES_MODEL", os.getenv("TRADINGAGENTS_QUICK_MODEL", "gpt-5.4-mini"))
    base_url = os.getenv("TRADINGAGENTS_BASE_URL") or None
    client = create_llm_client(
        provider=provider,
        model=model,
        base_url=base_url,
        timeout=int(os.getenv("TRADINGAGENTS_LLM_TIMEOUT", "120")),
        reasoning_effort=os.getenv("TRADINGAGENTS_REASONING_EFFORT", "low"),
    )
    return client.get_llm()


LLM = make_llm()


def ask_agent(role: str, task: str, context: dict[str, Any], fallback: str) -> str:
    if LLM is None:
        return fallback

    system = (
        f"You are the {role} in a TradingAgents-style multi-agent desk. "
        "Analyze China futures contracts. Be concise, specific, risk-aware, and write in Chinese."
    )
    human = (
        f"{task}\n\n"
        "Use only this structured futures data and baseline plan. Do not invent prices.\n"
        f"{compact_json(context)}"
    )
    try:
        response = LLM.invoke([SystemMessage(content=system), HumanMessage(content=human)])
        return str(response.content).strip() or fallback
    except Exception as exc:
        return f"{fallback}（LLM节点失败：{type(exc).__name__}）"


def load_data_node(state: FuturesState) -> FuturesState:
    baseline = state["baseline"]
    return {"data": baseline.get("data", {})}


def market_analyst_node(state: FuturesState) -> FuturesState:
    reports = state["baseline"].get("reports", {})
    fallback = fallback_summary(reports.get("market_analyst", {}))
    text = ask_agent(
        "Market Analyst",
        "生成价格行为和技术结构报告：趋势、动量、支撑阻力、量价、可交易触发位。",
        {"data": state["data"], "baseline": reports.get("market_analyst", {})},
        fallback,
    )
    return {"market_report": text}


def price_structure_node(state: FuturesState) -> FuturesState:
    reports = state["baseline"].get("reports", {})
    fallback = fallback_summary(reports.get("price_structure_analyst", {}))
    text = ask_agent(
        "Futures Price Structure Analyst",
        (
            "使用 /Users/sdg223157/.claude/skills/futures-price-structure-analysis/SKILL.md 的框架，"
            "检查合约要素、期限结构、传统基差、跨月价差、库存供需、价格形态、支撑阻力、ATR和量价确认。"
        ),
        {"data": state["data"], "baseline": reports.get("price_structure_analyst", {})},
        fallback,
    )
    return {"price_structure_report": text}


def term_structure_node(state: FuturesState) -> FuturesState:
    reports = state["baseline"].get("reports", {})
    fallback = fallback_summary(reports.get("term_structure_analyst", {}))
    text = ask_agent(
        "Term Structure Analyst",
        "生成期限结构报告：近远月曲线、主力/远月价差、展期含义、逼仓或宽松风险。",
        {"term_structure": state["data"].get("term_structure"), "baseline": reports.get("term_structure_analyst", {})},
        fallback,
    )
    return {"term_report": text}


def fundamental_node(state: FuturesState) -> FuturesState:
    reports = state["baseline"].get("reports", {})
    fallback = fallback_summary(reports.get("fundamental_analyst", {}))
    text = ask_agent(
        "Fundamental Analyst",
        "生成基本面报告：合约要素、合约乘数、保证金率、库存/基差/供需状态，以及适用的宏观驱动。",
        {
            "contract": state["data"].get("contract"),
            "inventory": state["data"].get("inventory"),
            "basis": state["data"].get("basis"),
            "baseline": reports.get("fundamental_analyst", {}),
        },
        fallback,
    )
    return {"fundamental_report": text}


def news_node(state: FuturesState) -> FuturesState:
    reports = state["baseline"].get("reports", {})
    fallback = fallback_summary(reports.get("news_analyst", {}))
    text = ask_agent(
        "News Analyst",
        "生成新闻事件报告：GNews/SerpApi标题、风险词、多空事件催化和需要人工确认的新闻。",
        {"news": state["data"].get("news"), "baseline": reports.get("news_analyst", {})},
        fallback,
    )
    return {"news_report": text}


def bull_researcher_node(state: FuturesState) -> FuturesState:
    reports = state["baseline"].get("reports", {})
    fallback = fallback_summary(reports.get("bull_researcher", {}), "argument")
    text = ask_agent(
        "Bull Researcher",
        "基于四位分析师报告，提出最强多头论点、触发条件和失效条件。",
        analyst_context(state),
        fallback,
    )
    return {"bull_argument": text}


def bear_researcher_node(state: FuturesState) -> FuturesState:
    reports = state["baseline"].get("reports", {})
    fallback = fallback_summary(reports.get("bear_researcher", {}), "argument")
    text = ask_agent(
        "Bear Researcher",
        "基于四位分析师报告，提出最强空头论点、触发条件和失效条件。",
        analyst_context(state),
        fallback,
    )
    return {"bear_argument": text}


def research_manager_node(state: FuturesState) -> FuturesState:
    fallback = "综合多空证据后，等待价格触发关键位并保留严格止损。"
    text = ask_agent(
        "Research Manager",
        "裁决多空辩论。输出方向评级、主要证据、反证、交易前必须确认的条件。",
        debate_context(state),
        fallback,
    )
    return {"research_plan": text}


def trader_node(state: FuturesState) -> FuturesState:
    trader = state["baseline"].get("reports", {}).get("trader", {})
    fallback = str(trader.get("rationale") or "")
    text = ask_agent(
        "Trader",
        "将研究经理裁决转化为可执行期货计划。必须尊重已有entry/stop/target/lots、level_basis、trade_score和合约乘数风险约束，并解释stop、target1、target2与评分依据。",
        {"research_plan": state["research_plan"], "trader": trader, "risk": state["baseline"].get("reports", {}).get("risk_manager", {})},
        fallback,
    )
    return {"trader_rationale": text}


def risk_team_node(state: FuturesState) -> FuturesState:
    risk = state["baseline"].get("reports", {}).get("risk_manager", {})
    context = {"plan": state["trader_rationale"], "risk": risk, "data": state["data"]}
    return {
        "risk_debate": {
            "aggressive": ask_agent("Aggressive Risk Analyst", "说明允许交易或提高仓位的条件。", context, "若突破确认且流动性充足，可执行基础手数。"),
            "neutral": ask_agent("Neutral Risk Analyst", "给出中性风控审核。", context, "手数由用户输入决定；审核保证金占用、合约乘数、止损估算亏损与极端跳空风险。"),
            "conservative": ask_agent("Conservative Risk Analyst", "指出拒绝或降仓的风险。", context, "若止损过远、事件风险高或保证金占用过高，应等待。"),
        }
    }


def portfolio_manager_node(state: FuturesState) -> FuturesState:
    manager = state["baseline"].get("reports", {}).get("portfolio_manager", {})
    fallback = str(manager.get("summary") or "")
    text = ask_agent(
        "Portfolio Manager",
        "做最终组合经理裁决。必须尊重risk.approved；若未批准，final action应等待。",
        {
            "research_plan": state["research_plan"],
            "trader_plan": state["trader_rationale"],
            "risk_debate": state["risk_debate"],
            "manager": manager,
        },
        fallback,
    )
    return {"final_summary": text}


def analyst_context(state: FuturesState) -> dict[str, Any]:
    return {
        "market": state.get("market_report"),
        "price_structure": state.get("price_structure_report"),
        "term_structure": state.get("term_report"),
        "fundamental": state.get("fundamental_report"),
        "news": state.get("news_report"),
    }


def debate_context(state: FuturesState) -> dict[str, Any]:
    context = analyst_context(state)
    context["bull"] = state.get("bull_argument")
    context["bear"] = state.get("bear_argument")
    return context


def build_graph():
    graph = StateGraph(FuturesState)
    graph.add_node("load_data", load_data_node)
    graph.add_node("market_analyst", market_analyst_node)
    graph.add_node("price_structure_analyst", price_structure_node)
    graph.add_node("term_structure_analyst", term_structure_node)
    graph.add_node("fundamental_analyst", fundamental_node)
    graph.add_node("news_analyst", news_node)
    graph.add_node("bull_researcher", bull_researcher_node)
    graph.add_node("bear_researcher", bear_researcher_node)
    graph.add_node("research_manager", research_manager_node)
    graph.add_node("trader", trader_node)
    graph.add_node("risk_team", risk_team_node)
    graph.add_node("portfolio_manager", portfolio_manager_node)

    graph.add_edge(START, "load_data")
    graph.add_edge("load_data", "market_analyst")
    graph.add_edge("market_analyst", "price_structure_analyst")
    graph.add_edge("price_structure_analyst", "term_structure_analyst")
    graph.add_edge("term_structure_analyst", "fundamental_analyst")
    graph.add_edge("fundamental_analyst", "news_analyst")
    graph.add_edge("news_analyst", "bull_researcher")
    graph.add_edge("bull_researcher", "bear_researcher")
    graph.add_edge("bear_researcher", "research_manager")
    graph.add_edge("research_manager", "trader")
    graph.add_edge("trader", "risk_team")
    graph.add_edge("risk_team", "portfolio_manager")
    graph.add_edge("portfolio_manager", END)
    return graph.compile()


def merge_result(state: FuturesState) -> dict[str, Any]:
    result = state["baseline"]
    reports = result.setdefault("reports", {})
    reports.setdefault("market_analyst", {})["summary"] = state.get("market_report")
    reports.setdefault("price_structure_analyst", {})["summary"] = state.get("price_structure_report")
    reports.setdefault("term_structure_analyst", {})["summary"] = state.get("term_report")
    reports.setdefault("fundamental_analyst", {})["summary"] = state.get("fundamental_report")
    reports.setdefault("news_analyst", {})["summary"] = state.get("news_report")
    reports.setdefault("bull_researcher", {})["argument"] = state.get("bull_argument")
    reports.setdefault("bear_researcher", {})["argument"] = state.get("bear_argument")
    reports.setdefault("trader", {})["rationale"] = state.get("trader_rationale")
    reports.setdefault("risk_manager", {}).update(state.get("risk_debate", {}))
    reports.setdefault("portfolio_manager", {})["summary"] = state.get("final_summary")
    result["engine"] = "official_futures_tradingagents"
    result["framework"] = {
        "tauricresearch_runtime": str(OFFICIAL_ROOT),
        "langgraph": True,
        "official_llm_client": LLM is not None,
        "futures_contract_analysis": True,
        "analysts": ["market", "price_structure", "term_structure", "fundamental", "news"],
        "skills": ["/Users/sdg223157/.claude/skills/futures-price-structure-analysis/SKILL.md"],
        "researchers": ["bull", "bear"],
        "risk_debaters": ["aggressive", "neutral", "conservative"],
    }
    return result


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: futures_tradingagents_bridge.py /path/to/payload.json")

    payload = load_payload(sys.argv[1])
    baseline = payload["baseline"]
    final_state = build_graph().invoke({"baseline": baseline})
    result = merge_result(final_state)
    print(MARKER + json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
