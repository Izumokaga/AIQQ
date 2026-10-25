#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_bridge：上下文、图库与睡眠桥接。"""
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

class PanelBridgeMixin:
    def raw_history(self):
        config = self._config()
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/raw-history" % port,
            method="GET",
            headers={**({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise PanelError("读取历史模型输出失败：%s" % exc)

    def save_context(self):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            raise PanelError("机器人未运行，无法保存内存中的上下文")
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/save-context" % port,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise PanelError("保存上下文失败：%s" % exc)

    def _post_bot_context_action(self, path, payload, label):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            raise PanelError("机器人未运行，无法%s" % label)
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s%s" % (port, path),
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise PanelError("%s失败：%s" % (label, exc))

    def save_gallery_image(self, name, description, favorite, images):
        return self._post_bot_context_action(
            "/gallery-save",
            {"name": name, "description": description,
             "favorite": favorite, "images": images},
            "存入图库")

    def delete_user_context(self, user_id):
        return self._post_bot_context_action(
            "/delete-context-user", {"user_id": int(user_id)}, "删除本用户上下文")

    def delete_group_context(self, group_id):
        return self._post_bot_context_action(
            "/delete-context-group", {"group_id": int(group_id)}, "删除本群上下文")

    def delete_professional_context(self, user_id):
        return self._post_bot_context_action(
            "/delete-professional-context-user", {"user_id": int(user_id)},
            "删除本用户专业模式上下文")

    def delete_all_contexts(self):
        return self.clear_context_snapshots()

    def clear_context_snapshots(self):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            raise PanelError("机器人未运行，无法清空上下文快照")
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/clear-context" % port,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise PanelError("清空上下文快照失败：%s" % exc)

    def wake_sleep(self, group_id=0):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            raise PanelError("机器人未运行，无法结束睡眠")
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/wake" % port,
            data=json.dumps({"group_id": int(group_id or 0)}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise PanelError("结束睡眠失败：%s" % exc)

    def sleep_prep(self, group_id=0):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            raise PanelError("机器人未运行，无法触发睡眠预备发言")
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/sleep-prep" % port,
            data=json.dumps({"group_id": int(group_id or 0)}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise PanelError("触发睡眠预备发言失败：%s" % exc)

    def force_sleep(self, group_id=0):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            raise PanelError("机器人未运行，无法强制睡眠")
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/force-sleep" % port,
            data=json.dumps({"group_id": int(group_id or 0)}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise PanelError("强制睡眠失败：%s" % exc)

__all__ = [name for name in globals() if not name.startswith("__")]
