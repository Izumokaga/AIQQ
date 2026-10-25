#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试用的 NapCat / OneBot 11 假服务，只依赖标准库。"""

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _send(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        action = self.path.split("?", 1)[0].strip("/")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            params = json.loads(self.rfile.read(length).decode("utf-8", errors="replace")) if length else {}
        except json.JSONDecodeError:
            params = {}
        mock = self.server.mock
        data = mock.handle(action, params)
        self._send({"status": "ok", "retcode": 0, "data": data, "message": ""})


class MockOneBot(object):
    def __init__(self, port=0, self_id=10001):
        self.port = port
        self.self_id = self_id
        self.sends = []
        self.history = []
        self.group_name = "测试群"
        self._lock = threading.Lock()
        self._server = None

    def start(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._server.mock = self
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, name="mock-onebot", daemon=True).start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def handle(self, action, params):
        if action == "get_login_info":
            return {"user_id": self.self_id, "nickname": "测试机器人"}
        if action == "get_group_info":
            return {"group_id": params.get("group_id"), "group_name": self.group_name}
        if action == "get_group_msg_history":
            with self._lock:
                return {"messages": list(self.history)}
        if action == "send_group_msg":
            with self._lock:
                self.sends.append(params)
                return {"message_id": 9000 + len(self.sends)}
        return {}

    def snapshot(self):
        with self._lock:
            return list(self.sends)


def post_event(webhook_url, event, timeout=5):
    data = json.dumps(event, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def make_group_event(message_id, user_id, message, group_id=888888, self_id=10001, nickname="群友"):
    return {
        "time": int(time.time()),
        "self_id": self_id,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": message_id,
        "message_seq": message_id,
        "group_id": group_id,
        "user_id": user_id,
        "message": message,
        "raw_message": "",
        "font": 0,
        "sender": {"user_id": user_id, "nickname": nickname, "card": "", "role": "member"},
    }
