---
description: 메일 백업 폴더에 새로 넣은(또는 바뀐) 월별 백업 zip 을 검색기 색인에 반영하고, 무엇이 반영됐는지 요약한 뒤 검색 화면을 열어준다. 사용자가 /동기화 를 입력하거나 "메일 백업 동기화/반영" 을 요청할 때 사용.
---

# /동기화 — 메일 백업 검색기 색인 반영

이 프로젝트 폴더의 `mail_search.py` 가 메일 백업 검색기다. 백업 폴더 경로는 `config.json` 의 `mail_root` 에 있다.
아래 순서대로 진행하고, 마지막에 한국어로 짧게 보고한다.

## 1. 검색 서버가 이미 켜져 있는지 확인

```bash
curl -s --max-time 3 http://127.0.0.1:8765/api/status
```

- **응답이 오면 (서버 실행 중)**: 같은 색인을 두 프로세스가 동시에 쓰지 않도록, 서버에게 색인을 맡긴다.
  ```bash
  curl -s -X POST -H "X-Requested-With: mailsearch" http://127.0.0.1:8765/api/reindex
  ```
  그다음 `/api/status` 를 몇 초 간격으로 확인해서 `status.running` 이 `false` 가 될 때까지 기다린다 (Monitor 의 until-loop 사용, 최대 10분).
  결과는 `status.by_month`, `status.added`, `status.done`, `status.removed`, `status.errors`, `facets.count`, `facets.months` 에서 읽는다.
- **응답이 없으면 (서버 꺼짐)**: 프로젝트 폴더에서 직접 색인한다. 새 파일이 많으면 몇 분 걸릴 수 있으니 timeout 을 넉넉히(600000ms) 준다.
  ```bash
  python -I mail_search.py --index-only
  ```
  출력은 JSON 이다: `changed_files`, `added_mails`, `removed_files`, `by_month`, `error_count`, `errors`, `total_mails`, `latest_month_folder`.

## 2. 이번 달 백업이 들어왔는지 확인

회사 규칙상 매달 1일~10일쯤 **전월** 메일 백업을 받는다. 오늘 날짜 기준 전월 폴더 이름은 `YYYYMM 메일` 형식이다 (예: 2026년 11월이면 `202610 메일`).

- 가장 최근 월 폴더(`latest_month_folder` 또는 `facets.months[0]`)가 전월보다 이전이면 "전월(YYYYMM) 백업이 아직 없습니다" 라고 알려준다.
- 백업 폴더 바로 아래에 월 폴더가 아닌 zip 이 새로 놓여 있으면(`by_month` 에 `(루트)` 가 있으면), 월 폴더(`YYYYMM 메일`)로 옮기는 게 좋겠다고 제안만 한다. **파일을 직접 옮기거나 지우지는 않는다.**

## 3. 검색 화면 열기

- 서버가 꺼져 있었다면 `preview_start` 로 `mail-search` 설정(.claude/launch.json)을 실행해 검색 화면을 연다.
- 이미 켜져 있었다면 `preview_start` 에 `url: http://localhost:8765/` 를 줘서 연다.

## 4. 보고 (짧게)

예시:

> 동기화 완료 — 202611 메일 zip 5개 반영, 새 메일 412통 (총 7,917통). 오류 0건.
> 검색 화면을 열었습니다.

- 바뀐 파일이 0개면: "새로 반영할 백업이 없습니다 (총 N통, 최근 폴더: YYYYMM 메일)".
- 오류가 있으면 오류 난 파일 이름을 최대 5개까지 보여준다.
- 삭제된 파일(`removed`)이 있으면 그 수도 알려준다 (백업 폴더에서 zip 을 지우면 색인에서도 빠진다).

## 하지 말 것

- 백업 폴더 안의 파일을 옮기거나, 압축을 풀거나, 지우지 않는다.
- `mail_index.db` 를 지우지 않는다 (전체 재색인이 필요하면 사용자에게 먼저 묻는다).
- 메일 내용을 외부 서비스로 보내지 않는다.
