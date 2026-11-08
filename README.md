# 주식투자의 성공을 위한 성공파트너

국내 주식 포트폴리오를 점검하고 평일 아침 리포트를 만드는 개인용 펀드매니저 도구.

- 보유·관심 종목과 투자 원칙은 비공개 포트폴리오 페이지에서 입력함 (이 저장소에는 보관하지 않음)
- 평일 오전 8시 전에 예약 작업이 시세·지수·뉴스를 모아 지표를 계산하고, Claude가 코멘트를 붙여 페이지에 올림
- 분석·제안 도구이며 주문 기능은 없음

## 구성

| 경로 | 내용 |
| --- | --- |
| `fundmanager/data.py` | 시세·지수(네이버 금융, FinanceDataReader), 뉴스(Google 뉴스 RSS) 수집 |
| `fundmanager/analyze.py` | 수익률·비중·변동성·MDD·베타·RSI 계산, 투자 원칙 점검 |
| `fundmanager/build_report.py` | 리포트 JSON 생성 |
| `web/portfolio.html` | 포트폴리오 페이지 소스 |
| `docs/morning-report.md` | 아침 예약 작업 실행 절차 |
| `tests/` | 계산·점검 로직 시험 |

## 직접 실행

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m fundmanager.build_report --input input.json --out report.json
```

시험(네트워크 불필요): `.venv/bin/python -m unittest discover -s tests -t .`

`input.json` 형식: `{"holdings":[{"code":"005930","name":"삼성전자","qty":10,"avg_price":70000}], "watchlist":[], "rules":{"cash":0}}`
