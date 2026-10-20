"""阅读疑难本地接收服务测试：锚点编解码、回跳链接、投递入库、HTTP 往返。"""

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import question_server as qs
from db import Database


class AnchorCodecTests(unittest.TestCase):
    def test_roundtrip_dict(self):
        anchor = {"q": "机器学习", "p": "监督", "s": "。", "o": 42}
        token = qs.encode_anchor(anchor)
        self.assertTrue(token)
        self.assertNotIn("=", token)
        self.assertNotIn("+", token)
        self.assertNotIn("/", token)
        self.assertEqual(qs.decode_anchor(token), anchor)

    def test_empty_anchor_encodes_to_empty(self):
        self.assertEqual(qs.encode_anchor(None), "")
        self.assertEqual(qs.encode_anchor({}), "")
        self.assertEqual(qs.decode_anchor(""), {})

    def test_decode_garbage_returns_empty_dict(self):
        self.assertEqual(qs.decode_anchor("!!!not base64!!!"), {})


class JumpUrlTests(unittest.TestCase):
    ANCHOR = {"q": "hello", "o": 3}

    def test_appends_fragment_when_none(self):
        url = qs.build_jump_url("https://example.com/a?b=1", self.ANCHOR)
        self.assertTrue(url.startswith("https://example.com/a?b=1#oaq="))
        token = url.split("#oaq=", 1)[1]
        self.assertEqual(qs.decode_anchor(token), self.ANCHOR)

    def test_preserves_existing_fragment(self):
        url = qs.build_jump_url("https://example.com/a#sec-3", self.ANCHOR)
        self.assertTrue(url.startswith("https://example.com/a#sec-3&oaq="))

    def test_replaces_stale_oaq_fragment(self):
        """同一篇文章被反复标记时，链接里只会留下最新那一次的锚点。"""
        first = qs.build_jump_url("https://example.com/a", {"q": "一"})
        second = qs.build_jump_url(first, {"q": "二"})
        self.assertEqual(second.count("oaq="), 1)
        token = second.split("#oaq=", 1)[1]
        self.assertEqual(qs.decode_anchor(token), {"q": "二"})

    def test_drops_stale_oaq_but_keeps_router_fragment(self):
        for stale in ("https://example.com/a#sec-3&oaq=STALE",
                      "https://example.com/a#oaq=STALE&sec-3"):
            url = qs.build_jump_url(stale, self.ANCHOR)
            self.assertTrue(url.startswith("https://example.com/a#sec-3&oaq="))
            self.assertNotIn("STALE", url)

    def test_empty_anchor_returns_url_unchanged(self):
        self.assertEqual(qs.build_jump_url("https://e.com/x#y", None),
                         "https://e.com/x#y")
        self.assertEqual(qs.build_jump_url("https://e.com/x", {}),
                         "https://e.com/x")

    def test_empty_url_returns_empty(self):
        self.assertEqual(qs.build_jump_url("", self.ANCHOR), "")
        self.assertEqual(qs.build_jump_url("   ", self.ANCHOR), "")


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Database(Path(self.temp_dir.name) / "q.db")
        self.addCleanup(self.db.close)
        self.added = []
        self.server = qs.QuestionServer(self.db, on_added=self.added.append)

    def test_ingest_stores_fields_and_returns_id(self):
        qid = self.server._ingest({
            "url": "https://x.com/p", "title": "标题", "quote": "不懂的句子",
            "question": "为什么？", "category": "算法", "priority": 2,
            "note": "备注",
            "anchor": {"q": "不懂的句子", "p": "前", "s": "后", "o": 7},
        })
        row = self.db.get_question(qid)
        self.assertEqual(row["url"], "https://x.com/p")
        self.assertEqual(row["quote"], "不懂的句子")
        self.assertEqual(row["question"], "为什么？")
        self.assertEqual(row["category"], "算法")
        self.assertEqual(row["priority"], 2)
        self.assertEqual(row["note"], "备注")
        self.assertEqual(json.loads(row["anchor"])["o"], 7)
        self.assertEqual(self.added, [qid])

    def test_ingest_blank_returns_none(self):
        self.assertIsNone(self.server._ingest({"quote": "  ", "question": ""}))
        self.assertEqual(self.db.count_questions(), 0)
        self.assertEqual(self.added, [])

    def test_ingest_coerces_priority(self):
        good = self.server._ingest({"question": "q", "priority": "3"})
        self.assertEqual(self.db.get_question(good)["priority"], 3)
        bad = self.server._ingest({"question": "q2", "priority": "abc"})
        self.assertEqual(self.db.get_question(bad)["priority"], 0)

    def test_ingest_accepts_string_anchor(self):
        raw = json.dumps({"q": "z"})
        qid = self.server._ingest({"quote": "z", "anchor": raw})
        self.assertEqual(self.db.get_question(qid)["anchor"], raw)

    def test_ingest_swallows_callback_errors(self):
        def boom(_qid):
            raise RuntimeError("nope")

        server = qs.QuestionServer(self.db, on_added=boom)
        qid = server._ingest({"quote": "仍然入库"})
        self.assertIsNotNone(qid)
        self.assertEqual(self.db.count_questions(), 1)


class HttpRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Database(Path(self.temp_dir.name) / "http.db")
        self.addCleanup(self.db.close)
        self.added = []
        self.server = qs.QuestionServer(self.db, on_added=self.added.append, port=0)
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.stop)
        self.port = self.server._httpd.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def _post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_health_reports_unresolved_count(self):
        self.db.add_question(quote="一")
        status, payload = self._get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["unresolved"], 1)

    def test_post_creates_question_visible_via_get(self):
        status, payload = self._post("/questions", {
            "url": "https://y.com", "quote": "句子", "question": "疑问",
        })
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.added, [payload["id"]])
        _, listing = self._get("/questions?resolved=false")
        self.assertEqual(len(listing["questions"]), 1)
        self.assertEqual(listing["questions"][0]["quote"], "句子")

    def test_get_single_question(self):
        qid = self.db.add_question(quote="单条")
        status, payload = self._get("/question?id=%d" % qid)
        self.assertEqual(status, 200)
        self.assertEqual(payload["question"]["quote"], "单条")

    def test_post_blank_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/questions", {"quote": "  "})
        self.assertEqual(ctx.exception.code, 400)

    def test_post_invalid_json_is_rejected(self):
        req = urllib.request.Request(self.base + "/questions", data=b"{not json",
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/nope")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
