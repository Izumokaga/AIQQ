#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ webhook：机器人 Webhook HTTP 服务。"""
import argparse
import asyncio
import base64
import gzip
import hashlib
import heapq
import hmac
import html
import json
import logging
import os
import queue
import random
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import wave
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import TimedRotatingFileHandler



from .config import MAX_BODY_BYTES, _as_int, log

def _read_chunked(rfile, max_bytes=MAX_BODY_BYTES):
    """读取 HTTP/1.1 chunked 请求体（NapCat 的 HTTP 客户端使用 chunked）。"""
    chunks = []
    total = 0
    while True:
        line = rfile.readline(1024)
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            size = int(line.split(b";", 1)[0], 16)
        except ValueError:
            raise ValueError("非法的 chunk 长度")
        if size == 0:
            while True:
                trailer = rfile.readline(1024)
                if trailer in (b"", b"\r\n", b"\n"):
                    break
            break
        total += size
        if total > max_bytes:
            raise ValueError("请求体过大")
        chunk = rfile.read(size)
        if len(chunk) != size:
            raise ValueError("chunk 数据不完整")
        chunks.append(chunk)
        rfile.read(2)  # 每个 chunk 末尾的 CRLF
    return b"".join(chunks)


class BotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, bot):
        self.bot = bot
        ThreadingHTTPServer.__init__(self, address, handler)


class WebhookHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "AIQQ/1.0"

    def log_message(self, fmt, *args):
        log.debug("HTTP %s - %s", self.address_string(), fmt % args)

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        bot = self.server.bot
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json(200, bot.stats_snapshot())
        elif path == "/raw-history":
            token = str(bot.cfg["onebot"].get("token") or "")
            if token:
                authorized = hmac.compare_digest(
                    self.headers.get("Authorization") or "", "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            self._send_json(200, bot.raw_history_snapshot())
        elif path == "/":
            body = ("AIQQ is running.\nhealth: http://%s:%d/healthz\n"
                    % (bot.cfg["onebot"]["webhook_host"], bot.cfg["onebot"]["webhook_port"])).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json(404, {"status": "not_found"})

    def do_POST(self):
        bot = self.server.bot
        path = self.path.split("?", 1)[0]
        if path == "/save-context":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                self.rfile.read(min(length, MAX_BODY_BYTES))
            self._send_json(200, bot.save_context_now())
            return
        if path == "/clear-context":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                self.rfile.read(min(length, MAX_BODY_BYTES))
            self._send_json(200, bot.clear_context_snapshots())
            return
        if path in ("/delete-context-user", "/delete-context-group",
                    "/delete-professional-context-user"):
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(min(length, MAX_BODY_BYTES)) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            except ValueError:
                self._send_json(400, {"status": "bad_json"})
                return
            if path == "/delete-context-user":
                user_id = _as_int(payload.get("user_id"), 0)
                if user_id <= 0:
                    self._send_json(400, {"status": "bad_user"})
                    return
                self._send_json(200, bot.delete_user_context(user_id))
            elif path == "/delete-professional-context-user":
                user_id = _as_int(payload.get("user_id"), 0)
                if user_id <= 0:
                    self._send_json(400, {"status": "bad_user"})
                    return
                self._send_json(200, bot.delete_professional_context(user_id))
            else:
                group_id = _as_int(payload.get("group_id"), 0)
                if group_id <= 0:
                    self._send_json(400, {"status": "bad_group"})
                    return
                self._send_json(200, bot.delete_group_context(group_id))
            return
        if path == "/gallery-save":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(min(length, MAX_BODY_BYTES)) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            except ValueError:
                self._send_json(400, {"status": "bad_json"})
                return
            images = payload.get("images") or []
            name = str(payload.get("name") or "").strip()
            if not name or not isinstance(images, list):
                self._send_json(400, {"status": "bad_params"})
                return
            last_error = ""
            for info in images:
                if not isinstance(info, dict):
                    continue
                try:
                    data = read_image_bytes(info, bot.lm.image_max_bytes)
                    item = bot.images.save_bytes(
                        data, name,
                        str(payload.get("description") or ""),
                        {"source": "qq-panel", "user_id": payload.get("user_id")},
                        payload.get("favorite", 50))
                    self._send_json(200, {"ok": True, "item": item})
                    return
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
            self._send_json(400, {"status": "image_failed", "error": last_error})
            return
        if path == "/reload-model":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                self.rfile.read(min(length, MAX_BODY_BYTES))
            if not bot.request_model_reload():
                self._send_json(409, {"status": "already_reloading"})
                return
            self._send_json(200, {"status": "reloading"})
            return
        if path == "/restart":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                self.rfile.read(min(length, MAX_BODY_BYTES))
            self._send_json(200, {"status": "restarting"})
            bot.request_restart()
            return
        if path == "/shutdown":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                self.rfile.read(min(length, MAX_BODY_BYTES))
            self._send_json(200, {"status": "shutting_down"})
            bot.request_shutdown()
            return
        if path == "/wake":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(min(length, MAX_BODY_BYTES)) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            except ValueError:
                payload = {}
            was_sleeping = bot.force_wake(_as_int(payload.get("group_id"), 0))
            self._send_json(200, {
                "status": "awake" if was_sleeping else "not_sleeping",
                "was_sleeping": bool(was_sleeping),
            })
            return
        if path == "/sleep-prep":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(min(length, MAX_BODY_BYTES)) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            except ValueError:
                payload = {}
            self._send_json(200, bot.start_sleep_prep(_as_int(payload.get("group_id"), 0)))
            return
        if path == "/force-sleep":
            token = str(bot.cfg["onebot"].get("token") or "")
            provided = self.headers.get("Authorization") or ""
            if token:
                authorized = hmac.compare_digest(provided, "Bearer " + token)
            else:
                authorized = self.client_address[0] in ("127.0.0.1", "::1")
            if not authorized:
                self._send_json(401, {"status": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(min(length, MAX_BODY_BYTES)) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            except ValueError:
                payload = {}
            self._send_json(200, bot.start_force_sleep(_as_int(payload.get("group_id"), 0)))
            return
        if path != bot.cfg["onebot"]["webhook_path"]:
            self._send_json(404, {"status": "not_found"})
            return

        log.debug("Webhook headers: %s", dict(self.headers))
        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in transfer_encoding:
            try:
                body = _read_chunked(self.rfile)
            except ValueError as exc:
                log.warning("Webhook chunked 读取失败：%s", exc)
                self._send_json(400, {"status": "bad_chunked_body"})
                return
        else:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                self._send_json(400, {"status": "empty_body"})
                return
            if length > MAX_BODY_BYTES:
                self._send_json(413, {"status": "body_too_large"})
                return
            body = self.rfile.read(length)

        secret = bot.cfg["onebot"].get("webhook_secret") or ""
        if secret:
            expected = "sha1=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
            provided = self.headers.get("x-signature") or ""
            if not hmac.compare_digest(expected, provided):
                log.warning("Webhook 签名校验失败，已拒绝该事件。")
                self._send_json(401, {"status": "bad_signature"})
                return

        try:
            event = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            self._send_json(400, {"status": "bad_json"})
            return

        events = event if isinstance(event, list) else [event]
        accepted = 0
        for item in events:
            if isinstance(item, dict) and bot.enqueue(item):
                accepted += 1
        if accepted == 0:
            self._send_json(503, {"status": "busy"})
        else:
            self._send_json(200, {"status": "ok"})

__all__ = [name for name in globals() if not name.startswith("__")]
