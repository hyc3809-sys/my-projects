"""포트폴리오·종목 지표 계산과 투자 원칙 점검.

계산식(일봉 종가 기준)
- 수익률 = (현재가 / 평단가 - 1) x 100
- 비중 = 평가금액 / (주식 평가금액 합 + 현금) x 100
- 변동성(연환산) = 일간 수익률 표준편차 x sqrt(252)
- RSI(14) = Wilder 방식 지수평활
- 52주 고점 대비 = (현재가 / 최근 252거래일 최고가 - 1) x 100
- 최대낙폭(MDD) = 현재 보유수량 기준 포트폴리오 가치의 1년 최대 하락률
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _r(x, n=2):
    if x is None:
        return None
    try:
        if isinstance(x, (float, np.floating)) and (math.isnan(x) or math.isinf(x)):
            return None
    except TypeError:
        return None
    return round(float(x), n)


def rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) <= period:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_loss = loss.iloc[-1]
    if last_loss == 0:
        return 100.0
    rs = gain.iloc[-1] / last_loss
    return 100 - 100 / (1 + rs)


def price_metrics(df: pd.DataFrame) -> dict:
    """종목·지수 공통 가격 지표."""
    close = df["Close"].astype(float)
    last = close.iloc[-1]
    prev = close.iloc[-2] if len(close) > 1 else None
    win = close.iloc[-TRADING_DAYS:]
    rets = close.pct_change().dropna()

    def ma(n):
        return close.iloc[-n:].mean() if len(close) >= n else None

    def ret_n(n):
        return (last / close.iloc[-n - 1] - 1) * 100 if len(close) > n else None

    vol_ratio = None
    if "Volume" in df and len(df) >= 21:
        v = df["Volume"].astype(float)
        avg20 = v.iloc[-21:-1].mean()
        vol_ratio = v.iloc[-1] / avg20 if avg20 else None

    ma20, ma60, ma120 = ma(20), ma(60), ma(120)
    return {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "close": _r(last, 2),
        "prev_close": _r(prev, 2),
        "chg_pct": _r((last / prev - 1) * 100) if prev else None,
        "ma20": _r(ma20, 2),
        "ma60": _r(ma60, 2),
        "ma120": _r(ma120, 2),
        "above_ma20": bool(last > ma20) if ma20 else None,
        "above_ma60": bool(last > ma60) if ma60 else None,
        "high52": _r(win.max(), 2),
        "low52": _r(win.min(), 2),
        "from_high52_pct": _r((last / win.max() - 1) * 100),
        "ret_1m_pct": _r(ret_n(21)),
        "ret_3m_pct": _r(ret_n(63)),
        "vol20_ann_pct": _r(rets.iloc[-20:].std() * math.sqrt(TRADING_DAYS) * 100)
        if len(rets) >= 20
        else None,
        "rsi14": _r(rsi(close), 1),
        "volume_ratio": _r(vol_ratio),
    }


def portfolio(
    holdings: list[dict],
    histories: dict[str, pd.DataFrame],
    index_hist: pd.DataFrame | None,
    cash: float,
) -> tuple[list[dict], dict]:
    """보유 종목별 지표와 포트폴리오 합계."""
    positions = []
    for h in holdings:
        code = h["code"]
        df = histories.get(code)
        pos = {
            "code": code,
            "name": h.get("name") or code,
            "qty": h["qty"],
            "avg_price": h["avg_price"],
            "memo": h.get("memo", ""),
        }
        if df is None:
            pos["error"] = "시세 조회 실패"
            positions.append(pos)
            continue
        m = price_metrics(df)
        value = h["qty"] * m["close"]
        cost = h["qty"] * h["avg_price"]
        pos.update(m)
        pos.update(
            {
                "value": _r(value, 0),
                "cost": _r(cost, 0),
                "pnl": _r(value - cost, 0),
                "pnl_pct": _r((value / cost - 1) * 100) if cost else None,
                "day_pnl": _r(h["qty"] * (m["close"] - m["prev_close"]), 0)
                if m["prev_close"]
                else None,
            }
        )
        positions.append(pos)

    ok = [p for p in positions if "error" not in p]
    stock_value = sum(p["value"] for p in ok)
    total_cost = sum(p["cost"] for p in ok)
    total_assets = stock_value + (cash or 0)
    for p in ok:
        p["weight_pct"] = _r(p["value"] / total_assets * 100) if total_assets else None

    totals = {
        "stock_value": _r(stock_value, 0),
        "cost": _r(total_cost, 0),
        "pnl": _r(stock_value - total_cost, 0),
        "pnl_pct": _r((stock_value / total_cost - 1) * 100) if total_cost else None,
        "day_pnl": _r(sum(p["day_pnl"] or 0 for p in ok), 0),
        "cash": _r(cash or 0, 0),
        "total_assets": _r(total_assets, 0),
        "cash_pct": _r((cash or 0) / total_assets * 100) if total_assets else None,
        "count": len(positions),
    }
    if totals["day_pnl"] is not None and stock_value:
        prev_value = stock_value - totals["day_pnl"]
        totals["day_pnl_pct"] = _r(totals["day_pnl"] / prev_value * 100) if prev_value else None

    risk = _portfolio_risk(holdings, histories, index_hist)
    weights = sorted((p.get("weight_pct") or 0 for p in ok), reverse=True)
    risk["top_weight_pct"] = weights[0] if weights else None
    risk["top3_weight_pct"] = _r(sum(weights[:3])) if weights else None
    return positions, {"totals": totals, "risk": risk}


def _portfolio_risk(holdings, histories, index_hist) -> dict:
    """현재 보유수량을 1년간 그대로 들고 있었다고 가정한 가치 곡선 기준."""
    series = []
    for h in holdings:
        df = histories.get(h["code"])
        if df is not None:
            series.append(df["Close"].astype(float).iloc[-TRADING_DAYS:] * h["qty"])
    if not series:
        return {}
    value = pd.concat(series, axis=1).ffill().dropna().sum(axis=1)
    if len(value) < 20:
        return {}
    rets = value.pct_change().dropna()
    peak = value.cummax()
    mdd = ((value / peak) - 1).min() * 100
    out = {
        "vol_ann_pct": _r(rets.std() * math.sqrt(TRADING_DAYS) * 100),
        "mdd_1y_pct": _r(mdd),
        "basis_days": int(len(value)),
    }
    if index_hist is not None:
        idx = index_hist["Close"].astype(float).pct_change()
        joined = pd.concat([rets, idx], axis=1, join="inner").dropna()
        if len(joined) >= 20 and joined.iloc[:, 1].var() > 0:
            beta = joined.iloc[:, 0].cov(joined.iloc[:, 1]) / joined.iloc[:, 1].var()
            out["beta_kospi"] = _r(beta)
    return out


def check_rules(positions: list[dict], totals: dict, rules: dict) -> list[dict]:
    """투자 원칙 점검. 사용자가 입력하지 않은 원칙은 점검하지 않음."""
    alerts = []
    max_w = rules.get("max_weight_pct")
    stop = rules.get("stop_loss_pct")
    take = rules.get("take_profit_pct")
    min_cash = rules.get("min_cash_pct")

    def add(level, p, text):
        alerts.append({"level": level, "code": p.get("code"), "name": p.get("name"), "text": text})

    for p in positions:
        if "error" in p:
            add("high", p, f"시세 조회 실패: 종목코드({p['code']}) 확인 필요")
            continue
        if stop is not None and p["pnl_pct"] is not None and p["pnl_pct"] <= -abs(stop):
            add("high", p, f"손절 기준 도달: 수익률 {p['pnl_pct']}% (기준 -{abs(stop)}%)")
        if take is not None and p["pnl_pct"] is not None and p["pnl_pct"] >= take:
            add("mid", p, f"익절 기준 도달: 수익률 +{p['pnl_pct']}% (기준 +{take}%)")
        if max_w is not None and p.get("weight_pct") and p["weight_pct"] > max_w:
            add("mid", p, f"비중 초과: {p['weight_pct']}% (기준 {max_w}%)")
        if p["chg_pct"] is not None and p["chg_pct"] <= -5:
            add("mid", p, f"전일 급락: {p['chg_pct']}%")
        if p["volume_ratio"] is not None and p["volume_ratio"] >= 2:
            add("info", p, f"거래량 급증: 20일 평균의 {p['volume_ratio']}배")
        if p["above_ma60"] is False and p["above_ma20"] is False:
            add("info", p, "20일·60일 이동평균선 모두 하회")
        if p["rsi14"] is not None and p["rsi14"] >= 70:
            add("info", p, f"RSI {p['rsi14']} (70 이상, 과열권)")
        if p["rsi14"] is not None and p["rsi14"] <= 30:
            add("info", p, f"RSI {p['rsi14']} (30 이하, 과매도권)")

    if min_cash is not None and totals.get("cash_pct") is not None and totals["cash_pct"] < min_cash:
        alerts.append(
            {
                "level": "mid",
                "code": None,
                "name": "현금",
                "text": f"현금 비중 부족: {totals['cash_pct']}% (기준 {min_cash}% 이상)",
            }
        )
    order = {"high": 0, "mid": 1, "info": 2}
    return sorted(alerts, key=lambda a: order[a["level"]])
