#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ 本地网页控制面板。

只依赖 Python 标准库：
    python panel.py
    python panel.py --host 0.0.0.0 --password 你的密码

可编辑：
    persona.txt   人设，保存后机器人下一条回复自动重新加载
    init.txt      初始化参数，可保存并运行 setup.py 应用
可管理：
    img/          图片名称、描述、删除（机器人热加载索引）
    context/      上下文快照查看、删除
只读：
    image_index.txt
"""

import argparse
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _ensure_std_streams(name):
    """pythonw 下 stdout/stderr 为 None，写到 logs 避免 print 崩溃。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    directory = os.path.join(BASE_DIR, "logs")
    os.makedirs(directory, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(os.path.join(directory, "%s_stdout.log" % name),
                          "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(os.path.join(directory, "%s_stderr.log" % name),
                          "a", encoding="utf-8", buffering=1)


_ensure_std_streams("panel")
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import setup as init_setup  # noqa: E402

PANEL_PASSWORD = ""
PANEL_RUNTIME = None
PANEL_SCREENSHOT_TOKEN = ""
MAX_BODY = 2 * 1024 * 1024


class PanelError(Exception):
    pass


class PanelStore(object):
    EDITABLE = ("persona.txt", "init.txt")
    READONLY = ("image_index.txt",)
    ALLOWED = EDITABLE + READONLY

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.backup_dir = os.path.join(self.root, "backups", "panel")
        self.settings_path = os.path.join(self.root, "panel_settings.json")
        self.password_path = os.path.join(self.root, "panel_password.txt")
        os.makedirs(self.backup_dir, exist_ok=True)

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
        lan_ip = _lan_ip() if data["lan_enabled"] else ""
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
        threading.Thread(
            target=self._shutdown_panel_process, name="panel-shutdown", daemon=True).start()
        result["ok"] = True
        result["message"] = "正在关闭面板、机器人和 NapCat；QQ 保持运行"
        return result

    def _shutdown_panel_process(self):
        time.sleep(0.5)
        runtime = PANEL_RUNTIME
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

    def _config(self):
        try:
            with open(os.path.join(self.root, "config.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # ---------------------------- 控制面板 QQ 指令 ---------------------------- #
    def _admin_allowed(self, event):
        config = self._config()
        qq = config.get("qq") or {}
        users = set()
        groups = set()
        for value in qq.get("admin_users") or []:
            try:
                users.add(int(value))
            except (TypeError, ValueError):
                pass
        for value in qq.get("admin_groups") or []:
            try:
                groups.add(int(value))
            except (TypeError, ValueError):
                pass
        if not users and not groups:
            return True
        try:
            user_id = int(event.get("user_id") or 0)
            group_id = int(event.get("group_id") or 0)
        except (TypeError, ValueError):
            return False
        return user_id in users or (group_id > 0 and group_id in groups)

    def enqueue_admin_command(self, payload):
        if not isinstance(payload, dict):
            return {"ok": False, "status": "bad_body"}
        event = {
            "message_type": payload.get("message_type"),
            "group_id": payload.get("group_id") or 0,
            "user_id": payload.get("user_id") or 0,
            "message_id": payload.get("message_id") or "",
            "images": payload.get("images") or [],
        }
        command = str(payload.get("command") or "").strip().lower()
        is_admin = self._admin_allowed(event)
        threading.Thread(
            target=self._run_admin_command, args=(event, command, is_admin),
            name="aiqq-admin-command", daemon=True).start()
        return {"ok": True, "admin": is_admin}

    # 标准面板：所有成员可用；管理员面板：管理员账号或管理员群成员可用。
    STANDARD_COMMANDS = {
        "帮助", "图库", "上下文", "上下文.保存", "上下文.删除本用户上下文",
        "监控", "重启.模型", "睡眠", "预备", "唤醒", "人设预设",
    }
    ADMIN_ONLY_COMMANDS = {
        "上下文.删除本群上下文", "上下文.删除所有上下文", "重启.机器人",
    }

    def _run_admin_command(self, event, command, is_admin=False):
        # 兼容旧指令写法，但帮助只展示标准/管理员面板的正式命令。
        aliases = {
            "保存上下文": "上下文.保存",
            "重启bot": "重启.机器人",
            "重启.bot": "重启.机器人",
        }
        command = aliases.get(command, command)
        # 图库图片指令归到 /控制面板.图库 下；旧写法继续兼容。
        if command.startswith("图库.图片发送."):
            command = "图片发送." + command[len("图库.图片发送."):]
        elif command.startswith("图库.图片存入."):
            command = "图片存入." + command[len("图库.图片存入."):]
        try:
            if command.startswith("上下文.删除指定用户上下文."):
                if not is_admin:
                    self._admin_send_text(
                        event, "无权限使用管理员指令。\n\n" + self._help_text(False))
                    return
                raw_user = command[len("上下文.删除指定用户上下文."):].strip()
                try:
                    user_id = int(raw_user)
                except ValueError:
                    user_id = 0
                if user_id <= 0:
                    self._admin_send_text(
                        event, "用法：/控制面板.上下文.删除指定用户上下文.QQ号")
                    return
                self._admin_send_text(event, "收到")
                self.delete_user_context(user_id)
                return
            if command.startswith("图片发送."):
                name = command[len("图片发送."):].strip()
                if not name:
                    self._admin_send_text(event, "用法：/控制面板.图片发送.图库中图片名")
                    return
                info = self.image_info(name)
                if not info:
                    self._admin_send_text(event, "找不到图库图片：%s" % name)
                    return
                with open(info["path"], "rb") as fh:
                    self._admin_send_image(event, fh.read())
                return
            if command.startswith("图片存入."):
                if not is_admin:
                    self._admin_send_text(
                        event, "无权限使用管理员指令。\n\n" + self._help_text(False))
                    return
                rest = command[len("图片存入."):].strip()
                name_part, sep, favorite = rest.rpartition(".")
                name, sep2, description = name_part.partition(".")
                parts = [name, description, favorite] if sep and sep2 else []
                if len(parts) < 3:
                    self._admin_send_text(
                        event, "用法：/控制面板.图片存入.图片名.描述.喜爱程度")
                    return
                name = parts[0].strip()
                description = parts[1].strip()
                favorite = parts[2].strip() or "50"
                images = event.get("images") or []
                if not name:
                    self._admin_send_text(event, "图片名不能为空")
                    return
                if not images:
                    self._admin_send_text(event, "当前消息没有附带图片")
                    return
                self._admin_send_text(event, "收到")
                result = self.save_gallery_image(name, description, favorite, images)
                item = result.get("item") or {}
                self._admin_send_text(
                    event, "已存入图库：%s（喜爱 %s）" % (
                        item.get("name") or name,
                        item.get("favorite", favorite)))
                return
            if command == "人设预设":
                presets = self.list_persona_presets()
                if not presets:
                    self._admin_send_text(event, "persona 文件夹里还没有预设")
                    return
                lines = ["人设预设（%d）：" % len(presets)]
                lines.extend("· %s" % item.get("name") for item in presets)
                if is_admin:
                    lines.append("管理员可用：/控制面板.载入人设.人设名称")
                self._admin_send_text(event, "\n".join(lines))
                return
            if command.startswith("载入人设."):
                if not is_admin:
                    self._admin_send_text(
                        event, "无权限使用管理员指令。\n\n" + self._help_text(False))
                    return
                name = command[len("载入人设."):].strip()
                try:
                    preset = self.read_persona_preset(name)
                    self.write("persona.txt", preset["content"])
                except PanelError as exc:
                    self._admin_send_text(
                        event, "载入人设失败：%s" % exc)
                    return
                self._admin_send_text(
                    event, "已载入人设：%s" % preset.get("name", name))
                return
            if command not in self.STANDARD_COMMANDS and command not in self.ADMIN_ONLY_COMMANDS:
                self._admin_send_text(
                    event, "未知控制面板指令。\n\n" + self._help_text(is_admin))
                return
            if command in self.ADMIN_ONLY_COMMANDS and not is_admin:
                self._admin_send_text(
                    event, "无权限使用管理员指令。\n\n" + self._help_text(False))
                return
            if command == "帮助":
                self._admin_send_text(event, self._help_text(is_admin))
                return
            if command in ("图库", "上下文", "监控"):
                view = {"图库": "library", "上下文": "contexts", "监控": "monitor"}[command]
                path = self._admin_screenshot(view)
                try:
                    with open(path, "rb") as fh:
                        raw = fh.read()
                    self._admin_send_image(event, raw)
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                return
            if command == "上下文.保存":
                self._admin_send_text(event, "收到")
                self.save_context()
                return
            if command == "上下文.删除本用户上下文":
                self._admin_send_text(event, "收到")
                self.delete_user_context(int(event.get("user_id") or 0))
                return
            if command == "上下文.删除本群上下文":
                if not int(event.get("group_id") or 0):
                    self._admin_send_text(event, "只能在群聊里删除本群上下文")
                    return
                self._admin_send_text(event, "收到")
                self.delete_group_context(int(event.get("group_id") or 0))
                return
            if command == "上下文.删除所有上下文":
                self._admin_send_text(event, "收到")
                self.delete_all_contexts()
                return
            if command == "重启.模型":
                self._admin_send_text(event, "收到")
                self.reload_model()
                return
            if command == "重启.机器人":
                self._admin_send_text(event, "收到")
                self.restart_bot()
                return
            if command in ("睡眠", "预备", "唤醒"):
                self._admin_send_text(event, "收到")
                # 与网页面板按钮共用同一组 PanelStore 方法，并按网页返回状态回复。
                if command == "睡眠":
                    result = self.force_sleep(int(event.get("group_id") or 0)) or {}
                elif command == "预备":
                    result = self.sleep_prep(int(event.get("group_id") or 0)) or {}
                else:
                    result = self.wake_sleep(int(event.get("group_id") or 0)) or {}
                self._admin_send_sleep_result(event, command, result)
                return
        except Exception as exc:  # noqa: BLE001
            sys.stdout.write("[admin] command %s failed: %s\n" % (command, exc))
            sys.stdout.flush()
            try:
                self._admin_send_text(event, "指令执行失败：%s" % exc)
            except Exception:
                pass

    def _admin_send_sleep_result(self, event, command, result):
        status = str((result or {}).get("status") or "")
        if command == "唤醒":
            text = "已强制结束睡眠" if (result or {}).get("was_sleeping") else "当前不在睡眠中"
        elif status == "sleeping":
            text = "当前正在睡觉，无法%s" % ("睡眠预备" if command == "预备" else "重复进入睡眠")
        elif status == "no_group":
            text = "没有可用的触发群"
        elif status in ("busy", "running"):
            text = "睡眠预备正在进行中" if command == "预备" and status == "running" else "当前正在处理睡眠流程"
        elif command == "睡眠":
            text = "正在进入睡眠"
        elif command == "预备":
            text = "已触发睡眠预备"
        else:
            text = "操作已执行"
        self._admin_send_text(event, text)

    def _help_text(self, is_admin=False):
        if not is_admin:
            return (
                "控制面板-一般用户\n"
                "/控制面板.帮助\n"
                "/控制面板.人设预设\n"
                "/控制面板.图库\n"
                "/控制面板.图库.图片发送.图库中图片名\n"
                "/控制面板.上下文\n"
                "/控制面板.上下文.保存\n"
                "/控制面板.上下文.删除本用户上下文\n"
                "/控制面板.监控\n"
                "/控制面板.重启.模型\n"
                "/控制面板.睡眠\n"
                "/控制面板.预备\n"
                "/控制面板.唤醒"
            )
        return (
            "控制面板-管理员\n"
            "/控制面板.帮助\n"
            "/控制面板.人设预设\n"
            "/控制面板.载入人设.人设名称\n"
            "/控制面板.图库\n"
            "/控制面板.图库.图片发送.图库中图片名\n"
            "/控制面板.图库.图片存入.图片名.描述.喜爱程度\n"
            "/控制面板.上下文\n"
            "/控制面板.上下文.保存\n"
            "/控制面板.上下文.删除本用户上下文\n"
            "/控制面板.上下文.删除指定用户上下文.qq号\n"
            "/控制面板.上下文.删除本群上下文\n"
            "/控制面板.上下文.删除所有上下文\n"
            "/控制面板.监控\n"
            "/控制面板.重启.模型\n"
            "/控制面板.重启.机器人\n"
            "/控制面板.睡眠\n"
            "/控制面板.预备\n"
            "/控制面板.唤醒"
        )

    def _onebot_send(self, event, segments):
        config = self._config()
        onebot = config.get("onebot") or {}
        api = str(onebot.get("api_url") or "http://127.0.0.1:3000").rstrip("/")
        token = str(onebot.get("token") or "")
        message_type = event.get("message_type")
        if message_type == "group":
            action = "send_group_msg"
            target = int(event.get("group_id") or 0)
            payload = {"group_id": target, "message": segments}
        elif message_type == "private":
            action = "send_private_msg"
            target = int(event.get("user_id") or 0)
            payload = {"user_id": target, "message": segments}
        else:
            return
        if target <= 0:
            return
        request = urllib.request.Request(
            api + "/" + action,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()

    def _admin_send_text(self, event, text):
        self._onebot_send(event, [{"type": "text", "data": {"text": text}}])

    def _admin_send_image(self, event, raw):
        encoded = base64.b64encode(raw).decode("ascii")
        self._admin_send_image_segment(event, "base64://" + encoded)

    def _admin_send_image_segment(self, event, value):
        value = str(value or "").strip()
        if value.startswith("data:image/"):
            value = value.split(",", 1)[-1]
        if value.startswith("http://") or value.startswith("https://"):
            file_value = value
        elif value.startswith("base64://"):
            file_value = value
        else:
            file_value = "base64://" + value
        self._onebot_send(
            event, [{"type": "image", "data": {"file": file_value}}])

    def _find_browser(self):
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        for name in ("msedge", "chrome", "chromium"):
            path = shutil.which(name)
            if path:
                return path
        raise PanelError("未找到 Edge/Chrome，无法生成网页截图")

    def _admin_screenshot(self, view):
        browser = self._find_browser()
        settings = self._panel_settings_data()
        runtime = PANEL_RUNTIME
        port = runtime.port if runtime is not None else int(settings.get("port") or 8090)
        token = quote(PANEL_SCREENSHOT_TOKEN, safe="")
        url = "http://127.0.0.1:%d/screenshot-login?token=%s&view=%s" % (
            port, token, view)
        out_dir = os.path.join(self.root, "temp", "admin_shots")
        os.makedirs(out_dir, exist_ok=True)
        output = os.path.join(out_dir, "%s.png" % uuid.uuid4().hex)
        errors = []
        for headless in ("--headless=new", "--headless"):
            profile = os.path.join(out_dir, "profile-%s" % uuid.uuid4().hex)
            os.makedirs(profile, exist_ok=True)
            args = [
                browser,
                headless,
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-crash-reporter",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1200,1400",
                "--force-device-scale-factor=0.8",
                "--user-data-dir=%s" % profile,
                "--screenshot=%s" % output,
                url,
            ]
            proc = None
            try:
                proc = subprocess.Popen(
                    args, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                deadline = time.time() + 35
                while time.time() < deadline:
                    if os.path.isfile(output) and os.path.getsize(output) > 1000:
                        return output
                    if proc.poll() is not None:
                        break
                    time.sleep(0.25)
                errors.append("截图文件为空")
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
            finally:
                if proc is not None and proc.poll() is None:
                    try:
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=5)
                    except Exception:
                        proc.kill()
                shutil.rmtree(profile, ignore_errors=True)
        try:
            os.remove(output)
        except OSError:
            pass
        raise PanelError("网页截图失败：%s" % ("; ".join(errors) or "未知错误"))

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

    def _raw_images(self):
        path = os.path.join(self.root, "img", "index.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _favorite(self, value, default=50):
        match = re.search(r"\d{1,3}", str(value or ""))
        return max(0, min(100, int(match.group()))) if match else default

    def _write_images(self, entries):
        directory = os.path.join(self.root, "img")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "index.json")
        tmp = path + ".panel.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

        lines = []
        for item in entries:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            description = str(item.get("description") or "").replace("\r", " ").replace("\n", " ").strip()
            favorite = self._favorite(item.get("favorite"), 50)
            if description:
                lines.append("%s：%s（喜爱 %d）" % (name, description, favorite))
            else:
                lines.append("%s（喜爱 %d）" % (name, favorite))
        text_path = os.path.join(self.root, "image_index.txt")
        text_tmp = text_path + ".panel.tmp"
        with open(text_tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines))
            if lines:
                fh.write("\n")
        os.replace(text_tmp, text_path)

    def _images(self):
        items = []
        for item in self._raw_images():
            filename = str(item.get("file") or "")
            path = os.path.join(self.root, "img", os.path.basename(filename))
            size = 0
            try:
                size = os.path.getsize(path)
            except OSError:
                pass
            items.append({
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "favorite": self._favorite(item.get("favorite"), 50),
                "file": filename,
                "size": size,
                "created_at": item.get("created_at", ""),
            })
        return items

    def _image_index(self, entries, name):
        target = str(name or "").strip().casefold()
        if not target:
            return -1
        for index, item in enumerate(entries):
            item_name = str(item.get("name") or "").strip().casefold()
            item_file = os.path.basename(str(item.get("file") or "")).casefold()
            if target in (item_name, item_file, os.path.splitext(item_file)[0]):
                return index
        return -1

    def update_image(self, name, new_name=None, description=None, favorite=None):
        entries = self._raw_images()
        index = self._image_index(entries, name)
        if index < 0:
            raise PanelError("找不到图片：%s" % name)
        item = entries[index]
        if new_name is not None:
            cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', "", str(new_name)).strip()[:60]
            if not cleaned:
                raise PanelError("图片名不能为空")
            for other in range(len(entries)):
                if other != index and str(entries[other].get("name") or "").strip().casefold() == cleaned.casefold():
                    raise PanelError("图片名已存在：%s" % cleaned)
            old_file = os.path.basename(str(item.get("file") or ""))
            if old_file and cleaned.casefold() != str(item.get("name") or "").strip().casefold():
                extension = os.path.splitext(old_file)[1]
                new_file = cleaned + extension
                old_path = os.path.join(self.root, "img", old_file)
                new_path = os.path.join(self.root, "img", new_file)
                if os.path.normcase(old_path) != os.path.normcase(new_path):
                    if os.path.exists(new_path):
                        raise PanelError("目标图片文件已存在：%s" % new_file)
                    if os.path.isfile(old_path):
                        os.rename(old_path, new_path)
                    item["file"] = new_file
                else:
                    item["file"] = new_file
            item["name"] = cleaned
        if description is not None:
            item["description"] = re.sub(r"\s+", " ", str(description)).strip()[:500]
        if favorite is not None:
            item["favorite"] = self._favorite(favorite, item.get("favorite", 50))
        entries[index] = item
        self._write_images(entries)
        return {"ok": True, "name": item.get("name"), "description": item.get("description"),
                "favorite": self._favorite(item.get("favorite"), 50)}

    def delete_image(self, name):
        entries = self._raw_images()
        index = self._image_index(entries, name)
        if index < 0:
            raise PanelError("找不到图片：%s" % name)
        item = entries.pop(index)
        filename = os.path.basename(str(item.get("file") or ""))
        if filename:
            try:
                os.remove(os.path.join(self.root, "img", filename))
            except OSError:
                pass
        self._write_images(entries)
        return {"ok": True, "name": item.get("name"), "deleted": filename}

    def image_info(self, name):
        target = str(name or "").strip()
        if not target:
            return None
        for item in reversed(self._images()):
            filename = str(item.get("file") or "")
            stem = os.path.splitext(filename)[0]
            if target.casefold() not in (
                    str(item.get("name") or "").casefold(),
                    filename.casefold(), stem.casefold()):
                continue
            path = os.path.join(self.root, "img", os.path.basename(filename))
            if not os.path.isfile(path):
                return None
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            return {"path": path, "mime": mime, "item": item}
        return None

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


class PanelHandler(BaseHTTPRequestHandler):
    store = None
    server_version = "AIQQPanel/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("[panel] " + (fmt % args) + "\n")
        sys.stdout.flush()

    def _authorized(self):
        cookie = self.headers.get("Cookie", "")
        if PANEL_SCREENSHOT_TOKEN and cookie:
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "aiqq_screenshot" and hmac.compare_digest(
                        value, PANEL_SCREENSHOT_TOKEN):
                    return True
        if not PANEL_PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return False
        _, _, password = decoded.partition(":")
        return hmac.compare_digest(password, PANEL_PASSWORD)

    def _require_auth(self):
        if self._authorized():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Control Panel"')
        self.end_headers()
        return False

    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise PanelError("Content-Length 无效")
        if length > MAX_BODY:
            raise PanelError("请求体超过 2MB")
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise PanelError("请求体不是合法 JSON：%s" % exc)
        if not isinstance(data, dict):
            raise PanelError("请求体必须是对象")
        return data

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/screenshot-login":
            params = parse_qs(parsed.query)
            token = (params.get("token") or [""])[0]
            if not PANEL_SCREENSHOT_TOKEN or not hmac.compare_digest(
                    token, PANEL_SCREENSHOT_TOKEN):
                self._send_json({"error": "forbidden"}, 403)
                return
            view = (params.get("view") or ["editor"])[0]
            if view not in ("library", "contexts", "monitor", "editor", "settings"):
                view = "editor"
            self.send_response(302)
            self.send_header(
                "Set-Cookie",
                "aiqq_screenshot=%s; Path=/; HttpOnly; SameSite=Lax" % PANEL_SCREENSHOT_TOKEN)
            self.send_header("Location", "/?view=" + view)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._require_auth():
            return
        if parsed.path in ("/", "/index.html"):
            try:
                with open(os.path.join(BASE_DIR, "panel.html"), encoding="utf-8") as fh:
                    raw = fh.read().encode("utf-8")
            except OSError as exc:
                self._send_json({"error": "读取 panel.html 失败：%s" % exc}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path == "/api/state":
            self._send_json(self.store.state())
            return
        if parsed.path == "/api/debug-screenshot":
            view = (parse_qs(parsed.query).get("view") or ["library"])[0]
            try:
                path = self.store._admin_screenshot(view)
                size = os.path.getsize(path)
                os.remove(path)
                self._send_json({"ok": True, "size": size})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        if parsed.path == "/api/panel-settings":
            self._send_json(self.store.panel_settings())
            return
        if parsed.path == "/api/monitor":
            self._send_json(self.store.monitor_state())
            return
        if parsed.path == "/api/raw-history":
            try:
                self._send_json(self.store.raw_history())
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 503)
            return
        if parsed.path == "/api/napcat-status":
            try:
                self._send_json(self.store.napcat_status())
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 503)
            return
        if parsed.path == "/api/file":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                content = self.store.read(name)
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json({"name": name, "content": content,
                             "editable": name in self.store.EDITABLE})
            return
        if parsed.path == "/api/persona-presets":
            self._send_json({"presets": self.store.list_persona_presets()})
            return
        if parsed.path == "/api/persona-preset":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                result = self.store.read_persona_preset(name)
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json(result)
            return
        if parsed.path == "/api/image":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                info = self.store.image_info(name)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
                return
            if not info:
                self._send_json({"error": "图片不存在"}, 404)
                return
            try:
                with open(info["path"], "rb") as fh:
                    raw = fh.read()
            except OSError as exc:
                self._send_json({"error": "读取图片失败：%s" % exc}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", info["mime"])
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/api/contexts":
            self._send_json({"contexts": self.store.contexts()})
            return
        if parsed.path == "/api/context":
            filename = (parse_qs(parsed.query).get("file") or [""])[0]
            try:
                self._send_json(self.store.context_detail(filename))
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 404)
            return
        self._send_json({"error": "not found"}, 404)

    def _admin_bridge_authorized(self):
        config = self.store._config()
        token = str((config.get("onebot") or {}).get("token") or "")
        provided = self.headers.get("X-AIQQ-Admin-Token") or ""
        if token:
            return hmac.compare_digest(provided, token)
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/admin-command":
            if not self._admin_bridge_authorized():
                self._send_json({"ok": False, "status": "forbidden"}, 403)
                return
            try:
                body = self._read_body()
            except Exception:
                self._send_json({"ok": False, "status": "bad_body"}, 400)
                return
            try:
                self._send_json(self.store.enqueue_admin_command(body))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        if not self._require_auth():
            return
        try:
            body = self._read_body()
            if parsed.path == "/api/open-folder":
                folder = body.get("folder")
                if folder not in ("img", "context"):
                    raise PanelError("缺少文件夹类型")
                self._send_json(self.store.open_folder(folder))
                return
            if parsed.path == "/api/file":
                name = (parse_qs(parsed.query).get("name") or [""])[0]
                content = body.get("content")
                if not isinstance(content, str):
                    raise PanelError("缺少 content")
                result = self.store.write(name, content)
                self._send_json({"ok": True, **result})
                return
            if parsed.path == "/api/persona-presets":
                try:
                    result = self.store.write_persona_preset(
                        body.get("name"), body.get("content"))
                except PanelError as exc:
                    self._send_json({"error": str(exc)}, 400)
                    return
                self._send_json({"ok": True, **result})
                return
            if parsed.path == "/api/apply-init":
                result = self.store.apply_init()
                self._send_json(result)
                return
            if parsed.path == "/api/start":
                result = self.store.start_script(body.get("mode", "all"))
                self._send_json(result)
                return
            if parsed.path == "/api/restart":
                result = self.store.restart_bot()
                self._send_json(result)
                return
            if parsed.path == "/api/stop-bot":
                result = self.store.stop_bot()
                self._send_json(result)
                return
            if parsed.path == "/api/shutdown-panel":
                result = self.store.shutdown_panel()
                self._send_json(result)
                return
            if parsed.path == "/api/shutdown-all":
                result = self.store.shutdown_all()
                self._send_json(result)
                return
            if parsed.path == "/api/reload-model":
                result = self.store.reload_model()
                self._send_json(result)
                return
            if parsed.path == "/api/save-context":
                result = self.store.save_context()
                self._send_json(result)
                return
            if parsed.path == "/api/clear-context":
                result = self.store.clear_context_snapshots()
                self._send_json(result)
                return
            if parsed.path == "/api/wake":
                result = self.store.wake_sleep(body.get("group_id", 0))
                self._send_json(result)
                return
            if parsed.path == "/api/sleep-prep":
                result = self.store.sleep_prep(body.get("group_id", 0))
                self._send_json(result)
                return
            if parsed.path == "/api/force-sleep":
                result = self.store.force_sleep(body.get("group_id", 0))
                self._send_json(result)
                return
            if parsed.path == "/api/panel-settings":
                result = self.store.save_panel_settings(
                    body.get("lan_enabled", False),
                    body.get("password"),
                    body.get("port", 8090),
                )
                self._send_json({"ok": True, **result})
                runtime = PANEL_RUNTIME
                if runtime is not None:
                    threading.Thread(
                        target=runtime.rebind_from_settings,
                        name="panel-rebind", daemon=False).start()
                return
            if parsed.path == "/api/image/update":
                name = body.get("name")
                if not isinstance(name, str):
                    raise PanelError("缺少图片名")
                result = self.store.update_image(
                    name,
                    new_name=body.get("new_name"),
                    description=body.get("description"),
                    favorite=body.get("favorite"),
                )
                self._send_json(result)
                return
            if parsed.path == "/api/image/delete":
                name = body.get("name")
                if not isinstance(name, str):
                    raise PanelError("缺少图片名")
                self._send_json(self.store.delete_image(name))
                return
            if parsed.path == "/api/context/delete":
                filename = body.get("file")
                if not isinstance(filename, str):
                    raise PanelError("缺少上下文文件名")
                self._send_json(self.store.delete_context(filename))
                return
        except PanelError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": "服务器错误：%s" % exc}, 500)
            return
        self._send_json({"error": "not found"}, 404)


def _is_loopback(host):
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _lan_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""


class PanelHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    allow_reuse_port = False


class PanelRuntime(object):
    """控制面板监听器；修改设置时原地换绑，不退出进程。"""

    def __init__(self, store, host, port, password):
        self.store = store
        self.host = host
        self.port = port
        self.password = password
        self.server = None
        self.thread = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _bind(self, host, port):
        last_error = None
        server = None
        for _ in range(20):
            try:
                server = PanelHTTPServer((host, port), PanelHandler)
                break
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        if server is None:
            raise last_error
        thread = threading.Thread(
            target=server.serve_forever, name="panel-http", daemon=True)
        thread.start()
        self.server = server
        self.thread = thread
        self.host = host
        self.port = port
        print("控制面板：http://%s:%d" % (host, port))
        if not _is_loopback(host):
            lan_ip = _lan_ip()
            if lan_ip:
                print("局域网访问：http://%s:%d" % (lan_ip, port))
            print("用户名可任意填写，密码为面板设置中的访问密码。")
        sys.stdout.flush()

    def start(self):
        self._bind(self.host, self.port)

    def rebind_from_settings(self):
        data = self.store._panel_settings_data()
        host = "0.0.0.0" if data["lan_enabled"] else "127.0.0.1"
        self.rebind(host, data["port"], data["password"])

    def rebind(self, host, port, password):
        global PANEL_PASSWORD
        with self._lock:
            old_host, old_port, old_password = self.host, self.port, self.password
            old_server = self.server
            if old_server is not None:
                try:
                    old_server.shutdown()
                    old_server.server_close()
                except Exception:  # noqa: BLE001
                    pass
            PANEL_PASSWORD = password or ""
            self.password = password or ""
            try:
                self._bind(host, port)
            except OSError as exc:
                print("[panel] 换绑 %s:%d 失败：%s，尝试恢复旧监听…" % (host, port, exc))
                PANEL_PASSWORD = old_password or ""
                self.password = old_password or ""
                try:
                    self._bind(old_host, old_port)
                except OSError as restore_exc:
                    print("[panel] 恢复旧监听也失败：%s" % restore_exc)
                raise
            print("[panel] 新设置已生效")
            sys.stdout.flush()

    def wait(self):
        while not self._stop.wait(0.5):
            pass

    def shutdown(self):
        self._stop.set()
        server = self.server
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001
                pass


def _panel_alive(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1) as response:
            return "AIQQPanel" in (response.headers.get("Server") or "")
    except urllib.error.HTTPError as exc:
        return "AIQQPanel" in (exc.headers.get("Server") or "")
    except Exception:  # noqa: BLE001
        return False


def _open_panel_browser(port):
    if os.environ.get("AIQQ_PANEL_NO_BROWSER"):
        return
    try:
        webbrowser.open("http://127.0.0.1:%d/" % port)
    except Exception:  # noqa: BLE001
        pass


def main(argv=None):
    global PANEL_PASSWORD, PANEL_RUNTIME, PANEL_SCREENSHOT_TOKEN
    parser = argparse.ArgumentParser(description="AIQQ 网页控制面板")
    parser.add_argument("--host", default=None, help="监听地址；默认读取面板设置")
    parser.add_argument("--port", type=int, default=None, help="监听端口；默认读取面板设置")
    parser.add_argument("--password", default=None, help="访问密码；默认读取面板设置")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_args(argv)

    store = PanelStore(BASE_DIR)
    settings = store._panel_settings_data()
    host = args.host if args.host is not None else (
        "0.0.0.0" if settings["lan_enabled"] else "127.0.0.1")
    port = args.port if args.port is not None else settings["port"]
    password = args.password if args.password is not None else settings["password"]

    if not _is_loopback(host) and not password:
        print("局域网监听地址缺少访问密码，暂时只监听 127.0.0.1。")
        print("启动后请在面板「面板设置」中设置密码并重新开启局域网。")
        host = "127.0.0.1"

    if args.host is not None or args.port is not None or args.password is not None:
        # 命令行覆盖也写入设置，保证面板内显示与重启后的监听一致
        store.save_panel_settings(not _is_loopback(host), password, port)

    PANEL_PASSWORD = password or ""
    PANEL_SCREENSHOT_TOKEN = secrets.token_urlsafe(24)
    PanelHandler.store = store
    if _panel_alive(port):
        print("控制面板已经在运行：http://127.0.0.1:%d" % port)
        if not args.no_browser:
            _open_panel_browser(port)
        return 0
    runtime = PanelRuntime(store, host, port, password)
    PANEL_RUNTIME = runtime
    try:
        runtime.start()
    except OSError as exc:
        if _panel_alive(port):
            print("控制面板已经在运行：http://127.0.0.1:%d" % port)
            return 0
        print("无法监听 %s:%d：%s" % (host, port, exc))
        return 1

    if PANEL_PASSWORD:
        print("已启用访问密码")
    if not args.no_browser:
        _open_panel_browser(port)
    print("按 Ctrl+C 停止")
    try:
        runtime.wait()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
