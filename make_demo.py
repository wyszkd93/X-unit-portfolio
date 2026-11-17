# -*- coding: utf-8 -*-
"""
데모용 가짜 메일 백업 만들기:  python make_demo.py
→ demo/backup/YYYYMM 메일/받은메일함_1.zip, 보낸메일함.zip, 외부메일함.zip  (모두 가상의 사람·회사·내용)
→ demo/data/config.json  (백업 폴더 = demo/backup, 포트 8790)

실행:  python mail_search.py --data-dir demo/data
스크린샷·시연용이며, 실제 메일은 하나도 쓰지 않는다.
"""
import io
import json
import os
import random
import shutil
import zipfile
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "demo")
CS = "ks_c_5601-1987"          # 사내 메일 시스템이 흔히 쓰는 한글 인코딩을 그대로 재현
KST = timezone(timedelta(hours=9))
DOMAIN = "example.com"

ME = ("김하늘", "인프라운영팀", "sky.kim")
PEOPLE = [
    ("이도윤", "DB운영팀", "doyun.lee"), ("박서연", "보안관제팀", "seoyeon.park"),
    ("최민준", "네트워크팀", "minjun.choi"), ("정예린", "개발1팀", "yerin.jung"),
    ("한지호", "서비스기획팀", "jiho.han"), ("오수아", "인프라운영팀", "sua.oh"),
    ("윤태경", "개발2팀", "taekyung.yoon"), ("강다은", "품질관리팀", "daeun.kang"),
]
EXTERNAL = [("클라우드파트너 기술지원", "support", "cloudpartner.example"),
            ("모니터링솔루션 고객센터", "help", "monitor-sol.example"),
            ("보안교육센터", "noreply", "secu-edu.example")]

TOPICS = [
    ("[작업공지] {d} 야간 DB 서버 패치 작업 안내", "DB 서버 OS 보안 패치를 진행합니다.\n작업 시간: {d} 22:00 ~ 02:00\n영향 범위: 주문 조회 서비스 일시 지연\n\n문의는 DB운영팀으로 부탁드립니다.", ["작업계획서_DB패치.pdf"]),
    ("[요청] 방화벽 정책 오픈 요청 ({n}건)", "신규 API 서버 연동을 위해 아래 정책 오픈을 요청드립니다.\n- 출발지: 10.10.{n}.0/24\n- 목적지: API 게이트웨이 443\n\n정책 신청서 첨부합니다.", ["방화벽_정책신청서.xlsx"]),
    ("RE: 테이블스페이스 사용률 경고 확인 부탁드립니다", "확인했습니다. 테이블스페이스 사용률이 87% 라서 이번 주 안에 데이터파일을 추가하겠습니다.\n추가 후 다시 공유드리겠습니다.", []),
    ("[장애보고] {d} 배치 서버 디스크 풀 장애 경위", "1. 현상: 정산 배치 지연\n2. 원인: 로그 로테이션 누락으로 /var 디스크 100%\n3. 조치: 로그 정리 및 로테이션 설정 보완\n4. 재발 방지: 디스크 사용률 80% 경보 추가", ["장애보고서.docx"]),
    ("[공유] {m}월 서버 자원 사용률 보고", "{m}월 서버 자원 사용률 보고서 공유드립니다.\nCPU 평균 41%, 메모리 평균 63%로 안정적입니다.", ["자원사용률_보고.xlsx", "추이그래프.png"]),
    ("인증서 만료 예정 안내 (D-{n})", "웹 서버 SSL 인증서가 {n}일 뒤 만료됩니다.\n갱신 일정 협의 부탁드립니다.", []),
    ("[회의록] 클라우드 전환 주간 회의", "참석: 인프라운영팀, 개발1팀, 보안관제팀\n- 2단계 전환 대상 서버 12대 확정\n- 백업 정책 \"PM 작업\" 시간대로 조정\n- 다음 회의: 다음 주 화요일", ["회의록.docx"]),
    ("RE: 모니터링 대시보드 접근 권한 요청", "요청하신 대시보드 조회 권한 추가했습니다. 다시 로그인해 보시고 안 되면 말씀 주세요.", []),
    ("[점검] 분기 백업 복구 훈련 결과", "분기 백업 복구 훈련을 완료했습니다.\n복구 목표 시간(RTO) 4시간 이내 달성.\n세부 결과는 첨부 문서를 참고해 주세요.", ["복구훈련_결과.pdf"]),
    ("배포 일정 확인 부탁드립니다", "다음 배포 일정이 {d} 로 잡혀 있는데 서버 작업과 겹치지 않는지 확인 부탁드립니다.", []),
]
EXT_TOPICS = [
    ("[기술지원] 문의하신 로드밸런서 설정 답변드립니다", "문의하신 세션 유지(sticky session) 설정 방법 안내드립니다.\n콘솔 > 대상 그룹 > 속성에서 활성화할 수 있습니다.", []),
    ("[공지] 정기 점검 안내", "서비스 품질 향상을 위한 정기 점검이 예정되어 있습니다.\n점검 중에는 콘솔 접속이 제한됩니다.", []),
    ("[안내] 정보보호 교육 수료증", "정보보호 교육을 수료하셨습니다. 수료증을 첨부합니다.", ["수료증.pdf"]),
]


def addr(name, team, user, domain=DOMAIN):
    disp = f"{name}/{team}" if team else name
    return f"{Header(disp, CS).encode()} <{user}@{domain}>"


def fake_file(name):
    rnd = random.Random(name)
    ext = name.rsplit(".", 1)[-1]
    head = {"pdf": b"%PDF-1.4\n% demo\n", "png": b"\x89PNG\r\n\x1a\n"}.get(ext, b"PK\x03\x04demo")
    return head + bytes(rnd.getrandbits(8) for _ in range(rnd.randint(2000, 9000)))


def make_mail(sender, to, cc, subject, body, files, when, html=False, mid=None):
    msg = MIMEMultipart() if files else None
    text = MIMEText(f"<div style='font-family:Malgun Gothic'>{body.replace(chr(10), '<br>')}</div>" if html else body,
                    "html" if html else "plain", CS)
    if msg is None:
        msg = text
    else:
        msg.attach(text)
        for f in files:
            part = MIMEApplication(fake_file(f))
            part.add_header("Content-Disposition", "attachment", filename=(CS, "", f))
            msg.attach(part)
    msg["Subject"] = Header(subject, CS).encode()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Date"] = format_datetime(when)
    msg["Message-ID"] = mid or f"<{random.getrandbits(64):x}@{DOMAIN}>"
    return msg.as_bytes()


def eml_name(subject, when):
    ap = "오전" if when.hour < 12 else "오후"
    safe = "".join("_" if c in '\\/:*?"<>|' else c for c in subject)[:60]
    return f"{safe}[{when:%Y-%m-%d} {ap} {when.hour % 12 or 12}_{when:%M}].eml"


def month_mails(y, m, rnd):
    inbox, sent, ext = [], [], []
    me = addr(*ME)
    for i in range(rnd.randint(26, 34)):
        when = datetime(y, m, rnd.randint(1, 28), rnd.randint(8, 19), rnd.randint(0, 59), tzinfo=KST)
        subj, body, files = rnd.choice(TOPICS)
        fill = {"d": f"{when:%m/%d}", "n": rnd.randint(2, 30), "m": m}
        subj, body = subj.format(**fill), body.format(**fill)
        p = rnd.choice(PEOPLE)
        others = rnd.sample([x for x in PEOPLE if x != p], rnd.randint(0, 5))
        greeting = f"안녕하세요. {p[1]} {p[0]}입니다.\n\n" if rnd.random() < .5 else ""
        if rnd.random() < .25:          # 내가 보낸 메일
            raw = make_mail(me, [addr(*p)], [addr(*o) for o in others[:2]], subj,
                            f"안녕하세요. {ME[1]} {ME[0]}입니다.\n\n{body}", files, when, html=rnd.random() < .5)
            sent.append((eml_name(subj, when), raw))
        else:
            raw = make_mail(addr(*p), [me] + [addr(*o) for o in others], [], subj, greeting + body,
                            files, when, html=rnd.random() < .5)
            inbox.append((eml_name(subj, when), raw))
    for i in range(rnd.randint(3, 6)):
        when = datetime(y, m, rnd.randint(1, 28), rnd.randint(8, 19), rnd.randint(0, 59), tzinfo=KST)
        subj, body, files = rnd.choice(EXT_TOPICS)
        e = rnd.choice(EXTERNAL)
        raw = make_mail(addr(e[0], "", e[1], e[2]), [me], [], subj, body, files, when, html=True)
        ext.append((eml_name(subj, when), raw))
    return inbox, sent, ext


def write_zip(path, items, nested=False):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if nested:   # 실제 백업처럼 zip 안에 zip 이 들어 있는 경우도 재현
            half = len(items) // 2
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as inner:
                for n, raw in items[half:]:
                    inner.writestr(n, raw)
            z.writestr("받은메일함_추가분.zip", buf.getvalue())
            items = items[:half]
        for n, raw in items:
            z.writestr(n, raw)


def main():
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    rnd = random.Random(20261117)
    backup = os.path.join(DEMO, "backup")
    data = os.path.join(DEMO, "data")
    shutil.rmtree(DEMO, ignore_errors=True)
    os.makedirs(data)
    total = 0
    for y, m in [(2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]:
        d = os.path.join(backup, f"{y}{m:02d} 메일")
        os.makedirs(d)
        inbox, sent, ext = month_mails(y, m, rnd)
        write_zip(os.path.join(d, "받은메일함_1.zip"), inbox, nested=(m == 9))
        write_zip(os.path.join(d, "보낸메일함.zip"), sent)
        write_zip(os.path.join(d, "외부메일함.zip"), ext)
        total += len(inbox) + len(sent) + len(ext)
    with open(os.path.join(data, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"mail_root": backup, "port": 8790, "open_browser": False}, f, ensure_ascii=False, indent=2)
    print(f"가짜 메일 {total}통 → {backup}")
    print("실행: python mail_search.py --data-dir demo/data   (http://127.0.0.1:8790)")


if __name__ == "__main__":
    main()
