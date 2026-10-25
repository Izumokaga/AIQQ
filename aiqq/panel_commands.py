#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_commands：QQ 控制面板命令、帮助与截图。"""
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

class PanelCommandsMixin:
    def _config(self):
        try:
            with open(os.path.join(self.root, "config.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

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
                    result = self.activate_persona_preset(name)
                except PanelError as exc:
                    self._admin_send_text(
                        event, "载入人设失败：%s" % exc)
                    return
                if result.get("init"):
                    self._admin_send_text(
                        event, "已载入人设及同名初始化数据：%s；机器人正在重启"
                        % result.get("name", name))
                elif result.get("template_loaded"):
                    self._admin_send_text(
                        event, "已载入人设：%s；同名初始化预设为空，已载入空模板，"
                        "请前往网页初始化参数修改" % result.get("name", name))
                else:
                    suffix = ("（%s）" % result["warning"]) if result.get("warning") else ""
                    self._admin_send_text(
                        event, "已载入人设：%s%s"
                        % (result.get("name", name), suffix))
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
            if command == "上下文.删除本用户专业模式上下文":
                self._admin_send_text(event, "收到")
                self.delete_professional_context(int(event.get("user_id") or 0))
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
                "/控制面板.上下文.删除本用户专业模式上下文\n"
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
            "/控制面板.上下文.删除本用户专业模式上下文\n"
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
        runtime = panel_state.PANEL_RUNTIME
        port = runtime.port if runtime is not None else int(settings.get("port") or 8090)
        token = quote(panel_state.PANEL_SCREENSHOT_TOKEN, safe="")
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


__all__ = [name for name in globals() if not name.startswith("__")]
