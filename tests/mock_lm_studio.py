#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试用的 LM Studio 假服务（OpenAI 兼容接口），只依赖标准库。"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - 关闭测试噪音
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/v1/models", "/models"):
            self._send(200, {
                "object": "list",
                "data": [{"id": "mock-model", "object": "model", "owned_by": "mock"}],
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            self._send(400, {"error": "bad json"})
            return

        mock = self.server.mock
        mock.record(request)

        user_text = ""
        for message in reversed(request.get("messages") or []):
            if message.get("role") == "user":
                user_text = str(message.get("content") or "")
                break

        if "ERROR" in user_text:
            self._send(500, {"error": {"message": "mock server error"}})
            return

        reply = mock.reply_for(user_text)
        self._send(200, {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })


class MockLMStudio(object):
    def __init__(self, port=0):
        self.port = port
        self.requests = []
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    def start(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._server.mock = self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="mock-lm", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def record(self, request):
        with self._lock:
            self.requests.append(request)

    @property
    def last_request(self):
        with self._lock:
            return self.requests[-1] if self.requests else None

    @staticmethod
    def reply_for(user_text):
        if "空回复" in user_text:
            return ""
        if "长回复" in user_text:
            return "".join("第%d句。" % (index + 1) for index in range(250))
        if "分条" in user_text:
            return "第一条消息/Enter/第二条消息/ENTER/第三条消息"
        return "收到：「%s」" % user_text[-30:]
