#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ onebot：OneBot HTTP API 客户端。"""
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




class OneBotError(Exception):
    pass


class OneBotClient(object):
    def __init__(self, cfg):
        self.api_url = cfg["api_url"].rstrip("/")
        self.token = str(cfg.get("token") or "").strip()
        self.timeout = 20

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def call(self, action, params=None):
        url = "%s/%s" % (self.api_url, action.lstrip("/"))
        data = json.dumps(params or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise OneBotError(
                "OneBot 接口 %s 返回 HTTP %s：%s\n请检查 OneBot 服务是否开启、token 是否一致。"
                % (action, exc.code, body or exc.reason)
            )
        except urllib.error.URLError as exc:
            raise OneBotError(
                "无法连接 OneBot HTTP 服务 %s（%s）。\n请确认 NapCat 已启动，且 OneBot HTTP 服务已开启。"
                % (self.api_url, exc.reason)
            )
        except (TimeoutError, OSError) as exc:
            raise OneBotError("OneBot 接口 %s 请求失败：%s" % (action, exc))

        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise OneBotError("OneBot 接口 %s 返回了非 JSON 内容：%s" % (action, raw[:300]))

        status = str(data.get("status") or "")
        retcode = data.get("retcode", 0)
        if status and status != "ok":
            raise OneBotError("OneBot 接口 %s 调用失败：%s" % (action, data.get("message") or data.get("wording") or data))
        if retcode not in (0, None):
            raise OneBotError(
                "OneBot 接口 %s 调用失败（retcode=%s）：%s"
                % (action, retcode, data.get("message") or data.get("wording") or data)
            )
        return data.get("data")

    def get_login_info(self):
        return self.call("get_login_info") or {}

    def get_group_info(self, group_id):
        return self.call("get_group_info", {"group_id": group_id}) or {}

    def get_image(self, file):
        return self.call("get_image", {"file": file}) or {}

    def get_forward_msg(self, forward_id):
        for key in ("id", "message_id"):
            try:
                data = self.call("get_forward_msg", {key: forward_id})
            except OneBotError:
                continue
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for field in ("messages", "message", "content"):
                    messages = data.get(field)
                    if isinstance(messages, list):
                        return messages
        return []

    def get_msg(self, message_id):
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            pass
        return self.call("get_msg", {"message_id": message_id}) or {}

    def get_group_msg_history(self, group_id, count=20):
        data = self.call("get_group_msg_history", {"group_id": group_id, "count": count})
        if isinstance(data, list):
            return data
        return (data or {}).get("messages") or []

    def send_group_msg(self, group_id, message, auto_escape=False):
        params = {"group_id": group_id, "message": message}
        if auto_escape:
            params["auto_escape"] = True
        return self.call("send_group_msg", params) or {}

__all__ = [name for name in globals() if not name.startswith("__")]
