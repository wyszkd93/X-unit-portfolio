# -*- coding: utf-8 -*-
"""
메일 백업 검색기
- 월별 백업 폴더의 zip(.eml 포함, zip 안의 zip 포함)과 풀린 .eml 을 압축 해제 없이 색인
- 브라우저(http://127.0.0.1:8765)에서 검색 / 본문 미리보기 / 첨부 저장 / 원본 .eml 저장
- 외부 라이브러리 없음 (Python 3.9+ 표준 라이브러리만 사용)
"""
import email
import email.header
import email.utils
import html
import io
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_VERSION = "V4"   # 배포 zip 이름(메일백업검색기_V4.zip)에도 쓰인다
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _arg(name):
    """--name VALUE 형식의 실행 인자"""
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


# 설정·색인 파일 위치. 기본은 프로그램 폴더. --data-dir 또는 환경변수 MAIL_SEARCH_DATA_DIR 로 바꿀 수 있다 (데모/테스트용)
DATA_DIR = os.path.abspath(_arg("--data-dir") or os.environ.get("MAIL_SEARCH_DATA_DIR") or APP_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "mail_index.db")
WEB_DIR = os.path.join(APP_DIR, "web")
DEFAULT_CONFIG = {
    "mail_root": "",          # 비어 있으면 처음 실행 때 화면에서 백업 폴더를 고른다
    "port": 8765,
    "open_browser": True,
}

if sys.version_info < (3, 9):
    print("Python 3.9 이상이 필요합니다. 현재:", sys.version.split()[0])
    sys.exit(1)
INDEX_VERSION = "2"
BODY_LIMIT = 200_000

# ---------------------------------------------------------------- 설정

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                cfg.update(json.load(f))
        except Exception as ex:
            print("[경고] config.json 을 읽지 못해 기본값 사용:", ex)
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


CONFIG = load_config()


def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=2)


def root_ok():
    r = CONFIG.get("mail_root") or ""
    return bool(r) and os.path.isdir(r)


def count_backup_files(path, limit=100000):
    zips = emls = 0
    for dp, dn, fn in os.walk(path):
        for f in fn:
            low = f.lower()
            if low.endswith(".zip"):
                zips += 1
            elif low.endswith(".eml"):
                emls += 1
            if zips + emls >= limit:
                return zips, emls
    return zips, emls


PICK_LOCK = threading.Lock()


_PS_PICK = r"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'; $owner.Show(); $owner.Activate()
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = '메일 백업 zip 을 모아 두는 폴더를 고르세요'
$d.ShowNewFolderButton = $false
if ($env:MS_INIT -and (Test-Path -LiteralPath $env:MS_INIT)) { $d.SelectedPath = $env:MS_INIT }
$r = $d.ShowDialog($owner)
$owner.Close()
if ($r -eq 'OK') { $b = [Text.Encoding]::UTF8.GetBytes($d.SelectedPath); [Console]::OpenStandardOutput().Write($b, 0, $b.Length) }
"""


def _pick_folder_powershell():
    import base64
    import subprocess
    env = dict(os.environ, MS_INIT=CONFIG.get("mail_root") or "")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", base64.b64encode(_PS_PICK.encode("utf-16-le")).decode()],
        capture_output=True, timeout=900, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
    p = r.stdout.decode("utf-8", "replace").strip()
    return os.path.normpath(p) if p else ""


def pick_folder():
    """이 PC 에 폴더 선택 창을 띄운다. 취소하면 "", 띄울 수 없으면 None"""
    if not PICK_LOCK.acquire(blocking=False):
        return None
    try:
        if os.name == "nt":
            try:
                return _pick_folder_powershell()
            except Exception as ex:
                print("[경고] 폴더 선택 창(PowerShell) 실패:", ex)
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        init = CONFIG.get("mail_root") if root_ok() else os.path.expanduser("~")
        p = filedialog.askdirectory(parent=root, title="메일 백업 폴더 선택", initialdir=init, mustexist=True)
        root.destroy()
        return os.path.normpath(p) if p else ""
    except Exception:
        return None
    finally:
        PICK_LOCK.release()

# ---------------------------------------------------------------- 백업 가져오기
# 새로 받은 백업 zip(또는 eml)을 골라 → 백업 폴더\YYYYMM 메일\ 로 "복사" → 색인.
# 원본은 지우지 않는다. 서버는 자기 파일 선택 창이 돌려준 경로만 받아들인다.

_PS_PICK_FILES = r"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'; $owner.Show(); $owner.Activate()
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '가져올 메일 백업 파일을 고르세요 (여러 개 선택 가능)'
$d.Filter = '메일 백업 (*.zip;*.eml)|*.zip;*.eml|모든 파일 (*.*)|*.*'
$d.Multiselect = $true
if ($env:MS_INIT -and (Test-Path -LiteralPath $env:MS_INIT)) { $d.InitialDirectory = $env:MS_INIT }
$r = $d.ShowDialog($owner)
$owner.Close()
if ($r -eq 'OK') { $b = [Text.Encoding]::UTF8.GetBytes(($d.FileNames -join "`n")); [Console]::OpenStandardOutput().Write($b, 0, $b.Length) }
"""

PICKED_FILES = set()   # 파일 선택 창에서 실제로 고른 경로만 가져오기 허용
MONTH_RE = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])$")


def downloads_dir():
    p = os.path.join(os.path.expanduser("~"), "Downloads")
    return p if os.path.isdir(p) else os.path.expanduser("~")


def pick_files():
    """파일 선택 창(여러 개). 취소하면 [], 띄울 수 없으면 None"""
    if not PICK_LOCK.acquire(blocking=False):
        return None
    try:
        if os.name == "nt":
            try:
                import base64
                import subprocess
                env = dict(os.environ, MS_INIT=downloads_dir())
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
                     "-EncodedCommand", base64.b64encode(_PS_PICK_FILES.encode("utf-16-le")).decode()],
                    capture_output=True, timeout=900, env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode != 0:
                    raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
                out = r.stdout.decode("utf-8", "replace").strip()
                return [os.path.normpath(x.strip()) for x in out.split("\n") if x.strip()]
            except Exception as ex:
                print("[경고] 파일 선택 창(PowerShell) 실패:", ex)
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        ps = filedialog.askopenfilenames(parent=root, title="가져올 메일 백업 파일", initialdir=downloads_dir(),
                                         filetypes=[("메일 백업", "*.zip *.eml"), ("모든 파일", "*.*")])
        root.destroy()
        return [os.path.normpath(p) for p in ps]
    except Exception:
        return None
    finally:
        PICK_LOCK.release()


def _prev_month():
    t = time.localtime()
    y, m = (t.tm_year, t.tm_mon - 1) if t.tm_mon > 1 else (t.tm_year - 1, 12)
    return f"{y}{m:02d}"


def detect_month(paths, sample=300):
    """파일 안 메일 날짜(헤더만 읽음)로 가장 많은 연월을 찾는다. 못 찾으면 지난달"""
    from collections import Counter
    from email.parser import BytesHeaderParser
    cnt = Counter()
    hp = BytesHeaderParser()

    def take(raw, name):
        try:
            d = mail_date(hp.parsebytes(raw), name)
            if d:
                cnt[d[:4] + d[5:7]] += 1
        except Exception:
            pass

    for p in paths:
        seen = 0
        try:
            if p.lower().endswith(".eml"):
                with open(p, "rb") as f:
                    take(f.read(65536), os.path.basename(p))
                continue
            with zipfile.ZipFile(p) as zf:
                for info in zf.infolist():
                    if seen >= sample:
                        break
                    if info.filename.lower().endswith(".eml"):
                        with zf.open(info) as f:
                            take(f.read(65536), zip_name(info))
                        seen += 1
        except Exception:
            pass
    return (cnt.most_common(1)[0][0], dict(cnt)) if cnt else (_prev_month(), {})


def _same_file(a, b):
    import hashlib
    if os.path.getsize(a) != os.path.getsize(b):
        return False

    def h(p):
        x = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                x.update(chunk)
        return x.digest()
    return h(a) == h(b)


def _inside(p, root):
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(p)),
                                   os.path.normcase(os.path.abspath(root))]) == os.path.normcase(os.path.abspath(root))
    except ValueError:
        return False


def inspect_import(paths):
    files = []
    for p in paths:
        try:
            files.append({"path": p, "name": os.path.basename(p), "size": os.path.getsize(p),
                          "box": box_of(p), "inside": root_ok() and _inside(p, CONFIG["mail_root"])})
        except OSError:
            files.append({"path": p, "name": os.path.basename(p), "error": "파일을 찾을 수 없습니다"})
    month, counts = detect_month([f["path"] for f in files if "error" not in f])
    return {"files": files, "month": month, "month_counts": counts, "root": CONFIG.get("mail_root") or ""}


def do_import(paths, month, reindex=True):
    """골라 둔 파일을 백업폴더\\{month} 메일\\ 로 복사. 같은 내용이면 건너뛰고, 이름만 같으면 (2) 를 붙인다"""
    import shutil
    if not root_ok():
        return {"ok": False, "error": "먼저 메일 백업 폴더를 설정해 주세요 (톱니바퀴)."}
    if not MONTH_RE.match(month or ""):
        return {"ok": False, "error": "연월은 6자리 숫자로 입력해 주세요. 예) 202610"}
    if not paths:
        return {"ok": False, "error": "가져올 파일을 골라 주세요."}
    bad = [p for p in paths if p not in PICKED_FILES]
    if bad:
        return {"ok": False, "error": "[파일 고르기] 로 다시 골라 주세요."}
    if INDEX_LOCK.locked():
        return {"ok": False, "error": "지금 반영 중입니다. 끝난 뒤 다시 시도해 주세요."}
    root = CONFIG["mail_root"]
    dest_dir = os.path.join(root, f"{month} 메일")
    os.makedirs(dest_dir, exist_ok=True)
    result = []
    for p in paths:
        name = os.path.basename(p)
        if not name.lower().endswith((".zip", ".eml")):
            result.append({"name": name, "state": "skip", "msg": "zip/eml 파일이 아님"})
            continue
        if not os.path.isfile(p):
            result.append({"name": name, "state": "error", "msg": "파일을 찾을 수 없음"})
            continue
        if _inside(p, root):
            result.append({"name": name, "state": "skip", "msg": "이미 백업 폴더 안에 있음"})
            continue
        size = os.path.getsize(p)
        twin = next((q for q in (os.path.join(dest_dir, x) for x in os.listdir(dest_dir))
                     if os.path.isfile(q) and os.path.getsize(q) == size and _same_file(p, q)), None)
        if twin:
            result.append({"name": name, "state": "skip", "msg": f"이미 같은 파일이 있음 ({os.path.basename(twin)})"})
            continue
        clean = re.sub(r" \(\d+\)(?=\.[^.]+$)", "", name)   # 브라우저가 붙인 " (1)" 제거
        dest = os.path.join(dest_dir, clean)
        if os.path.exists(dest):
            stem, ext = os.path.splitext(clean)
            i = 2
            while os.path.exists(os.path.join(dest_dir, f"{stem} ({i}){ext}")):
                i += 1
            dest = os.path.join(dest_dir, f"{stem} ({i}){ext}")
        try:
            shutil.copy2(p, dest)
            result.append({"name": name, "state": "copied", "to": os.path.relpath(dest, root)})
        except OSError as ex:
            result.append({"name": name, "state": "error", "msg": str(ex)})
    copied = sum(r["state"] == "copied" for r in result)
    if copied and reindex:
        start_index()
    return {"ok": True, "dest": dest_dir, "copied": copied, "files": result}

# ---------------------------------------------------------------- 자동 가져오기 (선택 기능, 기본 꺼짐)
# config.json 에 아래처럼 켜 둔 PC 에서만 동작한다.
#   "auto_import": {"enabled": true, "dir": "C:\\Users\\나\\Downloads", "since": "2026-11-01"}
# dir 에 새로 생긴 받은/보낸/외부메일함*.zip 을 5분마다 찾아, 메일 날짜로 연월을 정해
# 백업폴더\YYYYMM 메일\ 로 복사(원본 유지)하고 반영한다. 한 번 처리한 파일은 다시 보지 않는다.

AUTO_SEEN_PATH = os.path.join(DATA_DIR, "auto_import_seen.json")
AUTO_STATE = {"last_check": None, "last": None}
AUTO_BOXES = ("받은메일함", "보낸메일함", "외부메일함")


def _auto_cfg():
    ai = CONFIG.get("auto_import")
    if not isinstance(ai, dict) or not ai.get("enabled"):
        return None
    d = ai.get("dir") or downloads_dir()
    since = 0.0
    if ai.get("since"):
        try:
            since = time.mktime(time.strptime(str(ai["since"])[:10], "%Y-%m-%d"))
        except ValueError:
            pass
    return d, since


def _zip_has_eml(p):
    try:
        with zipfile.ZipFile(p) as zf:
            return any(n.lower().endswith((".eml", ".zip")) for n in zf.namelist())
    except Exception:
        return False


def auto_import_once():
    """새 백업 zip 을 찾아 가져온다. 가져온 개수를 돌려준다"""
    cfg = _auto_cfg()
    if not cfg or not root_ok():
        return 0
    d, since = cfg
    if not os.path.isdir(d) or INDEX_LOCK.locked():
        return 0
    AUTO_STATE["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(AUTO_SEEN_PATH, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    now = time.time()
    groups = {}
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if not name.lower().endswith(".zip") or box_of(name) not in AUTO_BOXES or not os.path.isfile(p):
            continue
        st = os.stat(p)
        if st.st_mtime < since or now - st.st_mtime < 60:   # 오래된 파일 / 아직 받는 중
            continue
        key = f"{name}|{st.st_size}|{int(st.st_mtime)}"
        if key in seen:
            continue
        if not _zip_has_eml(p):
            seen[key] = "메일 없음"
            continue
        month, counts = detect_month([p])
        if not counts:
            seen[key] = "날짜 모름"
            continue
        groups.setdefault(month, []).append((p, key))
    total = 0
    for month, items in sorted(groups.items()):
        paths = [p for p, _ in items]
        PICKED_FILES.update(paths)
        r = do_import(paths, month, reindex=False)
        if not r.get("ok"):
            continue
        for (p, key), fr in zip(items, r["files"]):
            seen[key] = f"{month}:{fr['state']}"
        total += r["copied"]
        if r["copied"]:
            AUTO_STATE["last"] = {"time": time.strftime("%Y-%m-%d %H:%M"), "month": month, "copied": r["copied"],
                                  "names": [f["name"] for f in r["files"] if f["state"] == "copied"]}
    try:
        with open(AUTO_SEEN_PATH, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    if total:
        start_index()
    return total


def auto_import_loop():
    time.sleep(20)
    while True:
        wait = 300
        try:
            if _auto_cfg():
                if INDEX_LOCK.locked():
                    wait = 30
                else:
                    auto_import_once()
        except Exception as ex:
            print("[경고] 자동 가져오기 실패:", ex)
        time.sleep(wait)

# ---------------------------------------------------------------- 디코딩

KOREAN_CS = {"ks_c_5601-1987", "ks_c_5601_1987", "ks_c_5601", "euc-kr", "euc_kr",
             "korean", "x-windows-949", "windows-949", "cp949", "ks_c_5601-1989", "uhc"}


def dec(b, cs=None):
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    cs = (cs or "").strip().strip('"').lower()
    if cs in KOREAN_CS:
        return b.decode("cp949", "replace")
    if cs and cs not in ("unknown-8bit", "x-unknown", "us-ascii", "ascii"):
        try:
            return b.decode(cs)
        except (LookupError, UnicodeDecodeError):
            pass
    for c in ("utf-8", "cp949"):
        try:
            return b.decode(c)
        except UnicodeDecodeError:
            pass
    return b.decode("cp949", "replace")


def has_surrogates(s):
    return any("\udc80" <= ch <= "\udcff" for ch in s)


def hdr(v):
    """헤더 값을 사람이 읽을 수 있는 문자열로"""
    if v is None:
        return ""
    if isinstance(v, email.header.Header):
        v = str(v)
    v = str(v)
    if has_surrogates(v):
        v = dec(v.encode("ascii", "surrogateescape"))
    try:
        parts = email.header.decode_header(v)
    except Exception:
        return v
    out = []
    for t, cs in parts:
        if isinstance(t, str):
            out.append(t)
        elif cs:
            out.append(dec(t, cs))
        else:
            try:
                out.append(t.decode("raw-unicode-escape"))
            except Exception:
                out.append(dec(t))
    s = "".join(out)
    return re.sub(r"\s+", " ", s).strip()


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "table", "h1", "h2", "h3"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title") and self.skip:
            self.skip -= 1
        elif tag in ("td", "th"):
            self.out.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(s):
    p = _TextExtractor()
    try:
        p.feed(s)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", s)
    t = "".join(p.out).replace("\xa0", " ")
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def parse_msg(raw):
    return email.message_from_bytes(raw)


def leaf_parts(msg):
    return [p for p in msg.walk() if not p.is_multipart()]


def part_text(p):
    payload = p.get_payload(decode=True)
    return dec(payload, p.get_content_charset())


def part_filename(p):
    fn = p.get_filename()
    if not fn:
        return ""
    return hdr(fn)


def is_attachment(p):
    fn = part_filename(p)
    if not fn:
        return False
    disp = (p.get("Content-Disposition") or "").lower()
    if disp.startswith("attachment"):
        return True
    return not p.get("Content-ID")


def get_bodies(msg):
    """(plain, html) 본문"""
    plain, htm = None, None
    for p in leaf_parts(msg):
        ct = p.get_content_type()
        if is_attachment(p):
            continue
        if ct == "text/plain" and plain is None:
            plain = part_text(p)
        elif ct == "text/html" and htm is None:
            htm = part_text(p)
    return plain or "", htm or ""


DATE_IN_NAME = re.compile(r"\[(\d{4})-(\d{2})-(\d{2}) (오전|오후) (\d{1,2})_(\d{2})\]")


def mail_date(msg, name):
    d = msg.get("Date")
    if d:
        try:
            dt = email.utils.parsedate_to_datetime(hdr(d))
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    m = DATE_IN_NAME.search(name or "")
    if m:
        y, mo, da, ap, hh, mm = m.groups()
        h = int(hh) % 12 + (12 if ap == "오후" else 0)
        return f"{y}-{mo}-{da} {h:02d}:{mm}"
    return ""


def fmt_addrs(v):
    # getaddresses 는 "홍길동(HONG)/팀" 의 괄호를 주석으로 보고 잘라내므로 원문을 그대로 정리만 함
    v = hdr(v)
    v = re.sub(r"\s*<", " <", v.replace('"', ""))
    return re.sub(r">\s*,\s*", ">, ", v).strip()


def extract_record(raw, name):
    msg = parse_msg(raw)
    plain, htm = get_bodies(msg)
    body = plain if plain.strip() else html_to_text(htm)
    atts = [part_filename(p) for p in leaf_parts(msg) if is_attachment(p)]
    subject = hdr(msg.get("Subject"))
    if not subject:
        subject = re.sub(r"^\[.*?\]\[.*?\]\s*", "", os.path.splitext(os.path.basename(name))[0])
    return {
        "msgid": hdr(msg.get("Message-ID")).strip("<> "),
        "date": mail_date(msg, os.path.basename(name)),
        "sender": fmt_addrs(msg.get("From")),
        "recipients": fmt_addrs(msg.get("To")),
        "cc": fmt_addrs(msg.get("Cc")),
        "subject": subject,
        "body": body[:BODY_LIMIT],
        "attachments": "\n".join(atts),
        "size": len(raw),
    }

# ---------------------------------------------------------------- DB


def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


# ---------------------------------------------------------------- 전문 검색 색인 (FTS5, 2글자 단위)
# 한글은 띄어쓰기 단위로 끊으면 "회의실" 에서 "회의" 를 못 찾고, SQLite 기본 trigram 은 3글자 이상만 된다.
# 그래서 공백으로 나눈 덩어리마다 2글자씩 겹쳐 끊은 토큰(bigram)을 직접 만들어 넣는다.
#   "테이블스페이스" → 테이 이블 블스 스페 페이 이스   (검색어도 같은 방식으로 끊어 '연속된 구'로 찾음)
# FTS 는 후보를 빠르게 좁히는 용도이고, 최종 판정은 기존 instr 조건이 그대로 한다(결과가 달라지지 않음).
FTS_COLS = ("subject", "sender", "recipients", "cc", "attachments", "body")
FTS = {"ok": False}   # 색인이 다 만들어졌을 때만 True


def _bigram_tokens(chunk):
    # 토큰을 16진수로 바꿔 넣어 FTS 토크나이저가 기호·한글을 다시 쪼개지 않게 한다
    return ["x" + chunk[i:i + 2].encode("utf-8").hex() for i in range(len(chunk) - 1)]


def fts_text(s):
    return " ".join(tok for w in (s or "").lower().split() if len(w) >= 2 for tok in _bigram_tokens(w))


def fts_query(term, cols):
    """검색어 하나 → FTS MATCH 식 (2글자 이상 덩어리만). 쓸 수 없으면 None"""
    parts = [f'{{{" ".join(cols)}}} : "{" ".join(_bigram_tokens(w))}"' for w in term.lower().split() if len(w) >= 2]
    return " AND ".join(parts) or None


def fts_setup(con):
    """FTS5 표를 만든다. 이 Python 의 SQLite 가 지원하지 않으면 조용히 기존 방식만 쓴다"""
    try:
        con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS mails_fts USING fts5({', '.join(FTS_COLS)},"
                    " content='', contentless_delete=1, tokenize='unicode61')")
        return True
    except sqlite3.OperationalError:
        return False


def fts_add(con, where, args=()):
    rows = con.execute(f"SELECT id, {', '.join(FTS_COLS)} FROM mails WHERE {where}", args).fetchall()
    con.executemany(f"INSERT INTO mails_fts(rowid, {', '.join(FTS_COLS)}) VALUES(?{', ?' * len(FTS_COLS)})",
                    [(r[0], *(fts_text(r[i + 1]) for i in range(len(FTS_COLS)))) for r in rows])


def fts_remove_src(con, src):
    if FTS.get("table"):
        con.execute("DELETE FROM mails_fts WHERE rowid IN (SELECT id FROM mails WHERE src=?)", (src,))


def fts_build_if_needed(con):
    """예전 색인을 처음 열었을 때 한 번 전체를 FTS 에 넣는다 (그동안 검색은 기존 방식)"""
    if not FTS.get("table"):
        return
    row = con.execute("SELECT v FROM meta WHERE k='fts'").fetchone()
    if row and row[0] == "1":
        FTS["ok"] = True
        return
    STATUS["current"] = "검색 색인 만드는 중"
    con.execute("DELETE FROM mails_fts")
    fts_add(con, "1")
    con.execute("INSERT OR REPLACE INTO meta VALUES('fts', '1')")
    con.commit()
    FTS["ok"] = True


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS sources(path TEXT PRIMARY KEY, mtime REAL, size INTEGER);
    CREATE TABLE IF NOT EXISTS mails(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        src TEXT, inner TEXT, month TEXT, box TEXT,
        msgid TEXT, date TEXT, sender TEXT, recipients TEXT, cc TEXT,
        subject TEXT, body TEXT, attachments TEXT, size INTEGER);
    CREATE INDEX IF NOT EXISTS ix_mails_src ON mails(src);
    CREATE INDEX IF NOT EXISTS ix_mails_date ON mails(date);
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(mails)")}
    if "dup" not in cols:   # 중복 표시 컬럼 (예전 색인은 컬럼만 추가하고 바로 계산)
        con.execute("ALTER TABLE mails ADD COLUMN dup INTEGER NOT NULL DEFAULT 0")
        mark_duplicates(con)
    con.execute("CREATE INDEX IF NOT EXISTS ix_mails_msgid ON mails(msgid)")
    FTS["table"] = fts_setup(con)
    row = con.execute("SELECT v FROM meta WHERE k='fts'").fetchone()
    FTS["ok"] = bool(FTS["table"] and row and row[0] == "1")
    row = con.execute("SELECT v FROM meta WHERE k='version'").fetchone()
    if not row or row[0] != INDEX_VERSION:
        con.executescript("DELETE FROM mails; DELETE FROM sources; DELETE FROM meta WHERE k='fts';")
        con.execute("INSERT OR REPLACE INTO meta VALUES('version', ?)", (INDEX_VERSION,))
    con.commit()
    con.close()

# ---------------------------------------------------------------- 색인

STATUS = {"running": False, "done": 0, "total": 0, "current": "", "added": 0,
          "errors": [], "last_finished": None, "message": "", "by_month": {}, "removed": 0}
INDEX_LOCK = threading.Lock()


def zip_name(info):
    n = info.filename
    if not (info.flag_bits & 0x800):
        try:
            n = n.encode("cp437").decode("cp949")
        except Exception:
            pass
    return n


def box_of(name):
    base = os.path.splitext(os.path.basename(name))[0].replace(" ", "")
    for key in ("받은메일함", "보낸메일함", "외부메일함", "임시보관함", "보관함"):
        if key in base:
            return key
    return "구분없음"


def month_of(path, root):
    rel = os.path.relpath(path, root)
    return rel.split(os.sep)[0] if os.sep in rel else ""


def iter_zip(zf, chain, depth=0):
    """zip 안의 eml 을 (inner_chain, display_name, raw) 로 순회. zip 안의 zip 도 처리"""
    for info in zf.infolist():
        if info.is_dir():
            continue
        low = info.filename.lower()
        if low.endswith(".eml"):
            yield chain + [info.filename], zip_name(info), zf.read(info)
        elif low.endswith(".zip") and depth < 3:
            try:
                inner = zipfile.ZipFile(io.BytesIO(zf.read(info)))
                yield from iter_zip(inner, chain + [info.filename], depth + 1)
            except Exception as ex:
                STATUS["errors"].append(f"{zip_name(info)}: {ex}")


def scan_sources(root):
    out = []
    for dp, dn, fn in os.walk(root):
        for f in fn:
            low = f.lower()
            if low.endswith(".zip") or low.endswith(".eml"):
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append((p, st.st_mtime, st.st_size))
    return out


def run_index():
    if not INDEX_LOCK.acquire(blocking=False):
        return
    try:
        root = CONFIG["mail_root"]
        STATUS.update(running=True, done=0, total=0, current="", added=0, errors=[], message="",
                      by_month={}, removed=0)
        if not root:
            STATUS["message"] = "메일 백업 폴더가 아직 지정되지 않았습니다"
            return
        if not os.path.isdir(root):
            STATUS["message"] = f"백업 폴더를 찾을 수 없습니다: {root}"
            return
        con = db()
        known = {r["path"]: (r["mtime"], r["size"]) for r in con.execute("SELECT * FROM sources")}
        found = scan_sources(root)
        found_paths = {p for p, _, _ in found}
        # 사라진 파일 정리
        for p in set(known) - found_paths:
            STATUS["removed"] += 1
            fts_remove_src(con, p)
            con.execute("DELETE FROM mails WHERE src=?", (p,))
            con.execute("DELETE FROM sources WHERE path=?", (p,))
        con.commit()
        todo = [(p, m, s) for p, m, s in found if known.get(p) != (m, s)]
        STATUS["total"] = len(todo)
        for p, mtime, size in todo:
            STATUS["current"] = os.path.relpath(p, root)
            fts_remove_src(con, p)
            con.execute("DELETE FROM mails WHERE src=?", (p,))
            month = month_of(p, root)
            rows = []
            try:
                if p.lower().endswith(".zip"):
                    with zipfile.ZipFile(p) as zf:
                        for chain, name, raw in iter_zip(zf, []):
                            try:
                                rec = extract_record(raw, name)
                            except Exception as ex:
                                STATUS["errors"].append(f"{name}: {ex}")
                                continue
                            rows.append((p, json.dumps(chain, ensure_ascii=False), month,
                                         box_of(p if len(chain) == 1 else chain[-2]), rec))
                else:
                    with open(p, "rb") as f:
                        raw = f.read()
                    rec = extract_record(raw, os.path.basename(p))
                    rows.append((p, "[]", month, box_of(os.path.dirname(p)), rec))
            except Exception as ex:
                STATUS["errors"].append(f"{os.path.relpath(p, root)}: {ex}")
            for src, inner, mon, box, r in rows:
                con.execute(
                    "INSERT INTO mails(src,inner,month,box,msgid,date,sender,recipients,cc,subject,body,attachments,size)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (src, inner, mon, box, r["msgid"], r["date"], r["sender"], r["recipients"], r["cc"],
                     r["subject"], r["body"], r["attachments"], r["size"]))
            if FTS["ok"]:
                fts_add(con, "src=?", (p,))
            con.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?)", (p, mtime, size))
            con.commit()
            STATUS["added"] += len(rows)
            STATUS["done"] += 1
            bm = STATUS["by_month"].setdefault(month or "(루트)", {"files": 0, "mails": 0})
            bm["files"] += 1
            bm["mails"] += len(rows)
        mark_duplicates(con)
        try:
            fts_build_if_needed(con)
        except Exception as ex:
            STATUS["errors"].append(f"검색 색인: {ex}")
        try:
            classify_unsorted(con)
        except Exception as ex:
            STATUS["errors"].append(f"메일함 분류: {ex}")
        con.close()
        STATUS["last_finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        STATUS["running"] = False
        STATUS["current"] = ""
        INDEX_LOCK.release()


def _email_of(s):
    m = re.search(r"<([^>]+)>", s or "")
    return (m.group(1) if m else (s or "")).strip().lower()


def my_emails(con):
    """내 메일 주소: config 의 my_emails, 없으면 보낸메일함에서 가장 많이 나온 보낸사람"""
    cfg = [e.strip().lower() for e in (CONFIG.get("my_emails") or []) if e and e.strip()]
    if cfg:
        return set(cfg)
    cnt = {}
    for (s,) in con.execute("SELECT sender FROM mails WHERE box='보낸메일함'"):
        e = _email_of(s)
        if e:
            cnt[e] = cnt.get(e, 0) + 1
    return {max(cnt, key=cnt.get)} if cnt else set()


def mark_duplicates(con):
    """같은 Message-ID 가 여러 백업에 있으면 가장 먼저 색인된 것만 dup=0. 검색 때마다 계산하지 않도록 미리 저장"""
    con.execute("""UPDATE mails SET dup = CASE WHEN id IN (
        SELECT MIN(id) FROM mails GROUP BY CASE WHEN msgid='' THEN 'id'||id ELSE msgid END) THEN 0 ELSE 1 END""")
    con.commit()


def classify_unsorted(con):
    """메일함 정보가 없는 옛 백업(구분없음): 내가 보낸 메일 → 보낸메일함, 나머지 → 받은메일함"""
    me = my_emails(con)
    if not me:  # 내 주소를 알 수 없으면 그대로 둔다
        return 0
    rows = con.execute("SELECT id, sender FROM mails WHERE box='구분없음'").fetchall()
    con.executemany("UPDATE mails SET box=? WHERE id=?",
                    [("보낸메일함" if _email_of(s) in me else "받은메일함", i) for i, s in rows])
    con.commit()
    return len(rows)


def start_index():
    threading.Thread(target=run_index, daemon=True).start()

# ---------------------------------------------------------------- 메일 원본 읽기


def read_raw(row):
    chain = json.loads(row["inner"])
    if not chain:
        with open(row["src"], "rb") as f:
            return f.read()
    zf = zipfile.ZipFile(row["src"])
    try:
        for i, member in enumerate(chain):
            data = zf.read(member)
            if i < len(chain) - 1:
                zf.close()
                zf = zipfile.ZipFile(io.BytesIO(data))
        return data
    finally:
        zf.close()


def get_row(mid):
    con = db()
    try:
        return con.execute("SELECT * FROM mails WHERE id=?", (mid,)).fetchone()
    finally:
        con.close()

# ---------------------------------------------------------------- 검색

FIELDS = {   # 검색 범위 → 찾아볼 컬럼 (짧은 컬럼부터: 앞에서 찾으면 본문은 보지 않음)
    "all": ("subject", "sender", "recipients", "cc", "attachments", "body"),
    "subject": ("subject",),
    "sender": ("sender",),
    "recipients": ("recipients", "cc"),
    "body": ("body",),
    "attachments": ("attachments",),
}


def parse_terms(q):
    terms = []
    for m in re.finditer(r'(-?)"([^"]+)"|(-?)(\S+)', q or ""):
        if m.group(2):
            terms.append((m.group(1) == "-", m.group(2)))
        else:
            neg = m.group(3) == "-" and len(m.group(4)) > 0
            terms.append((neg, m.group(4)))
    return [(n, t) for n, t in terms if t]


def search(params):
    q = params.get("q", "")
    field = params.get("field", "all")
    cols = FIELDS.get(field, FIELDS["all"])
    where, args = [], []
    terms = parse_terms(q)
    if FTS["ok"]:   # 전문 검색 색인으로 후보를 먼저 좁힌다 (제외어는 아래 instr 로만)
        mq = [x for x in (fts_query(t, cols) for neg, t in terms if not neg) if x]
        if mq:
            where.append("id IN (SELECT rowid FROM mails_fts WHERE mails_fts MATCH ?)")
            args.append(" AND ".join(mq))
    for neg, t in terms:
        hit = " OR ".join(f"instr(lower(ifnull({c},'')), ?) > 0" for c in cols)
        where.append(f"NOT ({hit})" if neg else f"({hit})")
        args += [t.lower()] * len(cols)
    if params.get("box"):
        where.append("box=?")
        args.append(params["box"])
    if params.get("month"):
        where.append("month=?")
        args.append(params["month"])
    if params.get("ym"):  # 메일 날짜 기준 '2026' 또는 '2026-10'
        where.append("substr(date,1,?)=?")
        args += [len(params["ym"]), params["ym"]]
    if params.get("from"):
        where.append("date>=?")
        args.append(params["from"])
    if params.get("to"):
        where.append("date<=?")
        args.append(params["to"] + " 99")
    if params.get("attach") == "1":
        where.append("attachments<>''")
    # 같은 Message-ID 중복(풀린 eml + zip 둘 다 있는 경우 등)은 하나만 — 색인할 때 미리 계산해 둔 dup
    where.append("dup=0")
    wsql = " AND ".join(where)
    order = "ASC" if params.get("sort") == "asc" else "DESC"
    page = max(0, int(params.get("page", 0) or 0))
    size = 50
    con = db()
    try:
        # 조건에 맞는 메일을 한 번만 훑으면서 전체 개수(COUNT OVER)와 이번 페이지의 id 만 받는다.
        # (본문 같은 큰 칸까지 들고 정렬하면 느려서, 정렬은 id·날짜로만 하고 내용은 50통만 따로 읽는다)
        hits = con.execute(
            f"SELECT id, COUNT(*) OVER() FROM mails WHERE {wsql} ORDER BY date {order}, id {order} LIMIT ? OFFSET ?",
            args + [size, page * size]).fetchall()
        if hits:
            total = hits[0][1]
        else:   # 마지막 페이지 너머를 요청한 경우에만 따로 센다
            total = con.execute(f"SELECT COUNT(*) FROM mails WHERE {wsql}", args).fetchone()[0] if page else 0
        ids = [h[0] for h in hits]
        by_id = {r["id"]: r for r in con.execute(
            f"SELECT id,month,box,date,sender,recipients,subject,attachments,body FROM mails"
            f" WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        rows = [by_id[i] for i in ids]
    finally:
        con.close()
    pos = [t for n, t in terms if not n]
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "month": r["month"], "box": r["box"], "date": r["date"],
            "sender": r["sender"], "recipients": r["recipients"], "subject": r["subject"],
            "attach_count": len(r["attachments"].split("\n")) if r["attachments"] else 0,
            "snippet": snippet(r["body"], pos),
        })
    return {"total": total, "page": page, "size": size, "items": items, "terms": pos}


def snippet(body, terms, width=140):
    body = re.sub(r"\s+", " ", body or "")
    low = body.lower()
    best = -1
    for t in terms:
        i = low.find(t.lower())
        if i >= 0 and (best < 0 or i < best):
            best = i
    if best < 0:
        return body[:width]
    s = max(0, best - width // 3)
    return ("…" if s else "") + body[s:s + width] + ("…" if s + width < len(body) else "")


def facets():
    con = db()
    try:
        months = [r[0] for r in con.execute("SELECT DISTINCT month FROM mails ORDER BY month DESC")]
        boxes = [r[0] for r in con.execute("SELECT DISTINCT box FROM mails ORDER BY box")]
        count = con.execute("SELECT COUNT(*) FROM mails").fetchone()[0]
        uniq = "dup=0"
        unique = con.execute(f"SELECT COUNT(*) FROM mails WHERE {uniq}").fetchone()[0]
        ym_counts = {r[0]: r[1] for r in con.execute(
            f"SELECT substr(date,1,7), COUNT(*) FROM mails WHERE {uniq} GROUP BY 1 ORDER BY 1 DESC")}
        box_counts = {r[0]: r[1] for r in con.execute(
            f"SELECT box, COUNT(*) FROM mails WHERE {uniq} GROUP BY 1")}
    finally:
        con.close()
    return {"months": months, "boxes": boxes, "count": count, "unique": unique,
            "ym_counts": ym_counts, "box_counts": box_counts}

# ---------------------------------------------------------------- 메일 상세


def mail_detail(mid):
    row = get_row(mid)
    if not row:
        return None
    msg = parse_msg(read_raw(row))
    atts = []
    for i, p in enumerate(leaf_parts(msg)):
        if is_attachment(p):
            payload = p.get_payload(decode=True) or b""
            atts.append({"index": i, "name": part_filename(p), "size": len(payload)})
    try:
        src = os.path.relpath(row["src"], CONFIG["mail_root"])
    except ValueError:  # 다른 드라이브 등
        src = row["src"]
    return {
        "id": row["id"], "month": row["month"], "box": row["box"], "date": row["date"],
        "sender": row["sender"], "recipients": row["recipients"], "cc": row["cc"],
        "subject": row["subject"], "attachments": atts,
        "source": src + (" › " + " › ".join(json.loads(row["inner"])) if row["inner"] != "[]" else ""),
    }


def highlight_html(s, terms):
    if not terms:
        return s
    pat = re.compile("|".join(re.escape(html.escape(t, quote=False)) for t in sorted(terms, key=len, reverse=True)),
                     re.I)
    out, skip = [], 0
    for tok in re.split(r"(<[^>]*>)", s):
        if tok.startswith("<"):
            low = tok.lower()
            if re.match(r"<(style|script|title|head)\b", low):
                skip += 1
            elif re.match(r"</(style|script|title|head)\b", low) and skip:
                skip -= 1
            out.append(tok)
        elif skip or not tok:
            out.append(tok)
        else:
            out.append(pat.sub(lambda m: '<mark style="background:#ffe066;color:#000">' + m.group(0) + "</mark>",
                               tok))
    return "".join(out)


def mail_body_html(mid, terms):
    row = get_row(mid)
    if not row:
        return None
    msg = parse_msg(read_raw(row))
    parts = leaf_parts(msg)
    cid_map = {}
    for i, p in enumerate(parts):
        cid = (p.get("Content-ID") or "").strip().strip("<>")
        if cid:
            cid_map[cid.lower()] = i
    plain, htm = get_bodies(msg)
    if htm.strip():
        body = re.sub(r"<script\b.*?</script>", "", htm, flags=re.I | re.S)
        body = re.sub(r"<meta[^>]*charset[^>]*>", "", body, flags=re.I)

        def repl(m):
            cid = urllib.parse.unquote(m.group(2)).strip().lower()
            if cid in cid_map:
                return f'{m.group(1)}/api/mail/{mid}/part/{cid_map[cid]}?inline=1'
            return m.group(0)
        body = re.sub(r'((?:src|background)\s*=\s*["\']?)cid:([^"\'\s>]+)', repl, body, flags=re.I)
    else:
        body = ('<pre style="white-space:pre-wrap;word-break:break-word;font-family:inherit">'
                + html.escape(plain) + "</pre>")
    body = highlight_html(body, terms)
    head = ('<!doctype html><html><head><meta charset="utf-8"><base target="_blank">'
            '<style>body{font-family:"Malgun Gothic","맑은 고딕",sans-serif;font-size:14px;'
            'margin:16px;color:#222;background:#fff;word-break:break-word}img{max-width:100%;height:auto}</style>'
            '</head><body>')
    return head + body + "</body></html>"


def mail_part(mid, idx):
    row = get_row(mid)
    if not row:
        return None
    msg = parse_msg(read_raw(row))
    parts = leaf_parts(msg)
    if not (0 <= idx < len(parts)):
        return None
    p = parts[idx]
    return p.get_payload(decode=True) or b"", p.get_content_type(), part_filename(p) or f"part{idx}"

# ---------------------------------------------------------------- HTTP

SAFE_INLINE = {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp"}


def content_disposition(kind, name):
    ascii_name = re.sub(r'[^\x20-\x7e]|["\\]', "_", name) or "file"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}"


class Handler(BaseHTTPRequestHandler):
    server_version = "MailSearch"

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        u = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/status":
                return self._json({"status": STATUS, "facets": facets(), "root": CONFIG["mail_root"],
                                   "root_ok": root_ok(), "version": APP_VERSION,
                                   "auto": {"on": bool(_auto_cfg()), **AUTO_STATE}})
            if path == "/api/search":
                return self._json(search(params))
            m = re.fullmatch(r"/api/mail/(\d+)", path)
            if m:
                d = mail_detail(int(m.group(1)))
                return self._json(d) if d else self._json({"error": "not found"}, 404)
            m = re.fullmatch(r"/api/mail/(\d+)/body", path)
            if m:
                terms = [t for n, t in parse_terms(params.get("hl", "")) if not n]
                b = mail_body_html(int(m.group(1)), terms)
                if b is None:
                    return self._send(404, "not found", "text/plain")
                csp = ("default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
                       "font-src data:; base-uri 'none'; form-action 'none'")
                return self._send(200, b, "text/html; charset=utf-8", {"Content-Security-Policy": csp})
            m = re.fullmatch(r"/api/mail/(\d+)/part/(\d+)", path)
            if m:
                r = mail_part(int(m.group(1)), int(m.group(2)))
                if r is None:
                    return self._send(404, "not found", "text/plain")
                data, ctype, name = r
                if params.get("inline") == "1" and ctype in SAFE_INLINE:
                    return self._send(200, data, ctype)
                return self._send(200, data, "application/octet-stream",
                                  {"Content-Disposition": content_disposition("attachment", name)})
            m = re.fullmatch(r"/api/mail/(\d+)/eml", path)
            if m:
                row = get_row(int(m.group(1)))
                if not row:
                    return self._send(404, "not found", "text/plain")
                name = re.sub(r'[\\/:*?"<>|]', "_", (row["subject"] or "mail"))[:120] + ".eml"
                return self._send(200, read_raw(row), "message/rfc822",
                                  {"Content-Disposition": content_disposition("attachment", name)})
            return self._send(404, "not found", "text/plain")
        except Exception as ex:
            return self._json({"error": str(ex)}, 500)

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        # 다른 사이트에서의 요청 차단
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return self._send(403, "forbidden", "text/plain")
        if self.headers.get("X-Requested-With") != "mailsearch":
            return self._send(403, "forbidden", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            body = {}
        if self.path == "/api/reindex":
            start_index()
            return self._json({"ok": True})
        if self.path == "/api/pick-folder":
            p = pick_folder()
            if p is None:
                return self._json({"ok": False, "error": "폴더 선택 창을 열 수 없습니다. 경로를 직접 붙여넣어 주세요."})
            return self._json({"ok": True, "path": p})
        if self.path == "/api/pick-files":
            ps = pick_files()
            if ps is None:
                return self._json({"ok": False, "error": "파일 선택 창을 열 수 없습니다."})
            ps = [p for p in ps if os.path.isfile(p)]
            PICKED_FILES.update(ps)
            return self._json({"ok": True, **inspect_import(ps)})
        if self.path == "/api/import":
            paths = body.get("paths") if isinstance(body.get("paths"), list) else []
            return self._json(do_import([str(p) for p in paths], str(body.get("month", "")).strip()))
        if self.path == "/api/config":
            p = os.path.normpath(str(body.get("mail_root", "")).strip().strip('"')) if body.get("mail_root") else ""
            if not p or not os.path.isdir(p):
                return self._json({"ok": False, "error": "폴더를 찾을 수 없습니다: " + (p or "(비어 있음)")})
            zips, emls = count_backup_files(p)
            if zips + emls == 0 and not body.get("force"):
                return self._json({"ok": False, "empty": True,
                                   "error": "이 폴더(하위 폴더 포함)에 zip 이나 eml 파일이 없습니다."})
            if INDEX_LOCK.locked():
                return self._json({"ok": False, "error": "지금 반영 중입니다. 끝난 뒤 다시 시도해 주세요."})
            CONFIG["mail_root"] = p
            save_config()
            start_index()
            return self._json({"ok": True, "path": p, "zips": zips, "emls": emls})
        return self._send(404, "not found", "text/plain")


class Server(ThreadingHTTPServer):
    # Windows 에서 SO_REUSEADDR 를 켜면 같은 포트에 두 번째 프로그램이 또 붙어 버린다.
    # → 끄고, 포트를 독점(SO_EXCLUSIVEADDRUSE)해서 "이미 실행 중" 을 제대로 감지한다.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        import socket
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    init_db()
    port = int(CONFIG.get("port", 8765))
    url = f"http://127.0.0.1:{port}/"
    try:
        srv = Server(("127.0.0.1", port), Handler)
    except OSError:
        if "--no-browser" in sys.argv:  # 자동 실행 등: 이미 떠 있으면 조용히 끝냄
            return
        print(f"이미 실행 중인 것 같습니다. 브라우저에서 엽니다: {url}")
        webbrowser.open(url)
        return
    print("메일 백업 검색기")
    print(f"  백업 폴더 : {CONFIG['mail_root'] or '(아직 없음 - 브라우저 화면에서 선택)'}")
    print(f"  주소      : {url}")
    print("  종료하려면 이 창을 닫거나 Ctrl+C")
    start_index()
    if _auto_cfg():
        print(f"  자동 가져오기: {_auto_cfg()[0]}")
    threading.Thread(target=auto_import_loop, daemon=True).start()
    if CONFIG.get("open_browser", True) and "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if "--index-only" in sys.argv:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        init_db()
        t0 = time.time()
        run_index()
        f = facets()
        summary = {
            "elapsed_sec": round(time.time() - t0, 1),
            "message": STATUS["message"],
            "changed_files": STATUS["done"],
            "added_mails": STATUS["added"],
            "removed_files": STATUS["removed"],
            "by_month": STATUS["by_month"],
            "errors": STATUS["errors"][:20],
            "error_count": len(STATUS["errors"]),
            "total_mails": f["count"],
            "latest_month_folder": f["months"][0] if f["months"] else None,
            "mail_root": CONFIG["mail_root"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        main()
