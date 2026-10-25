#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_napcat：NapCat 登录、二维码与状态。"""
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

class PanelNapcatMixin:
    def _napcat_login(self):
        config_path = os.path.join(self.root, "NapCat.Shell", "config", "webui.json")
        try:
            with open(config_path, encoding="utf-8") as fh:
                webui = json.load(fh)
        except (OSError, ValueError) as exc:
            raise PanelError("读取 NapCat WebUI 配置失败：%s" % exc)
        host = str(webui.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        port = int(webui.get("port") or 6099)
        token = str(webui.get("token") or "")
        base = "http://%s:%d/api" % (host, port)
        credential = ""
        if token:
            password_hash = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
            login = urllib.request.Request(
                base + "/auth/login",
                data=json.dumps({"hash": password_hash, "totpCode": ""}).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(login, timeout=10) as response:
                    login_data = json.loads(response.read().decode("utf-8", errors="replace"))
                credential = str((login_data.get("data") or {}).get("Credential") or "")
            except Exception as exc:  # noqa: BLE001
                raise PanelError("NapCat WebUI 登录失败：%s" % exc)
            if not credential:
                raise PanelError("NapCat WebUI 登录失败：未返回凭据（可能启用了 2FA）")
        return base, credential

    def _napcat_qr_payload(self):
        base, credential = self._napcat_login()
        endpoint = base + "/QQLogin/GetQQLoginQrcode"
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        request = urllib.request.Request(
            endpoint, data=b"{}", method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise PanelError("获取 NapCat 登录二维码失败：%s" % exc)

    def napcat_status(self):
        config = self._config()
        onebot = config.get("onebot") or {}
        api = str(onebot.get("api_url") or "http://127.0.0.1:3000").rstrip("/")
        token = str(onebot.get("token") or "")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            request = urllib.request.Request(
                api + "/get_login_info", data=b"{}", method="POST", headers=headers)
            with urllib.request.urlopen(request, timeout=3) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            user_id = (data.get("data") or {}).get("user_id")
            if data.get("retcode") == 0 and user_id:
                return {
                    "running": True, "logged_in": True,
                    "qq": int(user_id),
                    "nickname": str((data.get("data") or {}).get("nickname") or ""),
                }
        except Exception:  # noqa: BLE001
            pass
        try:
            data = self._napcat_qr_payload()
            qrcode = self._find_qr_value(data)
            if qrcode:
                return {
                    "running": True, "logged_in": False,
                    "qrcode": qrcode,
                }
            message = str(data.get("message") or "")
            if "logined" in message.lower() or "登录" in message:
                return {"running": True, "logged_in": True, "qq": 0, "nickname": ""}
            return {
                "running": True, "logged_in": False,
                "error": message or "NapCat 未返回二维码",
            }
        except PanelError as exc:
            return {"running": False, "logged_in": False, "error": str(exc)}

    def _admin_napcat_qr(self):
        data = self._napcat_qr_payload()
        qrcode = self._find_qr_value(data)
        if not qrcode:
            message = str(data.get("message") or "")
            if "logined" in message.lower():
                raise PanelError("NapCat 已经登录")
            raise PanelError("NapCat 没有返回登录二维码：%s" % (message or "未知错误"))
        return qrcode

    def _find_qr_value(self, data):
        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).lower() == "qrcode" and isinstance(value, str) and value.strip():
                    return value.strip()
            for value in data.values():
                found = self._find_qr_value(value)
                if found:
                    return found
        elif isinstance(data, list):
            for value in data:
                found = self._find_qr_value(value)
                if found:
                    return found
        return ""


__all__ = [name for name in globals() if not name.startswith("__")]
