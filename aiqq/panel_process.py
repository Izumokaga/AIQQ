#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_process：NapCat、机器人与面板进程控制。"""
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

class PanelProcessMixin:
    def _napcat_webui_url(self):
        try:
            with open(os.path.join(self.root, "NapCat.Shell", "config", "webui.json"),
                      encoding="utf-8") as fh:
                webui = json.load(fh)
        except (OSError, ValueError):
            return ""
        host = str(webui.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        port = int(webui.get("port") or 6099)
        token = str(webui.get("token") or "")
        url = "http://%s:%d/webui" % (host, port)
        if token:
            url += "?token=" + quote(token, safe="")
        return url

    def _open_napcat_webui_when_ready(self):
        url = self._napcat_webui_url()
        if not url:
            return
        base = url.split("/webui", 1)[0] + "/"
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base, timeout=1) as response:
                    response.read(1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(1)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    def start_script(self, mode="all"):
        if os.name != "nt":
            raise PanelError("一键启动目前只支持 Windows")
        scripts = {
            "all": "start_all.bat",
            "bot": "start_bot.bat",
            "napcat": "start_napcat.bat",
        }
        mode = str(mode or "all").strip().lower()
        if mode not in scripts:
            raise PanelError("未知启动模式")
        script = os.path.join(self.root, scripts[mode])
        if not os.path.isfile(script):
            raise PanelError("找不到启动脚本：%s" % scripts[mode])
        if mode == "all" and not os.path.isfile(os.path.join(self.root, "config.json")):
            raise PanelError("还没有 config.json，请先保存并应用初始化参数")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            ["cmd.exe", "/c", script],
            cwd=self.root,
            close_fds=True,
            creationflags=flags,
        )
        if mode in ("all", "napcat"):
            threading.Thread(
                target=self._open_napcat_webui_when_ready,
                name="napcat-webui-open", daemon=True).start()
        return {"ok": True, "mode": mode, "script": scripts[mode], "pid": proc.pid}

    def restart_bot(self):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            result = self.start_script("bot")
            return {"ok": True, "message": "机器人未在线，已启动新进程", **result}
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/restart" % port,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read()
        except Exception as exc:  # noqa: BLE001
            raise PanelError("机器人重启请求失败：%s" % exc)
        return {"ok": True, "message": "机器人正在重启"}

    def stop_bot(self):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            return {"ok": True, "message": "机器人已经停止"}
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/shutdown" % port,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
        except Exception as exc:  # noqa: BLE001
            pid = (health.get("data") or {}).get("pid")
            if pid:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                raise PanelError("关闭机器人失败：%s" % exc)
        return {"ok": True, "message": "机器人已关闭，NapCat 保持运行"}

    def shutdown_panel(self):
        threading.Thread(
            target=self._shutdown_panel_process, name="panel-shutdown", daemon=True).start()
        return {"ok": True, "message": "控制面板正在关闭"}

    def shutdown_all(self):
        result = {}
        try:
            result["bot"] = self.stop_bot()
        except Exception as exc:  # noqa: BLE001
            result["bot"] = {"ok": False, "error": str(exc)}
        self._kill_napcat()
        self._kill_tts()
        result["ok"] = True
        result["message"] = "已关闭机器人、NapCat 和 TTS；面板和用户 QQ 保持运行"
        return result

    def _shutdown_panel_process(self):
        time.sleep(0.5)
        runtime = panel_state.PANEL_RUNTIME
        if runtime is not None:
            try:
                runtime.shutdown()
            except Exception:  # noqa: BLE001
                pass
        os._exit(0)

    def _kill_napcat(self):
        if os.name != "nt":
            return
        for image in ("NapCatWinBootMain.exe", "NapCatWinBootHook.exe"):
            try:
                subprocess.run(["taskkill", "/F", "/IM", image],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=8)
            except Exception:
                pass

    def reload_model(self):
        config = self._config()
        health = self._bot_health(config)
        if not health.get("online"):
            result = self.start_script("bot")
            return {"ok": True, "message": "机器人未在线，已启动新进程", **result}
        port = (config.get("onebot") or {}).get("webhook_port") or 8080
        token = str((config.get("onebot") or {}).get("token") or "")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/reload-model" % port,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return {"ok": True, "message": "模型正在重载中"}
            raise PanelError("模型重载请求失败：HTTP %s" % exc.code)
        except Exception as exc:  # noqa: BLE001
            raise PanelError("模型重载请求失败：%s" % exc)
        return {"ok": True, "message": "正在重启 LM Studio 模型并重载数据"}

__all__ = [name for name in globals() if not name.startswith("__")]
