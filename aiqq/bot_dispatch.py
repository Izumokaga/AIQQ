#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_dispatch：模型队列、命令分发、事件入口与追问。"""
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





from .config import _as_int, log
from .media import extract_image_segments, message_to_text
from .text import _preview
from .reply import strip_media_placeholders

class BotDispatchMixin:
    def enqueue(self, event):
        if self._stopping.is_set():
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            log.warning("事件队列已满，丢弃一条事件。")
            return False

    def _enqueue_model_request(self, entry, group_id, priority):
        with self._model_queue_lock:
            if entry.get("professional"):
                # 专业模式优先：清掉尚未开始的其他触发，确保本轮输出期间没有其他回复插入。
                self._model_queue = [
                    item for item in self._model_queue if item[2].get("professional")
                ]
                heapq.heapify(self._model_queue)
            kept = [
                item for item in self._model_queue
                if not (item[3] == group_id and item[0] > priority)
            ]
            if len(kept) != len(self._model_queue):
                self._model_queue = kept
                heapq.heapify(self._model_queue)
            if len(self._model_queue) >= self._model_max_queue:
                log.warning("[模型队列] 已满（%d），丢弃群 %s 的触发", self._model_max_queue, group_id)
                return False
            self._model_seq += 1
            heapq.heappush(
                self._model_queue,
                (int(priority), self._model_seq, entry, group_id))
            if self._model_active_group == group_id and priority == 0:
                self._model_preempted = True
            self._model_queue_lock.notify()
        return True

    def _model_queue_size(self):
        with self._model_queue_lock:
            return len(self._model_queue)

    def _model_queue_loop(self):
        while not self._stopping.is_set():
            with self._model_queue_lock:
                while not self._model_queue and not self._stopping.is_set():
                    self._model_queue_lock.wait(1.0)
                if self._stopping.is_set() and not self._model_queue:
                    break
                if not self._model_queue:
                    continue
                priority, seq, entry, group_id = heapq.heappop(self._model_queue)
                self._model_active_group = group_id
            try:
                self._reply_job(entry, group_id)
            finally:
                with self._model_queue_lock:
                    self._model_active_group = 0

    def _worker_loop(self):
        while not self._stopping.is_set():
            try:
                event = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if event is None:
                break
            try:
                self.handle_event(event)
            except Exception as exc:  # noqa: BLE001 - 单条事件失败不应拖垮进程
                self._record_error(exc)
                log.error("处理事件失败：%s\n%s", exc, traceback.format_exc())

    def _is_admin(self, user_id, group_id):
        if not self.admin_users and not self.admin_groups:
            return True
        return user_id in self.admin_users or (group_id and group_id in self.admin_groups)

    def _handle_admin_command(self, event):
        message_type = event.get("message_type")
        if message_type not in ("group", "private"):
            return False
        user_id = _as_int(event.get("user_id"), 0)
        if self.self_id and user_id == self.self_id:
            return False
        group_id = _as_int(event.get("group_id"), 0)
        text, _ = message_to_text(event.get("message"), self.self_id)
        # 命令消息可能同时附带图片/聊天记录，占位符不能混进指令参数。
        text = strip_media_placeholders(text)
        match = re.match(r"^[/／]\s*控制面板\s*[.．。]\s*(.+?)\s*$", text or "", re.I)
        if not match:
            return False
        # 标准面板对所有成员开放；管理员权限由面板按账号/群判断。
        # 机器人只负责把指令桥接到面板，避免把普通用户的合法标准指令拦掉。
        command = match.group(1).strip().lower()
        command = command.replace("．", ".").replace("。", ".").replace(" ", "")
        message_id = str(event.get("message_id") or "")
        if message_id and self.dedup.is_duplicate(
                ("admin", message_type, group_id, message_id)):
            return True
        payload = {
            "message_type": message_type,
            "group_id": group_id,
            "user_id": user_id,
            "message_id": str(event.get("message_id") or ""),
            "command": command,
            "images": extract_image_segments(event.get("message")),
        }
        url = str(self.cfg["qq"].get("admin_panel_url")
                  or "http://127.0.0.1:8090").rstrip("/") + "/api/admin-command"
        token = str(self.cfg["onebot"].get("token") or "")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     "X-AIQQ-Admin-Token": token},
        )
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                response.read()
            log.info("[控制面板] 已转发指令 %s（QQ %s / 群 %s）", command, user_id, group_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("[控制面板] 转发指令失败：%s", exc)
        return True

    def handle_event(self, event):
        with self._stats_lock:
            self.stats["events_received"] += 1

        post_type = event.get("post_type")
        if post_type == "meta_event":
            return
        if post_type != "message":
            return

        event_self_id = _as_int(event.get("self_id"), 0)
        if event_self_id:
            if not self.self_id:
                self.self_id = event_self_id
                log.info("从事件中获取机器人 QQ：%s", self.self_id)
            elif self.self_id != event_self_id:
                log.warning("事件中的 self_id=%s 与配置 qq.self_id=%s 不一致，将按事件账号处理。",
                            event_self_id, self.self_id)
                self.self_id = event_self_id
        if self._handle_admin_command(event):
            return
        if event.get("message_type") != "group":
            return

        self.handle_group_message(event)

    def _queue_pending_followup(self, group_id, entry, followup_reason, reason, quoted):
        with self._pending_lock:
            self._pending_followups[group_id] = {
                "entry": entry,
                "followup_reason": followup_reason,
                "reason": reason,
                "quoted": quoted,
            }

    def _clear_pending_followup(self, group_id):
        with self._pending_lock:
            self._pending_followups.pop(group_id, None)

    def _process_pending_followup(self, group_id):
        with self._pending_lock:
            pending = self._pending_followups.pop(group_id, None)
        if not pending or self._stopping.is_set():
            return
        if self._professional_active.is_set():
            return
        entry = pending["entry"]
        user_id = entry.get("user_id") or 0
        followup_reason = pending.get("followup_reason")
        if followup_reason:
            matched = self.followup.match(
                group_id, user_id, entry.get("text") or "",
                has_content=bool(entry.get("images") or entry.get("forwards")),
                quoted=bool(pending.get("quoted")))
            if not matched:
                return
            followup_reason = matched
            entry["followup"] = True
            self._cancel_idle_image_tasks()
        with self._busy_lock:
            busy = group_id in self._busy_groups
            if not busy:
                self._busy_groups.add(group_id)
        if busy:
            with self._pending_lock:
                self._pending_followups.setdefault(group_id, pending)
            return
        allowed, why = self.limiter.try_acquire(
            group_id, user_id, bypass_cooldown=True, bypass_interval=True)
        if not allowed:
            log.info("[限流] 排队追问 群 %s：%s", group_id, why)
            with self._busy_lock:
                self._busy_groups.discard(group_id)
            return
        if followup_reason:
            self.followup.consume(group_id)
        self._last_trigger_at = time.time()
        with self._autonomous_lock:
            self._group_last_trigger[group_id] = self._last_trigger_at
        self._schedule_spontaneous()
        self._note_context_activity(group_id)
        log.info("[触发] 群 %s | 排队追问(%s)：%s | 原因：%s", group_id, user_id,
                 _preview(entry.get("text") or "", 80), followup_reason or pending.get("reason"))
        self._llm_executor.submit(self._reply_job, entry, group_id)

    def _is_reply_to_self(self, reply_to, group_id=None):
        """判断这条消息是否在回复机器人自己发的消息。"""
        key = str(reply_to or "")
        if not key:
            return False
        if group_id is not None:
            for item in self.memory.recent(group_id, 80):
                if item.get("is_self") and str(item.get("message_id") or "") == key:
                    return True
        now = time.time()
        with self._reply_self_lock:
            cached = self._reply_self_cache.get(key)
            if cached and now - cached[0] < 120:
                return cached[1]
        try:
            raw = self.onebot.get_msg(key)
        except Exception as exc:  # noqa: BLE001
            log.debug("[触发] 读取引用消息 %s 失败：%s", key, exc)
            return False
        sender = raw.get("sender") or {}
        quoted_user = _as_int(raw.get("user_id") or sender.get("user_id"), 0)
        result = bool(self.self_id and quoted_user == self.self_id)
        with self._reply_self_lock:
            self._reply_self_cache[key] = (now, result)
            while len(self._reply_self_cache) > 200:
                self._reply_self_cache.pop(next(iter(self._reply_self_cache)))
        return result

    def _resolve_member_name(self, group_id, user_id):
        user_id = _as_int(user_id, 0)
        if user_id <= 0:
            return ""
        if self.self_id and user_id == self.self_id:
            return self.self_nick or str(user_id)
        key = (int(group_id), int(user_id))
        if key in self._member_names:
            return self._member_names[key]
        name = ""
        try:
            info = self.onebot.call(
                "get_group_member_info",
                {"group_id": int(group_id), "user_id": int(user_id)},
            ) or {}
            name = str(info.get("card") or info.get("nickname") or "").strip()
        except Exception as exc:  # noqa: BLE001
            log.debug("[触发] 读取群成员 %s/%s 名称失败：%s", group_id, user_id, exc)
        name = name or str(user_id)
        self._member_names[key] = name
        return name

__all__ = [name for name in globals() if not name.startswith("__")]
