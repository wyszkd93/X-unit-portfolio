# -*- coding: utf-8 -*-
"""
메일 백업 검색기 테스트 — 표준 라이브러리 unittest 만 사용
    python -m unittest discover -s tests -v

make_demo.py 로 만든 가상의 메일 백업(실제 메일 아님)을 임시 폴더에 만들어서 검사한다.
"""
import http.client
import importlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="mailsearch_test_")
os.environ["MAIL_SEARCH_DATA_DIR"] = os.path.join(TMP, "boot")   # import 할 때 실제 config.json 을 건드리지 않게

import make_demo  # noqa: E402
m = importlib.import_module("mail_search")  # noqa: E402

ME = "sky.kim@example.com"


def fresh_env(name):
    """새 임시 폴더에 가짜 백업을 만들고, 그 폴더를 보도록 검색기 설정을 바꾼 뒤 색인한다"""
    base = os.path.join(TMP, name)
    total = make_demo.build(base, port=0)
    data = os.path.join(base, "data")
    m.DB_PATH = os.path.join(data, "mail_index.db")
    m.AUTO_SEEN_PATH = os.path.join(data, "auto_import_seen.json")
    m.CONFIG.clear()
    m.CONFIG.update({"mail_root": os.path.join(base, "backup"), "port": 0, "open_browser": False})
    m.PICKED_FILES.clear()
    m.init_db()
    m.run_index()
    return base, total


def wait_index(timeout=30):
    t0 = time.time()
    time.sleep(0.2)
    while m.INDEX_LOCK.locked():
        if time.time() - t0 > timeout:
            raise TimeoutError("색인이 끝나지 않음")
        time.sleep(0.1)


def body_of(mid):
    con = m.db()
    try:
        return con.execute("SELECT body FROM mails WHERE id=?", (mid,)).fetchone()[0]
    finally:
        con.close()


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


# ---------------------------------------------------------------- 색인 · 검색 (읽기만)

class IndexAndSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.total = fresh_env("readonly")

    def test_all_mails_indexed_without_errors(self):
        self.assertEqual(m.STATUS["errors"], [])
        f = m.facets()
        self.assertEqual(f["count"], self.total)
        self.assertEqual(f["unique"], self.total)
        self.assertEqual(len(f["months"]), len(make_demo.MONTHS))
        self.assertEqual(sum(f["box_counts"].values()), self.total)
        self.assertEqual(set(f["boxes"]), {"받은메일함", "보낸메일함", "외부메일함"})

    def test_nested_zip_is_read(self):
        con = m.db()
        try:
            rows = con.execute("SELECT inner FROM mails WHERE month='202609 메일'").fetchall()
        finally:
            con.close()
        self.assertTrue(any(len(json.loads(r[0])) == 2 for r in rows), "zip 안의 zip 에 든 메일이 없음")

    def test_korean_headers_decoded(self):
        r = m.search({"q": ""})
        self.assertGreater(r["total"], 0)
        for it in r["items"]:
            self.assertNotIn("�", it["subject"] + it["sender"])
            self.assertRegex(it["subject"], r"[가-힣]")

    def test_sender_name_team_and_address_kept(self):
        r = m.search({"q": "박서연", "field": "sender"})
        self.assertGreater(r["total"], 0)
        s = r["items"][0]["sender"]
        self.assertIn("박서연/보안관제팀", s)
        self.assertIn("seoyeon.park@example.com", s)

    def test_and_search(self):
        r = m.search({"q": "배치 디스크"})
        self.assertGreater(r["total"], 0)
        for it in r["items"]:
            text = it["subject"] + body_of(it["id"])
            self.assertIn("배치", text)
            self.assertIn("디스크", text)

    def test_phrase_search(self):
        r = m.search({"q": '"PM 작업"'})
        self.assertGreater(r["total"], 0)
        for it in r["items"]:
            self.assertIn("PM 작업", body_of(it["id"]))

    def test_exclude_search(self):
        self.assertGreater(m.search({"q": "테이블스페이스"})["total"], 0)
        self.assertEqual(m.search({"q": "테이블스페이스 -확인했습니다"})["total"], 0)

    def test_field_filters(self):
        r = m.search({"q": "장애보고", "field": "subject"})
        self.assertGreater(r["total"], 0)
        self.assertTrue(all("장애보고" in it["subject"] for it in r["items"]))
        r = m.search({"q": "장애보고서.docx", "field": "attachments"})
        self.assertGreater(r["total"], 0)

    def test_box_month_and_attachment_filters(self):
        f = m.facets()
        for box, n in f["box_counts"].items():
            self.assertEqual(m.search({"box": box})["total"], n)
        self.assertEqual(m.search({"ym": "2026-10"})["total"], f["ym_counts"]["2026-10"])
        with_att = m.search({"attach": "1"})
        self.assertGreater(with_att["total"], 0)
        self.assertLess(with_att["total"], self.total)

    def test_detail_attachment_and_highlight(self):
        it = m.search({"q": "장애보고", "field": "subject"})["items"][0]
        d = m.mail_detail(it["id"])
        names = [a["name"] for a in d["attachments"]]
        self.assertIn("장애보고서.docx", names)
        data, ctype, name = m.mail_part(it["id"], d["attachments"][0]["index"])
        self.assertTrue(data.startswith(b"PK\x03\x04demo"))
        self.assertEqual(name, "장애보고서.docx")
        html = m.mail_body_html(it["id"], ["디스크"]).decode("utf-8") \
            if isinstance(m.mail_body_html(it["id"], ["디스크"]), bytes) else m.mail_body_html(it["id"], ["디스크"])
        self.assertIn("<mark", html)
        self.assertNotIn("<script", html.lower())

    def test_paging(self):
        p0 = m.search({"q": "", "page": "0"})
        p1 = m.search({"q": "", "page": "1"})
        self.assertEqual(len(p0["items"]), 50)
        self.assertFalse({i["id"] for i in p0["items"]} & {i["id"] for i in p1["items"]})


# ---------------------------------------------------------------- 증분 색인 · 중복 · 분류

class IncrementalIndexTest(unittest.TestCase):
    def setUp(self):
        self.base, self.total = fresh_env("incremental")
        self.root = m.CONFIG["mail_root"]

    def test_unchanged_files_are_not_reread(self):
        m.run_index()
        self.assertEqual(m.STATUS["total"], 0)
        self.assertEqual(m.facets()["count"], self.total)

    def test_duplicate_backup_is_deduplicated_and_removal_cleans_up(self):
        src = os.path.join(self.root, "202610 메일", "보낸메일함.zip")
        copy = os.path.join(self.root, "202610 메일", "보낸메일함_사본.zip")
        shutil.copy(src, copy)
        with zipfile.ZipFile(copy) as z:
            n = len(z.namelist())
        m.run_index()
        f = m.facets()
        self.assertEqual(f["count"], self.total + n)
        self.assertEqual(f["unique"], self.total, "같은 Message-ID 는 한 번만 보여야 함")
        os.remove(copy)
        m.run_index()
        self.assertEqual(m.STATUS["removed"], 1)
        self.assertEqual(m.facets()["count"], self.total)

    def test_unsorted_backup_classified_by_sender(self):
        mine = make_demo.make_mail(make_demo.addr(*make_demo.ME), ["a@example.com"], [], "내가 보낸 메일", "본문",
                                   [], make_demo.datetime(2026, 10, 5, 9, 0, tzinfo=make_demo.KST), mid="<mine@t>")
        other = make_demo.make_mail("x@example.com", [ME], [], "남이 보낸 메일", "본문",
                                    [], make_demo.datetime(2026, 10, 5, 10, 0, tzinfo=make_demo.KST), mid="<other@t>")
        with zipfile.ZipFile(os.path.join(self.root, "202610 메일", "옛백업.zip"), "w") as z:
            z.writestr("a.eml", mine)
            z.writestr("b.eml", other)
        m.run_index()
        self.assertEqual(m.search({"q": "내가 보낸 메일"})["items"][0]["box"], "보낸메일함")
        self.assertEqual(m.search({"q": "남이 보낸 메일"})["items"][0]["box"], "받은메일함")


# ---------------------------------------------------------------- 백업 가져오기 · 자동 가져오기

class ImportTest(unittest.TestCase):
    def setUp(self):
        self.base, self.total = fresh_env("import")
        self.root = m.CONFIG["mail_root"]
        self.dl = os.path.join(self.base, "downloads")
        os.makedirs(self.dl)
        # 다른 달(2026-10)의 백업을 "새로 받은 파일" 처럼 다운로드 폴더에 둔다
        shutil.copy(os.path.join(self.root, "202610 메일", "외부메일함.zip"), os.path.join(self.dl, "외부메일함 (1).zip"))
        self.src = os.path.join(self.dl, "외부메일함 (1).zip")

    def test_month_detected_from_mail_dates(self):
        month, counts = m.detect_month([self.src])
        self.assertEqual(month, "202610")
        self.assertTrue(counts)

    def test_import_rules(self):
        r = m.do_import([self.src], "202610", reindex=False)
        self.assertFalse(r["ok"], "파일 선택 창을 거치지 않은 경로는 거부해야 함")
        m.PICKED_FILES.add(self.src)
        self.assertFalse(m.do_import([self.src], "202613", reindex=False)["ok"])
        # 같은 내용의 파일이 이미 그 달 폴더에 있으면 이름이 달라도 건너뜀
        r = m.do_import([self.src], "202610", reindex=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["copied"], 0)
        # 새 달 폴더로는 복사되고, 브라우저가 붙인 " (1)" 은 떼고, 원본은 남는다
        r = m.do_import([self.src], "202611", reindex=False)
        self.assertEqual(r["copied"], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "202611 메일", "외부메일함.zip")))
        self.assertTrue(os.path.isfile(self.src))
        m.run_index()
        self.assertEqual(m.facets()["unique"], self.total)

    def test_auto_import(self):
        old = time.time() - 120
        os.utime(self.src, (old, old))
        other = os.path.join(self.dl, "회의자료.zip")      # 메일 백업이 아닌 zip 은 무시
        shutil.copy(self.src, other)
        os.utime(other, (old, old))
        m.CONFIG["auto_import"] = {"enabled": True, "dir": self.dl, "since": "2000-01-01"}
        # 같은 내용이 이미 202610 에 있으므로 복사 0건, 그래도 처리 기록은 남는다
        self.assertEqual(m.auto_import_once(), 0)
        with open(m.AUTO_SEEN_PATH, encoding="utf-8") as f:
            seen = json.load(f)
        self.assertEqual(len(seen), 1)
        self.assertTrue(next(iter(seen)).startswith("외부메일함 (1).zip|"))
        # 새 백업은 복사·반영된다
        fresh = make_demo.make_mail("x@example.com", [ME], [], "11월 새 메일", "본문", [],
                                    make_demo.datetime(2026, 11, 3, 9, 0, tzinfo=make_demo.KST), mid="<nov@t>")
        nov = os.path.join(self.dl, "받은메일함_1.zip")
        with zipfile.ZipFile(nov, "w") as z:
            z.writestr("n.eml", fresh)
        os.utime(nov, (old, old))
        self.assertEqual(m.auto_import_once(), 1)
        wait_index()
        self.assertTrue(os.path.isfile(os.path.join(self.root, "202611 메일", "받은메일함_1.zip")))
        self.assertEqual(m.search({"q": "11월 새 메일"})["total"], 1)
        # 다운로드 중인 파일(방금 바뀐 파일)은 기다린다
        busy = os.path.join(self.dl, "보낸메일함.zip")
        shutil.copy(nov, busy)
        self.assertEqual(m.auto_import_once(), 0)
        m.CONFIG.pop("auto_import")


# ---------------------------------------------------------------- HTTP 보안

class HttpSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fresh_env("http")
        cls.srv = m.Server(("127.0.0.1", 0), m.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_page_and_status(self):
        r, data = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        r, data = self.req("GET", "/api/status")
        self.assertEqual(json.loads(data)["facets"]["count"], m.facets()["count"])

    def test_dns_rebinding_blocked(self):
        r, _ = self.req("GET", "/api/status", {"Host": "evil.example:80"})
        self.assertEqual(r.status, 403)

    def test_post_requires_custom_header(self):
        r, _ = self.req("POST", "/api/reindex")
        self.assertEqual(r.status, 403)

    def test_post_from_other_origin_blocked(self):
        r, _ = self.req("POST", "/api/reindex", {"X-Requested-With": "mailsearch", "Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)

    def test_post_allowed_from_app(self):
        r, data = self.req("POST", "/api/reindex", {"X-Requested-With": "mailsearch",
                                                    "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(r.status, 200)
        wait_index()

    def test_mail_body_has_csp(self):
        mid = m.search({"q": ""})["items"][0]["id"]
        r, _ = self.req("GET", f"/api/mail/{mid}/body")
        self.assertEqual(r.status, 200)
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy") or "")

    def test_unsafe_attachment_is_download_only(self):
        it = m.search({"q": "장애보고", "field": "subject"})["items"][0]
        idx = m.mail_detail(it["id"])["attachments"][0]["index"]
        r, _ = self.req("GET", f"/api/mail/{it['id']}/part/{idx}?inline=1")
        self.assertEqual(r.getheader("Content-Type"), "application/octet-stream")
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")

    def test_import_needs_picked_file(self):
        r, data = self.req("POST", "/api/import", {"X-Requested-With": "mailsearch", "Content-Type": "application/json"},
                           json.dumps({"paths": [os.path.join(ROOT, "README.md")], "month": "202610"}))
        self.assertFalse(json.loads(data)["ok"])


# ---------------------------------------------------------------- 배포 파일 규칙

class DistributionFilesTest(unittest.TestCase):
    def test_bat_files_are_ascii_crlf(self):
        bats = [f for f in os.listdir(ROOT) if f.lower().endswith(".bat")]
        self.assertEqual(len(bats), 4)
        for f in bats:
            with open(os.path.join(ROOT, f), "rb") as fh:
                data = fh.read()
            self.assertTrue(data.isascii(), f"{f}: 한글 등 비ASCII 문자가 있으면 cmd 에서 깨짐")
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f"{f}: CRLF 가 아님")


if __name__ == "__main__":
    unittest.main()
