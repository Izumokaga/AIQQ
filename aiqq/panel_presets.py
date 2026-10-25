#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_presets：人设/初始化预设与配置应用。"""
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

class PanelPresetsMixin:
    def _preset_path(self, name):
        name = str(name or "").strip()
        if name.lower().endswith(".txt"):
            name = name[:-4].strip()
        if not re.fullmatch(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff ._-]{0,60}", name):
            raise PanelError("预设名称不合法")
        return name, os.path.join(self.root, "persona", name + ".txt")

    def list_persona_presets(self):
        directory = os.path.join(self.root, "persona")
        try:
            names = [name for name in os.listdir(directory)
                     if name.lower().endswith(".txt")
                     and os.path.isfile(os.path.join(directory, name))]
        except OSError:
            names = []
        presets = []
        for name in sorted(names, key=str.casefold):
            path = os.path.join(directory, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            presets.append({"name": name[:-4], "file": name, "size": size})
        return presets

    def read_persona_preset(self, name):
        clean, path = self._preset_path(name)
        if not os.path.isfile(path):
            raise PanelError("预设不存在：%s" % clean)
        try:
            return {"name": clean, "content": init_setup.read_text(path)}
        except OSError as exc:
            raise PanelError("读取预设失败：%s" % exc)

    def write_persona_preset(self, name, content):
        clean, path = self._preset_path(name)
        if not isinstance(content, str):
            raise PanelError("缺少 content")
        if len(content.encode("utf-8")) > MAX_BODY:
            raise PanelError("预设内容超过 2MB 限制")
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        if not content.endswith("\n"):
            content += "\n"
        tmp = path + ".panel.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, path)
        return {"name": clean, "path": path}

    def _init_preset_path(self, name):
        name = str(name or "").strip()
        if name.lower().endswith(".txt"):
            name = name[:-4].strip()
        if not re.fullmatch(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff ._-]{0,60}", name):
            raise PanelError("预设名称不合法")
        return name, os.path.join(self.root, "init", name + ".txt")

    def list_init_presets(self):
        directory = os.path.join(self.root, "init")
        try:
            names = [name for name in os.listdir(directory)
                     if name.lower().endswith(".txt")
                     and os.path.isfile(os.path.join(directory, name))]
        except OSError:
            names = []
        presets = []
        for name in sorted(names, key=str.casefold):
            path = os.path.join(directory, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            presets.append({"name": name[:-4], "file": name, "size": size})
        return presets

    def read_init_preset(self, name):
        clean, path = self._init_preset_path(name)
        if not os.path.isfile(path):
            raise PanelError("预设不存在：%s" % clean)
        try:
            return {"name": clean, "content": init_setup.read_text(path)}
        except OSError as exc:
            raise PanelError("读取预设失败：%s" % exc)

    def write_init_preset(self, name, content):
        clean, path = self._init_preset_path(name)
        if not isinstance(content, str):
            raise PanelError("缺少 content")
        if len(content.encode("utf-8")) > MAX_BODY:
            raise PanelError("预设内容超过 2MB 限制")
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        if not content.endswith("\n"):
            content += "\n"
        tmp = path + ".panel.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, path)
        return {"name": clean, "path": path}

    def has_init_preset(self, name):
        try:
            self.read_init_preset(name)
            return True
        except PanelError:
            return False

    def _init_template_content(self):
        for path in (os.path.join(self.root, "init", "空模板.txt"),
                     os.path.join(self.root, "init.example.txt")):
            if os.path.isfile(path):
                return init_setup.read_text(path)
        raise PanelError("找不到 init/空模板.txt")

    def activate_persona_preset(self, name):
        """写入人格文件；若存在同名初始化预设，也写入 init.txt 并应用/重启。"""
        persona = self.read_persona_preset(name)
        self.write("persona.txt", persona["content"])
        result = {
            "ok": True,
            "name": persona["name"],
            "persona_content": persona["content"],
            "init": False,
            "applied": None,
            "restart": None,
        }
        try:
            init_preset = self.read_init_preset(name)
        except PanelError:
            init_preset = None
        if init_preset is None:
            return result
        init_content = init_preset["content"]
        if (not init_content.strip()
                or not re.search(r"(?m)^\s*groups\s*=\s*[^\s#]", init_content)):
            template = self._init_template_content()
            self.write("init.txt", template)
            result["template_loaded"] = True
            result["warning"] = "同名初始化预设为空，已载入空模板，请前往初始化参数修改"
            return result
        self.write("init.txt", init_content)
        result["init"] = True
        result["init_content"] = init_preset["content"]
        applied = self.apply_init()
        result["applied"] = applied
        if not applied.get("ok"):
            raise PanelError("人格已载入，但初始化应用失败：%s" % (
                applied.get("output") or "setup.py 返回失败"))
        result["restart"] = self.restart_bot()
        result["message"] = "已载入人格和同名初始化数据，机器人正在重启"
        return result

    def apply_init(self):
        setup_path = os.path.join(self.root, "setup.py")
        try:
            proc = subprocess.run(
                [sys.executable, setup_path, "--no-napcat", "--no-test"],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise PanelError("setup.py 执行超时")
        output = (proc.stdout or "") + (proc.stderr or "")
        return {"ok": proc.returncode == 0, "code": proc.returncode, "output": output[-8000:]}

__all__ = [name for name in globals() if not name.startswith("__")]
