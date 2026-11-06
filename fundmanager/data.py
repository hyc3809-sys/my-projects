"""시세·지수·뉴스 수집.

- 시세·지수: FinanceDataReader의 네이버 금융 소스 (KRX 직접 조회는 로그인이 필요해 사용하지 않음)
- 뉴스: Google 뉴스 RSS (API 키 불필요)
"""

from __future__ import annotations

import datetime as dt
import urllib.parse
import warnings

import feedparser
import pandas as pd
import requests

warnings.filterwarnings("ignore")

import FinanceDataReader as fdr  # noqa: E402  (경고 억제 후 import)

INDEX_SYMBOLS = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}
HISTORY_DAYS = 400  # 52주 지표 계산용(달력일 기준)


def _start_date(days: int = HISTORY_DAYS) -> str:
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def fetch_stock_history(code: str, days: int = HISTORY_DAYS) -> pd.DataFrame:
    """일봉 OHLCV. 컬럼: Open, High, Low, Close, Volume, Change."""
    df = fdr.DataReader(f"NAVER:{code}", _start_date(days))
    if df is None or df.empty:
        raise ValueError(f"{code}: 시세 데이터 없음 (종목코드 확인 필요)")
    return df.sort_index()


def fetch_index_history(name: str, days: int = HISTORY_DAYS) -> pd.DataFrame:
    df = fdr.DataReader(INDEX_SYMBOLS[name], _start_date(days))
    if df is None or df.empty:
        raise ValueError(f"{name}: 지수 데이터 없음")
    return df.sort_index()


def fetch_news(query: str, limit: int = 3, within: str = "2d") -> list[dict]:
    """Google 뉴스 RSS 검색. within: '1d', '2d', '7d' 등."""
    q = urllib.parse.quote(f"{query} when:{within}")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for e in feed.entries[:limit]:
        title = e.get("title", "")
        source = e.get("source", {}).get("title", "") if e.get("source") else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")]
        items.append(
            {
                "title": title,
                "link": e.get("link", ""),
                "source": source,
                "published": e.get("published", ""),
            }
        )
    return items
