#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_contexts：上下文浏览、配置摘要与监控状态。"""
import base64
import gzip
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from . import panel_state
import setup as init_setup  # noqa: E402



from .panel_common import PanelError

class PanelContextsMixin:
    def _context_dir(self):
        config = self._config()
        directory = (config.get("lm_studio") or {}).get("context_save_dir") or "context"
        if not os.path.isabs(directory):
            directory = os.path.join(self.root, directory)
        return os.path.abspath(directory)

    def _context_path(self, filename):
        relative = str(filename or "").replace("\\", "/").strip("/")
        parts = relative.split("/")
        if (not relative or not relative.endswith(".json.gz")
                or any(part in ("", ".", "..") for part in parts)):
            raise PanelError("不安全的上下文文件名")
        path = os.path.abspath(os.path.join(self._context_dir(), *parts))
        root = os.path.abspath(self._context_dir())
        if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
            raise PanelError("上下文文件不存在")
        return path

    def _context_meta(self, relative):
        path = os.path.join(self._context_dir(), *str(relative).replace("\\", "/").split("/"))
        try:
            stat = os.stat(path)
        except OSError:
            return None
        parts = str(relative).replace("\\", "/").split("/")
        group_from_path = parts[0] if len(parts) >= 3 and parts[0].isdigit() else ""
        user_from_path = parts[1] if len(parts) >= 3 and parts[1].isdigit() else ""
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            return {
                "file": relative,
                "group_id": group_from_path,
                "user_id": user_from_path,
                "saved_at": "",
                "messages": 0,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(exc),
            }
        messages = payload.get("messages") or payload.get("entries") if isinstance(payload, dict) else []
        if not isinstance(messages, list):
            messages = []
        return {
            "file": relative,
            "group_id": payload.get("group_id") or group_from_path,
            "user_id": payload.get("user_id") or user_from_path,
            "saved_at": payload.get("saved_at") or "",
            "messages": len(messages),
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }

    def contexts(self):
        directory = self._context_dir()
        if not os.path.isdir(directory):
            return []
        records = []
        for root, _, names in os.walk(directory):
            for name in names:
                if not name.endswith(".json.gz"):
                    continue
                relative = os.path.relpath(os.path.join(root, name), directory).replace("\\", "/")
                meta = self._context_meta(relative)
                if meta:
                    records.append(meta)
        latest = {}
        for record in records:
            key = (str(record.get("group_id")), str(record.get("user_id")))
            rank = (record.get("mtime") or "", record.get("file") or "")
            if key not in latest or rank > latest[key]:
                latest[key] = rank
        for record in records:
            key = (str(record.get("group_id")), str(record.get("user_id")))
            record["latest"] = latest.get(key) == (record.get("mtime") or "", record.get("file") or "")
        records.sort(key=lambda item: (item.get("mtime") or "", item.get("file") or ""), reverse=True)
        return records

    def context_detail(self, filename):
        path = self._context_path(filename)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            raise PanelError("读取上下文失败：%s" % exc)
        messages = payload.get("messages") if isinstance(payload, dict) else []
        if not isinstance(messages, list):
            messages = []
        entries = payload.get("entries") if isinstance(payload, dict) else []
        if isinstance(entries, list) and entries and not messages:
            rendered = []
            for item in entries:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                text = str(item.get("content") or "")
                mention_names = [
                    "%s(QQ%s)" % (m.get("name") or "成员", m.get("user_id") or "")
                    for m in (item.get("mentions") or [])
                    if isinstance(m, dict)
                ]
                if role != "assistant" and mention_names:
                    text += "\n[被@] " + "、".join(mention_names)
                rendered.append({
                    "role": role,
                    "text": text,
                    "nickname": ("迪普西克" if role == "assistant"
                                 else (item.get("name") or "用户")),
                })
            messages = rendered
        return {
            "file": str(filename).replace("\\", "/"),
            "group_id": payload.get("group_id"),
            "user_id": payload.get("user_id"),
            "saved_at": payload.get("saved_at"),
            "messages": messages,
        }

    def delete_context(self, filename):
        path = self._context_path(filename)
        os.remove(path)
        return {"ok": True, "file": os.path.basename(path)}

    def _config_summary(self, config):
        lm = config.get("lm_studio") or {}
        qq = config.get("qq") or {}
        return {
            "self_id": qq.get("self_id"),
            "groups": qq.get("groups") or [],
            "model": lm.get("model") or "自动",
            "persona_mode": lm.get("persona_mode") or "adaptive",
            "persona_file": lm.get("persona_file") or "",
        }

    def _bot_health(self, config):
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        url = "http://127.0.0.1:%s/healthz" % port
        try:
            with urllib.request.urlopen(url, timeout=0.8) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            return {"online": True, "url": url, "data": data}
        except Exception:  # noqa: BLE001
            return {"online": False, "url": url, "data": {}}

    def monitor_state(self):
        config = self._config()
        return {
            "bot": self._bot_health(config),
            "config": self._config_summary(config),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def state(self):
        files = []
        for name in self.ALLOWED:
            path = os.path.join(self.root, name)
            try:
                stat = os.stat(path)
                files.append({
                    "name": name,
                    "editable": name in self.EDITABLE,
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
            except OSError:
                files.append({
                    "name": name,
                    "editable": name in self.EDITABLE,
                    "size": 0,
                    "mtime": "",
                })
        config = self._config()
        return {
            "files": files,
            "config": self._config_summary(config),
            "images": self._images(),
            "bot": self._bot_health(config),
        }


__all__ = [name for name in globals() if not name.startswith("__")]
