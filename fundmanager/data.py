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
