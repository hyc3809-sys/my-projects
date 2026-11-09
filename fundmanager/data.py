"""시세·지수·뉴스 수집.

- 시세·지수: FinanceDataReader의 네이버 금융 소스(fchart.stock.naver.com)
  - KRX 직접 조회는 로그인이 필요해 사용하지 않음
  - 지수도 네이버 소스로 받음 (FDR 기본 KS11/KQ11은 제3자 GitHub 캐시를 거침)
- 뉴스: Google 뉴스 RSS (API 키 불필요)
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
import warnings
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
import requests

warnings.filterwarnings("ignore")

import FinanceDataReader as fdr  # noqa: E402  (경고 억제 후 import)

KST = ZoneInfo("Asia/Seoul")
INDEX_SYMBOLS = {"KOSPI": "NAVER:KOSPI", "KOSDAQ": "NAVER:KOSDAQ"}
HISTORY_DAYS = 400  # 52주 지표 계산용(달력일 기준)
CLOSE_FIXED_AT = dt.time(15, 40)  # 정규장 종가(15:30) 확정 후 여유

# 뉴스가 아닌 블로그·유료 콘텐츠 출처
EXCLUDED_SOURCES = re.compile(r"블로그|Blog|프리미엄콘텐츠|머니스토리|티스토리|Tistory|카페", re.I)


def _start_date(days: int = HISTORY_DAYS) -> str:
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def drop_incomplete_today(df: pd.DataFrame, now: dt.datetime | None = None) -> pd.DataFrame:
    """종가가 확정되기 전(당일 15:40 이전)의 당일 행은 제외해 '전 거래일 종가 기준'을 지킴."""
    now = now or dt.datetime.now(KST)
    if len(df) and df.index[-1].date() == now.date() and now.time() < CLOSE_FIXED_AT:
        return df.iloc[:-1]
    return df


def fetch_stock_history(code: str, days: int = HISTORY_DAYS) -> pd.DataFrame:
    """일봉 OHLCV. 컬럼: Open, High, Low, Close, Volume, Change."""
    df = fdr.DataReader(f"NAVER:{code}", _start_date(days))
    if df is None or df.empty:
        raise ValueError(f"{code}: 시세 데이터 없음 (종목코드 확인 필요)")
    return drop_incomplete_today(df.sort_index())


def fetch_index_history(name: str, days: int = HISTORY_DAYS) -> pd.DataFrame:
    df = fdr.DataReader(INDEX_SYMBOLS[name], _start_date(days))
    if df is None or df.empty:
        raise ValueError(f"{name}: 지수 데이터 없음")
    return drop_incomplete_today(df.sort_index())


def _norm_title(title: str) -> str:
    return re.sub(r"\W", "", title)[:40]


def filter_news(entries: list[dict], limit: int, must_contain: str | None = None, seen: set | None = None) -> list[dict]:
    """블로그 출처 제외, 종목명이 제목에 없는 기사 제외, 중복 제거."""
    seen = seen if seen is not None else set()
    out = []
    for e in entries:
        title, source = e.get("title", ""), e.get("source", "")
        if EXCLUDED_SOURCES.search(source or ""):
            continue
        if must_contain and must_contain.lower() not in title.lower():
            continue
        key = _norm_title(title)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(e)
        if len(out) >= limit:
            break
    return out


def fetch_news(query: str, limit: int = 3, within_days: int = 2, must_contain: str | None = None, seen: set | None = None) -> list[dict]:
    """Google 뉴스 RSS 검색. within_days: 최근 며칠 기사."""
    q = urllib.parse.quote(f"{query} when:{within_days}d")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    entries = []
    for e in feed.entries[:30]:
        title = e.get("title", "")
        source = e.get("source", {}).get("title", "") if e.get("source") else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")]
        entries.append({"title": title, "link": e.get("link", ""), "source": source, "published": e.get("published", "")})
    return filter_news(entries, limit, must_contain, seen)


# ---------------------------------------------------------------------------
# 종목 지표·수급 (m.stock.naver.com), 해외 지수·환율 (api.stock.naver.com)
# ---------------------------------------------------------------------------
M_API = "https://m.stock.naver.com/api"
W_API = "https://api.stock.naver.com"
UA = {"User-Agent": "Mozilla/5.0"}

# 해외 지수: 표시 이름 -> 네이버 심볼
WORLD_INDICES = {"S&P500": ".INX", "NASDAQ": ".IXIC", "DOW": ".DJI", "필라델피아반도체": ".SOX"}


def _get_json(url: str):
    resp = requests.get(url, headers=UA, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_num(s) -> float | None:
    """'8.54배', '22,292원', '+858,557', '45.94%' -> 숫자. 값이 없거나 '-'면 None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group()) if m else None


def parse_eok(s) -> float | None:
    """'1,113조 1,314억' -> 11131314 (억원 단위)."""
    if not s:
        return None
    t = str(s).replace(",", "")
    jo = re.search(r"(\d+(?:\.\d+)?)조", t)
    eok = re.search(r"(\d+(?:\.\d+)?)억", t)
    if not jo and not eok:
        return None
    return (float(jo.group(1)) * 10000 if jo else 0) + (float(eok.group(1)) if eok else 0)


def fetch_stock_profile(code: str) -> dict:
    """주당 지표(EPS·BPS·DPS·추정EPS), 외국인 소진율, 시가총액, 증권사 컨센서스.

    네이버 화면의 PER·PBR은 조회 시점 가격 기준이라 쓰지 않고, 주당 지표만 받아
    KRX 종가로 다시 계산함(analyze.valuation).
    """
    d = _get_json(f"{M_API}/stock/{code}/integration")
    info = {t.get("code"): t.get("value") for t in d.get("totalInfos") or []}
    cons = d.get("consensusInfo") or {}
    return {
        "name": d.get("stockName"),
        "type": d.get("stockEndType"),  # stock / etf / etn ...
        "industry_code": d.get("industryCode"),
        "eps": parse_num(info.get("eps")),
        "bps": parse_num(info.get("bps")),
        "dps": parse_num(info.get("dividend")),
        "cns_eps": parse_num(info.get("cnsEps")),
        "foreign_rate_pct": parse_num(info.get("foreignRate")),
        "market_cap_eok": parse_eok(info.get("marketValue")),
        "consensus": {
            "date": cons.get("createDate"),
            "target_mean": parse_num(cons.get("priceTargetMean")),
            "recomm_mean": parse_num(cons.get("recommMean")),
        }
        if cons
        else None,
    }


def fetch_stock_flows(code: str, days: int = 20) -> list[dict]:
    """일별 투자자별 순매수 수량(주). 최신순."""
    rows = _get_json(f"{M_API}/stock/{code}/trend?pageSize={days}&page=1") or []
    out = []
    for r in rows:
        b = str(r.get("bizdate", ""))
        out.append(
            {
                "date": f"{b[:4]}-{b[4:6]}-{b[6:8]}" if len(b) == 8 else b,
                "foreign": parse_num(r.get("foreignerPureBuyQuant")),
                "institution": parse_num(r.get("organPureBuyQuant")),
                "individual": parse_num(r.get("individualPureBuyQuant")),
                "foreign_hold_pct": parse_num(r.get("foreignerHoldRatio")),
                "close": parse_num(r.get("closePrice")),
            }
        )
    return out


def fetch_market_flow(index: str) -> dict:
    """지수(KOSPI/KOSDAQ) 투자자별 순매수 금액(억원)과 프로그램 매매. 네이버가 주는 최근 1일."""
    d = _get_json(f"{M_API}/index/{index}/integration")
    deal, prog = d.get("dealTrendInfo") or {}, d.get("programTrendInfo") or {}
    b = str(deal.get("bizdate", ""))
    return {
        "date": f"{b[:4]}-{b[4:6]}-{b[6:8]}" if len(b) == 8 else b,
        "individual_eok": parse_num(deal.get("personalValue")),
        "foreign_eok": parse_num(deal.get("foreignValue")),
        "institution_eok": parse_num(deal.get("institutionalValue")),
        "program_total_eok": parse_num(prog.get("indexTotalReal")),
    }


def _series_summary(rows: list[dict], date_key: str = "localTradedAt") -> dict:
    """네이버 가격 목록(최신순) -> 최근값, 등락률, 약 1개월(21거래일) 변화율."""
    pts = [(str(r.get(date_key, ""))[:10], parse_num(r.get("closePrice")), parse_num(r.get("fluctuationsRatio"))) for r in rows]
    pts = [p for p in pts if p[1] is not None]
    if not pts:
        raise ValueError("가격 데이터 없음")
    last_date, last, chg = pts[0]
    out = {"date": last_date, "close": last, "chg_pct": chg, "prev_date": pts[1][0] if len(pts) > 1 else None}
    if len(pts) > 21 and pts[21][1]:
        out["ret_1m_pct"] = round((last / pts[21][1] - 1) * 100, 2)
    return out


def fetch_world_index(symbol: str) -> dict:
    return _series_summary(_get_json(f"{W_API}/index/{symbol}/price?page=1&pageSize=25"))


def fetch_usdkrw() -> dict:
    """원/달러 환율(하나은행 고시, 조회 시점의 최신값). 장중이면 당일 값이 들어옴."""
    return _series_summary(_get_json(f"{W_API}/marketindex/exchange/FX_USDKRW/prices?page=1&pageSize=25"))
