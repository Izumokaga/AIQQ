#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_config：面板设置、文件读写与备份。"""
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


from .panel_common import MAX_BODY, PanelError

class PanelConfigMixin:
    def _panel_settings_data(self):
        settings = {"lan_enabled": False, "port": 8090, "password": ""}
        if os.path.isfile(self.settings_path):
            try:
                with open(self.settings_path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    settings["lan_enabled"] = bool(raw.get("lan_enabled"))
                    settings["port"] = int(raw.get("port") or 8090)
                    settings["password"] = str(raw.get("password") or "")
            except (OSError, ValueError, TypeError):
                pass
        elif os.path.isfile(self.password_path):
            # 兼容旧的 panel_password.txt：有密码文件即视为已开启局域网
            try:
                with open(self.password_path, encoding="utf-8") as fh:
                    password = fh.readline().strip()
                if password:
                    settings["lan_enabled"] = True
                    settings["password"] = password
            except OSError:
                pass
        settings["port"] = max(1, min(65535, int(settings["port"])))
        return settings

    def panel_settings(self):
        data = self._panel_settings_data()
        lan_ip = panel_state._lan_ip() if data["lan_enabled"] else ""
        port = data["port"]
        return {
            "lan_enabled": data["lan_enabled"],
            "port": port,
            "password_set": bool(data["password"]),
            "host": "0.0.0.0" if data["lan_enabled"] else "127.0.0.1",
            "local_url": "http://127.0.0.1:%d" % port,
            "lan_ip": lan_ip,
            "lan_url": ("http://%s:%d" % (lan_ip, port)) if lan_ip and data["lan_enabled"] else "",
        }

    def save_panel_settings(self, lan_enabled, password, port):
        if isinstance(lan_enabled, str):
            lan_enabled = lan_enabled.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
        current = self._panel_settings_data()
        new_password = current["password"] if password is None else str(password)
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise PanelError("端口必须是数字")
        if port < 1 or port > 65535:
            raise PanelError("端口范围应为 1~65535")
        if "\n" in new_password or "\r" in new_password:
            raise PanelError("密码不能包含换行")
        if lan_enabled and not new_password:
            raise PanelError("开启局域网访问前必须设置密码")
        payload = {
            "lan_enabled": bool(lan_enabled),
            "port": port,
            "password": new_password,
        }
        tmp = self.settings_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, self.settings_path)
        except OSError as exc:
            raise PanelError("保存面板设置失败：%s" % exc)
        return self.panel_settings()

    def _path(self, name):
        if name not in self.ALLOWED:
            raise PanelError("不允许访问该文件")
        return os.path.join(self.root, name)

    def open_folder(self, name):
        roots = {
            "img": os.path.join(self.root, "img"),
            "context": os.path.join(self.root, "context"),
        }
        if name not in roots:
            raise PanelError("不允许打开该文件夹")
        path = os.path.abspath(roots[name])
        if not os.path.isdir(path):
            raise PanelError("文件夹不存在：%s" % path)
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            raise PanelError("打开文件夹失败：%s" % exc)
        return {"ok": True, "path": path}

    def read(self, name):
        path = self._path(name)
        if not os.path.isfile(path):
            return ""
        try:
            return init_setup.read_text(path)
        except OSError as exc:
            raise PanelError("读取失败：%s" % exc)

    def _validate(self, name, content):
        if len(content.encode("utf-8")) > MAX_BODY:
            raise PanelError("内容超过 2MB 限制")

    def _backup(self, name):
        path = self._path(name)
        if not os.path.isfile(path):
            return ""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(self.backup_dir, "%s.%s.bak" % (name, stamp))
        counter = 2
        while os.path.exists(target):
            target = os.path.join(self.backup_dir, "%s.%s_%d.bak" % (name, stamp, counter))
            counter += 1
        shutil.copy2(path, target)
        return target

    def write(self, name, content):
        if name not in self.EDITABLE:
            raise PanelError("该文件只读")
        path = self._path(name)
        self._validate(name, content)
        if not content.endswith("\n"):
            content += "\n"
        backup = self._backup(name)
        tmp = path + ".panel.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, path)
        return {"backup": backup, "path": path}

__all__ = [name for name in globals() if not name.startswith("__")]
