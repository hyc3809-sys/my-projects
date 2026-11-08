"""아침 리포트 데이터 생성 (해설 문구는 Claude가 따로 채움).

사용법
  python -m fundmanager.build_report --db-dir <ArtifactData out_dir> --out report.json
  python -m fundmanager.build_report --input input.json --out report.json

--db-dir 구조 (ArtifactData list ... out_dir 결과)
  <db-dir>/holdings/<code>.json, <db-dir>/watchlist/<code>.json, <db-dir>/config/rules.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

from . import analyze, data

KST = ZoneInfo("Asia/Seoul")
CODE_RE = re.compile(r"^[0-9A-Z]{6}$")


def _doc_body(obj: dict) -> dict:
    """ArtifactData 파일이 메타데이터로 감싸져 있으면 본문만 꺼냄."""
    if isinstance(obj, dict) and isinstance(obj.get("data"), dict) and "code" not in obj:
        return obj["data"]
    return obj


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def load_db_dir(path: pathlib.Path) -> dict:
    def read_coll(name):
        d = path / name
        return [_doc_body(json.loads(f.read_text())) for f in sorted(d.glob("*.json"))] if d.is_dir() else []

    rules_file = path / "config" / "rules.json"
    rules = _doc_body(json.loads(rules_file.read_text())) if rules_file.exists() else {}
    return {"holdings": read_coll("holdings"), "watchlist": read_coll("watchlist"), "rules": rules}


def normalize(inp: dict) -> tuple[list[dict], list[dict], dict, list[str]]:
    errors = []
    holdings = []
    for h in inp.get("holdings", []):
        code = str(h.get("code", "")).strip().upper()
        qty, avg = _num(h.get("qty")), _num(h.get("avg_price"))
        if not CODE_RE.match(code):
            errors.append(f"보유 종목 코드 형식 오류: '{code}' (6자리)")
            continue
        if not qty or qty <= 0 or avg is None or avg <= 0:
            errors.append(f"{code}: 수량 또는 평단가가 비어 있거나 0 이하")
            continue
        holdings.append({"code": code, "name": h.get("name", ""), "qty": qty, "avg_price": avg, "memo": h.get("memo", "")})
    watch = []
    held = {h["code"] for h in holdings}
    for w in inp.get("watchlist", []):
        code = str(w.get("code", "")).strip().upper()
        if CODE_RE.match(code) and code not in held:
            watch.append({"code": code, "name": w.get("name", ""), "memo": w.get("memo", "")})
    raw = inp.get("rules", {}) or {}
    rules = {k: _num(raw.get(k)) for k in ("max_weight_pct", "stop_loss_pct", "take_profit_pct", "min_cash_pct", "cash")}
    return holdings, watch, rules, errors


def build(inp: dict) -> dict:
    holdings, watch, rules, errors = normalize(inp)
    now = dt.datetime.now(KST)

    market = {}
    index_hist = {}
    for name in data.INDEX_SYMBOLS:
        try:
            index_hist[name] = data.fetch_index_history(name)
            market[name] = analyze.price_metrics(index_hist[name])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name} 지수 조회 실패: {type(e).__name__}: {e}")

    histories = {}
    for item in holdings + watch:
        try:
            histories[item["code"]] = data.fetch_stock_history(item["code"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{item['code']} 시세 조회 실패: {type(e).__name__}: {e}")

    positions, summary = analyze.portfolio(holdings, histories, index_hist.get("KOSPI"), rules.get("cash") or 0)
    alerts = analyze.check_rules(positions, summary["totals"], rules)

    watch_rows = []
    for w in watch:
        df = histories.get(w["code"])
        row = {**w, "name": w["name"] or w["code"]}
        if df is None:
            row["error"] = "시세 조회 실패"
        else:
            row.update(analyze.price_metrics(df))
        watch_rows.append(row)

    as_of = market.get("KOSPI", {}).get("date") or max((p.get("date", "") for p in positions), default="")
    # 뉴스 기간: 직전 거래일부터 오늘까지(월요일이면 금요일 기사 포함), 2~7일
    try:
        gap = (now.date() - dt.date.fromisoformat(as_of)).days
    except ValueError:
        gap = 1
    within = min(max(gap + 1, 2), 7)

    news, seen = [], set()
    targets = [("코스피 증시", "시장", None)]
    for x in holdings + watch:
        name = x["name"] if x["name"] and x["name"] != x["code"] else ""
        targets.append((f'"{name}"' if name else x["code"], name or x["code"], name or None))
    for q, label, must in targets:
        try:
            for n in data.fetch_news(q, limit=3, within_days=within, must_contain=must, seen=seen):
                news.append({"topic": label, **n})
        except Exception as e:  # noqa: BLE001
            errors.append(f"뉴스 조회 실패({label}): {type(e).__name__}")
    return {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(timespec="minutes"),
        "as_of": as_of,
        "market": market,
        "totals": summary["totals"],
        "risk": summary["risk"],
        "rules": rules,
        "positions": positions,
        "watch": watch_rows,
        "alerts": alerts,
        "news": news,
        "news_within_days": within,
        "commentary": "",
        "errors": errors,
        "sources": f"시세·지수: 네이버 금융(FinanceDataReader), 뉴스: Google 뉴스 RSS 최근 {within}일. 전 거래일 종가 기준.",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--db-dir", type=pathlib.Path)
    src.add_argument("--input", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    inp = load_db_dir(a.db_dir) if a.db_dir else json.loads(a.input.read_text())
    report = build(inp)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    t = report["totals"]
    print(f"as_of={report['as_of']} 종목={t['count']} 평가={t['stock_value']} 손익%={t['pnl_pct']} 경고={len(report['alerts'])} 오류={len(report['errors'])}")
    for e in report["errors"]:
        print("  오류:", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
