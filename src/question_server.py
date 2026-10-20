"""
办公助手 阅读疑难收集模块 — 接收浏览器扩展投递的疑难标注，并生成跳回原文的链接。

对外接口:
  encode_anchor(anchor)                 把锚点字典编码成 URL 片段用的 token
  build_jump_url(url, anchor)           生成带回跳锚点的链接
  QuestionServer(db, on_added, port)    本地 HTTP 接收服务（只监听回环地址）

设计要点:
  - 只监听 127.0.0.1，不对外暴露；端口默认 8765，需与扩展里保持一致。
  - 扩展的 background 通过 host_permissions 直接 POST JSON；服务器仍补 CORS 头，
    方便在页面里手动调试。
  - 锚点以 base64url(JSON) 塞进 URL 的 #oaq= 片段，回跳时不依赖任何回环请求，
    因此在任何网页上都能工作。
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, urlsplit

DEFAULT_PORT = 8765


def encode_anchor(anchor) -> str:
    """把锚点字典编码成 URL 安全的 token；空锚点返回空串。"""
    if not anchor:
        return ""
    raw = json.dumps(anchor, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_anchor(token: str) -> dict:
    """encode_anchor 的逆运算；解析失败时返回空字典。"""
    if not token:
        return {}
    try:
        padded = token + "=" * (-len(token) % 4)
        return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return {}


def build_jump_url(url: str, anchor=None) -> str:
    """在原文链接后附加高亮锚点，供扩展定位并高亮。

    url 已有片段（如路由锚点）时用 & 追加，避免覆盖；没有锚点时原样返回。
    """
    base = (url or "").strip()
    token = encode_anchor(anchor)
    if not base or not token:
        return base
    split = urlsplit(base)
    fragment = f"oaq={token}"
    if split.fragment:
        fragment = f"{split.fragment}&{fragment}"
    return split._replace(fragment=fragment).geturl()


class QuestionServer:
    """本地 HTTP 接收服务：把扩展投递的疑难写入 questions 表。"""

    def __init__(self, db, on_added=None, host: str = "127.0.0.1",
                 port: int = DEFAULT_PORT):
        self.db = db
        self.on_added = on_added
        self.host = host
        self.port = int(port)
        self._httpd = None
        self._thread = None

    # --- 生命周期 ---
    def start(self) -> bool:
        """启动服务，返回是否成功（端口被占用时返回 False）。"""
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port),
                                              self._make_handler())
        except OSError as e:
            print(f"[疑难] 本地接收服务启动失败（端口 {self.port}）：{e}")
            self._httpd = None
            return False
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)
        self._thread.start()
        print(f"[疑难] 本地接收服务已启动：http://{self.host}:{self.port}/")
        return True

    def stop(self):
        """停止服务并释放端口。"""
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
        self._httpd = None
        self._thread = None

    # --- 业务 ---
    def _ingest(self, data: dict):
        """把一条扩展投递的 JSON 存进数据库，返回新记录 id（内容为空返回 None）。"""
        quote = str(data.get("quote") or "").strip()
        question = str(data.get("question") or "").strip()
        if not quote and not question:
            return None
        anchor = data.get("anchor")
        if isinstance(anchor, dict):
            anchor_json = json.dumps(anchor, ensure_ascii=False)
        elif isinstance(anchor, str):
            anchor_json = anchor
        else:
            anchor_json = ""
        try:
            priority = int(data.get("priority") or 0)
        except (TypeError, ValueError):
            priority = 0
        qid = self.db.add_question(
            url=str(data.get("url") or ""),
            title=str(data.get("title") or ""),
            quote=quote,
            question=question,
            category=str(data.get("category") or ""),
            priority=priority,
            note=str(data.get("note") or ""),
            anchor=anchor_json,
        )
        if self.on_added:
            try:
                self.on_added(qid)
            except Exception as e:
                print(f"[疑难] 新增回调失败: {e}")
        return qid

    # --- HTTP ---
    def _make_handler(server):
        outer = server

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def _send_json(self, code: int, payload: dict):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                query = parse_qs(parsed.query)
                if path in ("/", "/health"):
                    return self._send_json(200, {
                        "ok": True,
                        "service": "office-assistant-questions",
                        "unresolved": outer.db.count_questions(),
                    })
                if path == "/questions":
                    resolved = None
                    if "resolved" in query:
                        resolved = query["resolved"][0].lower() in ("1", "true", "yes")
                    return self._send_json(
                        200, {"questions": outer.db.get_questions(resolved=resolved)}
                    )
                if path == "/question":
                    try:
                        qid = int(query.get("id", [""])[0])
                    except (TypeError, ValueError):
                        return self._send_json(400, {"ok": False, "error": "invalid id"})
                    row = outer.db.get_question(qid)
                    if row is None:
                        return self._send_json(404, {"ok": False, "error": "not found"})
                    return self._send_json(200, {"ok": True, "question": row})
                return self._send_json(404, {"ok": False, "error": "unknown path"})

            def do_POST(self):
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path != "/questions":
                    return self._send_json(404, {"ok": False, "error": "unknown path"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except (TypeError, ValueError):
                    length = 0
                raw = self.rfile.read(length) if length else b""
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    return self._send_json(400, {"ok": False, "error": "invalid json"})
                if not isinstance(data, dict):
                    return self._send_json(400, {"ok": False, "error": "invalid body"})
                try:
                    qid = outer._ingest(data)
                except Exception as e:
                    print(f"[疑难] 保存失败: {e}")
                    return self._send_json(500, {"ok": False, "error": str(e)})
                if qid is None:
                    return self._send_json(400, {"ok": False, "error": "empty content"})
                return self._send_json(200, {"ok": True, "id": qid})

        return _Handler
