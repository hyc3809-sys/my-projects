"""네트워크 없이 도는 계산·점검 로직 시험.  실행: .venv/bin/python -m unittest discover -s tests"""

import datetime as dt
import unittest

import numpy as np
import pandas as pd

from fundmanager import analyze, data
from fundmanager.build_report import normalize


def prices(closes, end="2026-11-06"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000.0}, index=idx)


class PriceMetrics(unittest.TestCase):
    def test_basic_fields(self):
        df = prices(np.linspace(100, 200, 300))
        m = analyze.price_metrics(df)
        self.assertEqual(m["close"], 200.0)
        self.assertAlmostEqual(m["chg_pct"], (200 / df["Close"].iloc[-2] - 1) * 100, places=2)
        self.assertTrue(m["above_ma20"])
        self.assertEqual(m["from_high52_pct"], 0.0)
        self.assertEqual(m["rsi14"], 100.0)  # 계속 오르기만 하면 RSI 100

    def test_rsi_range(self):
        rng = np.random.default_rng(0)
        df = prices(100 * np.cumprod(1 + rng.normal(0, 0.02, 300)))
        r = analyze.price_metrics(df)["rsi14"]
        self.assertTrue(0 <= r <= 100)


class Portfolio(unittest.TestCase):
    def setUp(self):
        self.h = {"A00001": prices([100.0] * 299 + [110.0]), "B00002": prices([50.0] * 299 + [45.0])}
        self.holdings = [
            {"code": "A00001", "name": "가", "qty": 10, "avg_price": 100},
            {"code": "B00002", "name": "나", "qty": 20, "avg_price": 60},
        ]

    def test_totals_and_weights(self):
        pos, s = analyze.portfolio(self.holdings, self.h, None, cash=1000)
        t = s["totals"]
        self.assertEqual(t["stock_value"], 10 * 110 + 20 * 45)
        self.assertEqual(t["cost"], 10 * 100 + 20 * 60)
        self.assertEqual(t["day_pnl"], 10 * 10 + 20 * -5)
        self.assertAlmostEqual(sum(p["weight_pct"] for p in pos) + t["cash_pct"], 100, places=1)

    def test_rules(self):
        pos, s = analyze.portfolio(self.holdings, self.h, None, cash=0)
        alerts = analyze.check_rules(pos, s["totals"], {"stop_loss_pct": 20, "take_profit_pct": 10, "max_weight_pct": 50})
        texts = " ".join(a["text"] for a in alerts)
        self.assertIn("손절 기준 도달", texts)   # 나: -25%
        self.assertIn("익절 기준 도달", texts)   # 가: +10%
        self.assertIn("비중 초과", texts)        # 가: 1100/2000 = 55%
        self.assertEqual(alerts[0]["level"], "high")

    def test_unset_rules_not_checked(self):
        pos, s = analyze.portfolio(self.holdings, self.h, None, cash=0)
        alerts = analyze.check_rules(pos, s["totals"], {})
        self.assertFalse([a for a in alerts if "기준" in a["text"]])


class Inputs(unittest.TestCase):
    def test_normalize(self):
        h, w, r, errs = normalize({
            "holdings": [{"code": "005930", "qty": "1,000", "avg_price": "70,000"}, {"code": "123", "qty": 1, "avg_price": 1},
                         {"code": "000660", "qty": 0, "avg_price": 1}],
            "watchlist": [{"code": "005930"}, {"code": "035420"}],
            "rules": {"cash": "5,000,000", "max_weight_pct": ""},
        })
        self.assertEqual([x["code"] for x in h], ["005930"])
        self.assertEqual(h[0]["qty"], 1000)
        self.assertEqual([x["code"] for x in w], ["035420"])  # 보유 종목은 관심 종목에서 제외
        self.assertEqual(r["cash"], 5_000_000)
        self.assertIsNone(r["max_weight_pct"])
        self.assertEqual(len(errs), 2)


class DataHelpers(unittest.TestCase):
    def test_drop_incomplete_today(self):
        df = prices([1.0, 2.0, 3.0], end="2026-11-09")
        morning = dt.datetime(2026, 11, 9, 7, 49, tzinfo=data.KST)
        evening = dt.datetime(2026, 11, 9, 16, 0, tzinfo=data.KST)
        self.assertEqual(len(data.drop_incomplete_today(df, morning)), 2)
        self.assertEqual(len(data.drop_incomplete_today(df, evening)), 3)

    def test_filter_news(self):
        entries = [
            {"title": "NAVER 실적 발표", "source": "연합뉴스"},
            {"title": "NAVER 실적 발표", "source": "뉴스1"},          # 중복
            {"title": "NAVER 관련 글", "source": "Naver Blog"},       # 블로그
            {"title": "SK하이닉스 하락", "source": "한국경제"},         # 종목명 없음
            {"title": "naver AI 투자", "source": "매일경제"},
        ]
        out = data.filter_news(entries, limit=5, must_contain="NAVER")
        self.assertEqual([e["source"] for e in out], ["연합뉴스", "매일경제"])


class NaverParsing(unittest.TestCase):
    def test_parse_num(self):
        self.assertEqual(data.parse_num("8.54배"), 8.54)
        self.assertEqual(data.parse_num("22,292원"), 22292)
        self.assertEqual(data.parse_num("+858,557"), 858557)
        self.assertEqual(data.parse_num("-5,772,197"), -5772197)
        self.assertEqual(data.parse_num("45.94%"), 45.94)
        self.assertIsNone(data.parse_num("-"))
        self.assertIsNone(data.parse_num(None))

    def test_parse_eok(self):
        self.assertEqual(data.parse_eok("1,113조 1,314억"), 11131314)
        self.assertEqual(data.parse_eok("3,759억"), 3759)
        self.assertIsNone(data.parse_eok(""))

    def test_series_summary(self):
        rows = [{"localTradedAt": f"2026-11-{30 - i:02d}T16:00:00-05:00", "closePrice": f"{110 - i:,}", "fluctuationsRatio": "1.0"} for i in range(25)]
        s = data._series_summary(rows)
        self.assertEqual(s["date"], "2026-11-30")
        self.assertEqual(s["close"], 110)
        self.assertAlmostEqual(s["ret_1m_pct"], (110 / 89 - 1) * 100, places=2)


class ValuationAndFlow(unittest.TestCase):
    def test_valuation_uses_close(self):
        v = analyze.valuation({"type": "stock", "eps": 20000, "bps": 100000, "dps": 2000, "cns_eps": 40000,
                               "consensus": {"target_mean": 300000, "date": "2026-11-06"}}, 200000)
        self.assertEqual(v["per"], 10.0)
        self.assertEqual(v["fwd_per"], 5.0)
        self.assertEqual(v["pbr"], 2.0)
        self.assertEqual(v["div_yield_pct"], 1.0)
        self.assertEqual(v["consensus_gap_pct"], 50.0)

    def test_loss_making(self):
        v = analyze.valuation({"type": "stock", "eps": -500, "bps": 1000}, 2000)
        self.assertIsNone(v["per"])
        self.assertTrue(v["loss_making"])

    def test_flow_summary(self):
        rows = [  # 최신순, 첫 행은 장중(as_of 이후) → 제외돼야 함
            {"date": "2026-11-09", "foreign": 999, "institution": 0, "individual": 0, "foreign_hold_pct": 50.0, "close": 100},
        ] + [
            {"date": f"2026-11-0{6 - i}", "foreign": -1000, "institution": 500 if i < 2 else -10, "individual": 500,
             "foreign_hold_pct": 45.0 + i * 0.1, "close": 100_000}
            for i in range(5)
        ]
        f = analyze.flow_summary(rows, "2026-11-06")
        self.assertEqual(f["date"], "2026-11-06")
        self.assertEqual(f["foreign_1d"], -1000)
        self.assertEqual(f["foreign_5d"], -5000)
        self.assertEqual(f["foreign_5d_eok"], -5.0)  # 5 x (-1000주 x 10만원) = -5억
        self.assertEqual(f["foreign_streak"], -5)
        self.assertEqual(f["institution_streak"], 2)
        self.assertEqual(f["foreign_hold_chg_5d_pp"], -0.4)
        alerts = analyze.flow_alerts({"code": "X", "name": "X", "flow": f, "valuation": None})
        self.assertIn("외국인 5일 연속 순매도", alerts[0]["text"])


if __name__ == "__main__":
    unittest.main()
