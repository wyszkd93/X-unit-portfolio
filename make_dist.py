# -*- coding: utf-8 -*-
"""
배포용 zip 만들기:  python make_dist.py
→ 배포\메일백업검색기_<APP_VERSION>.zip  (예: _V2)
   새 버전을 낼 때는 mail_search.py 의 APP_VERSION 을 올린다 (같은 이름이 있으면 덮어쓰지 않고 멈춤).
   개인 파일 mail_index.db / config.json / .claude 는 절대 포함하지 않음.
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "배포")
NAME = "메일백업검색기"

# 넣을 파일만 명시 (허용 목록 방식)
FILES = [
    "mail_search.py",
    "web/index.html",
    "메일검색.bat",
    "바탕화면 바로가기 만들기.bat",
    "자동 실행 켜기.bat",
    "자동 실행 끄기.bat",
    "사용법.txt",
    "icon.ico",
]
# 설치 없이 쓰는 Python (python.org embeddable package) — 폴더째 포함
BUNDLED_DIRS = ["python"]
# .bat 은 영문(ASCII) + CRLF 만 써야 어떤 PC(콘솔 코드페이지 949 / UTF-8)에서도 cmd 가 제대로 읽는다.
# 한글 안내는 PowerShell -EncodedCommand 로 띄운다.  →  파일 그대로 복사하고 검사만 한다.


def app_version():
    import re
    with open(os.path.join(HERE, "mail_search.py"), encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        sys.exit("mail_search.py 에서 APP_VERSION 을 찾을 수 없습니다")
    return m.group(1)


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{NAME}_{app_version()}.zip")  # 예: 메일백업검색기_V2.zip
    if os.path.exists(out) and "--overwrite" not in sys.argv:
        sys.exit(f"이미 있습니다: {out}\n  mail_search.py 의 APP_VERSION 을 올리거나, 덮어쓰려면 --overwrite 를 붙이세요.")
    for d in BUNDLED_DIRS:
        if not os.path.isfile(os.path.join(HERE, d, "python.exe")):
            sys.exit(f"{d}\\python.exe 가 없습니다 (python.org embeddable package 를 {d} 폴더에 풀어 주세요)")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in BUNDLED_DIRS:
            for dp, dn, fn in os.walk(os.path.join(HERE, d)):
                dn[:] = [x for x in dn if x != "__pycache__"]
                for f in sorted(fn):
                    full = os.path.join(dp, f)
                    rel = os.path.relpath(full, HERE).replace(os.sep, "/")
                    z.write(full, f"{NAME}/{rel}")
        for rel in FILES:
            src = os.path.join(HERE, *rel.split("/"))
            with open(src, "rb") as f:
                data = f.read()
            if rel.endswith(".bat"):
                if not data.isascii():
                    sys.exit(f"{rel} 에 한글 등 비ASCII 문자가 있습니다 (cmd 에서 깨짐)")
                if b"\r\n" not in data:
                    sys.exit(f"{rel} 의 줄바꿈이 CRLF 가 아닙니다")
            z.writestr(f"{NAME}/{rel}", data)
    # 개인 파일이 섞이지 않았는지 확인
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    bad = [n for n in names if n.endswith((".db", "config.json")) or "/.claude" in n]
    if bad:
        os.remove(out)
        sys.exit(f"개인 파일이 포함되어 중단: {bad}")
    print(out, f"({os.path.getsize(out) / 1048576:.1f} MB, 파일 {len(names)}개)")
    for n in names:
        if "/python/" not in n:
            print("  ", n)
    print("   + python/ (설치 없이 쓰는 Python)", sum("/python/" in n for n in names), "개 파일")


if __name__ == "__main__":
    main()
