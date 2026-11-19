# -*- coding: utf-8 -*-
"""
검색 속도 측정 (색인이 이미 있으면 읽기만 함):
    python tests/bench_search.py                     # 지금 쓰는 색인 (프로그램 폴더의 mail_index.db)
    python tests/bench_search.py --data-dir demo/data
검색어별 걸린 시간(중앙값)과 결과 수만 출력하고, 메일 내용은 출력하지 않는다.
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mail_search as m  # noqa: E402

QUERIES = [
    ("검색어 없음 (전체 목록)", {"q": ""}),
    ("한 단어", {"q": "회의"}),
    ("두 단어 AND", {"q": "점검 결과"}),
    ("정확한 문구", {"q": '"작업 계획"'}),
    ("제외어 포함", {"q": "보고 -공지"}),
    ("제목에서만", {"q": "요청", "field": "subject"}),
    ("본문에서만", {"q": "서버", "field": "body"}),
    ("없는 단어", {"q": "존재하지않는검색어"}),
]


def main(runs=7):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not os.path.exists(m.DB_PATH):   # 색인이 아직 없으면(데모·CI) 먼저 만든다. 있으면 건드리지 않음
        m.init_db()
        m.run_index()
    f = m.facets()
    size = os.path.getsize(m.DB_PATH) / 1048576
    print(f"색인: 메일 {f['count']:,}통 (중복 제외 {f['unique']:,}통), DB {size:.0f}MB, Python {sys.version.split()[0]}")
    print(f"{'검색':<22}{'결과':>8}{'중앙값':>10}{'최대':>10}")
    for label, params in QUERIES:
        times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            r = m.search(params)
            times.append((time.perf_counter() - t0) * 1000)
        print(f"{label:<22}{r['total']:>8,}{statistics.median(times):>8.0f}ms{max(times):>8.0f}ms")


if __name__ == "__main__":
    main()
