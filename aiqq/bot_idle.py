#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_idle：空闲识图与 TTS 空闲卸载。"""
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
from .media import _image_mime, image_bytes_to_data_url
from .voice import strip_voice_marker
from .text import _preview
from .reply import strip_speaker_labels

class BotIdleMixin:
    def _maybe_unload_voice_model(self, now=None):
        if not self.voice.enabled or not self.voice.qwen_enabled:
            return False
        if self.voice.is_busy():
            return False
        now = time.time() if now is None else now
        last = self._last_voice_done_at
        if not last:
            return False
        idle = int(self.cfg["voice"].get("qwen_tts_unload_idle_sec", 60))
        if now - last < idle:
            return False
        if self.voice.unload_qwen_server():
            self._last_voice_done_at = 0.0
            self._voice_model_unloaded = True
            return True
        return False

    def _voice_idle_loop(self):
        while not self._stopping.wait(10):
            try:
                self._maybe_unload_voice_model()
            except Exception as exc:  # noqa: BLE001
                log.warning("[语音] 空闲卸载检查异常：%s", exc)

    def _idle_image_hour_allowed(self):
        if not self.cfg["lm_studio"].get("idle_image_reply_enabled", True):
            return False
        start = int(self.cfg["lm_studio"].get("idle_image_start_hour", 7))
        end = int(self.cfg["lm_studio"].get("idle_image_end_hour", 22))
        hour = datetime.now().hour
        if start == end:
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def _idle_image_key(self, group_id, entry, index, info):
        return (group_id, str(entry.get("message_id") or ""), index,
                str(info.get("url") or ""), str(info.get("file") or ""))

    def _mark_images_handled(self, entry, group_id):
        """正常回复已使用这些图片后，标记为已处理并重置空闲识图冷却。"""
        images = entry.get("images") or []
        if not images:
            return
        with self._idle_image_seen_lock:
            for index, info in enumerate(images):
                if isinstance(info, dict):
                    self._idle_image_seen.add(
                        self._idle_image_key(group_id, entry, index, info))
        self._last_idle_image_at = time.time()

    def _cancel_idle_image_tasks(self):
        """任何正常触发都会取消空闲识图待办/进行中的回复。"""
        with self._idle_image_seen_lock:
            self._idle_image_generation += 1
        self._last_idle_image_at = time.time()
        log.info("[识图] 收到新的触发，已清除空闲识图待办")

    def _pick_idle_images(self, now, limit=None):
        window = max(1, int(self.cfg["lm_studio"].get("idle_image_window_sec", 300)))
        batch_size = max(1, min(10, int(self.cfg["lm_studio"].get("idle_image_batch_size", 5))))
        if limit is None:
            limit = batch_size
        limit = max(1, min(limit, batch_size))
        candidates = []
        for group_id in list(self.groups):
            if self._context_group_blocked(group_id):
                continue
            with self._busy_lock:
                if group_id in self._busy_groups:
                    continue
            for entry in self.memory.recent(group_id, 500):
                if entry.get("is_self") or entry.get("triggered"):
                    continue
                entry_time = _as_int(entry.get("time"), 0)
                if entry_time < now - window:
                    continue
                images = entry.get("images") or []
                for index, info in enumerate(images):
                    if not isinstance(info, dict):
                        continue
                    key = self._idle_image_key(group_id, entry, index, info)
                    with self._idle_image_seen_lock:
                        if key in self._idle_image_seen:
                            continue
                    candidates.append((entry_time, group_id, entry, index, info, key))
        if not candidates:
            return []
        candidates.sort(key=lambda item: (item[0], str(item[2].get("message_id") or ""), item[3]))
        group_id = candidates[0][1]
        selected = [item for item in candidates if item[1] == group_id][:limit]
        picked = []
        with self._idle_image_seen_lock:
            for entry_time, group_id, entry, index, info, key in selected:
                if key in self._idle_image_seen:
                    continue
                self._idle_image_seen.add(key)
                if len(self._idle_image_seen) > 10000:
                    self._idle_image_seen.clear()
                    self._idle_image_seen.add(key)
                picked.append((group_id, entry, info))
        return picked

    def _pick_idle_image(self, now):
        picked = self._pick_idle_images(now, 1)
        return picked[0] if picked else None

    def _idle_image_reply(self, group_id, entry, info, now):
        return self._idle_image_reply_batch(group_id, [(entry, info)], now)

    def _idle_image_reply_batch(self, group_id, items, now):
        with self._idle_image_seen_lock:
            generation = self._idle_image_generation
        if not self.lm.supports_vision():
            return False
        loaded = []
        for entry, info in items:
            try:
                raw = self._read_image_bytes(info)
                mime = _image_mime(raw)
                if self.lm.image_only_png_jpeg and mime not in ("image/png", "image/jpeg"):
                    log.info("[识图] 跳过不支持的图片格式：%s", mime)
                    continue
                loaded.append((entry, info, image_bytes_to_data_url(raw)))
            except Exception as exc:  # noqa: BLE001
                # 当前图片不加入输入，后续成功图片的编号自然前移。
                log.warning("[识图] 图片读取失败，编号顺延：%s", exc)
        if not loaded:
            log.info("[识图] 本轮没有可读取的图片，等待下一轮")
            return False
        messages = [{"role": "system", "content": self._system_prompt(
            group_id, "群聊中看到几张图片")}]
        batch_ids = {str(entry.get("message_id") or "") for entry, _info in items}
        for item in self.memory.recent(group_id, 15):
            if str(item.get("message_id") or "") in batch_ids:
                continue
            text = str(item.get("text") or "").strip()
            if not text and not item.get("images"):
                continue
            text = text or "[图片]"
            if item.get("is_self"):
                messages.append({"role": "assistant", "content": text})
            else:
                messages.append({"role": "user", "content": "%s：%s" % (
                    item.get("nickname") or item.get("user_id") or "群友", text)})
        last_entry = items[-1][0]
        current_text = str(last_entry.get("text") or "").strip()
        hint = ("（群友最近发了 %d 张图片，已按第 1 张到第 %d 张的顺序提供。"
                "结合上下文，用你平时的口吻自然回应，最多两小句。不要提识图、模型或系统。）"
                % (len(loaded), len(loaded)))
        if current_text:
            hint = ("（群友最近发来 %d 张图片并说：%s。图片按顺序提供。"
                    "结合上下文自然回应，最多两小句，不要提识图或系统。）"
                    % (len(loaded), current_text[:100]))
        content = [{"type": "text", "text": hint}]
        for entry, info, data_url in loaded:
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        messages.append({"role": "user", "content": content})
        started = time.time()
        with self._model_lock:
            reply = self.lm.chat(messages)
        reply = strip_speaker_labels(reply or "", (self.self_nick,)).strip()
        if not reply:
            log.info("[识图] 模型没有返回内容，跳过")
            return False
        with self._idle_image_seen_lock:
            if generation != self._idle_image_generation:
                log.info("[识图] 空闲识图期间出现新触发，丢弃本轮识图回复")
                return False
        cache_key = (group_id, str(last_entry.get("message_id") or ""))
        with self._image_inputs_lock:
            self._image_inputs_cache[cache_key] = [info for _entry, info, _url in loaded]
        self._remember_raw_reply(group_id, reply)
        self._send_reply(last_entry, group_id, reply, thinking_sec=time.time() - started)
        if self.cfg["lm_studio"].get("context_save_enabled", True):
            context_reply = strip_voice_marker(reply)[1] or reply
            user_text = last_entry.get("text") or "[图片x%d]" % len(loaded)
            count = self.group_context.append_exchange(
                group_id, user_text, context_reply,
                user_name=last_entry.get("nickname") or "",
                mentions=last_entry.get("mentions") or [])
            if count > int(self.cfg["lm_studio"].get("group_context_max_chars", 5000)):
                self._start_context_consolidation(group_id)
        self._last_idle_image_at = time.time()
        log.info("[识图] 已识图并主动回复：群 %s | %d 张 | %s",
                 group_id, len(loaded), _preview(reply, 80))
        return True

    def _idle_image_tick(self):
        if self._professional_active.is_set() or self._context_maintenance.is_set():
            return
            return
        if not self._idle_image_hour_allowed():
            return
        with self._connectivity_lock:
            if not self._connectivity.get("onebot_connected"):
                return
        now = time.time()
        if self._sleeping_until > now or self._spontaneous_waiting:
            return
        if self._idle_image_startup_reset:
            # 启动后先显示并等待一个 10 秒轮询周期，再开始第一轮识图。
            if now - self._last_idle_image_poll_at < int(
                    self.cfg["lm_studio"].get("idle_image_poll_sec", 10)):
                return
            self._idle_image_startup_reset = False
        elif now - self._last_idle_image_at < int(
                self.cfg["lm_studio"].get("idle_image_idle_sec", 60)):
            return
        self._last_idle_image_poll_at = now
        picked = self._pick_idle_images(now)
        if not picked:
            # 没有待读图片时保持 10 秒轮询；识图成功后才进入 60 秒冷却。
            return
        group_id = picked[0][0]
        with self._busy_lock:
            if group_id in self._busy_groups:
                return
            self._busy_groups.add(group_id)
        self._idle_image_processing = True
        try:
            self._idle_image_reply_batch(group_id, [(entry, info) for group_id, entry, info in picked], now)
        except Exception as exc:  # noqa: BLE001
            log.warning("[识图] 空闲识图失败：%s", exc)
        finally:
            self._idle_image_processing = False
            with self._busy_lock:
                self._busy_groups.discard(group_id)

    def _idle_image_loop(self):
        poll_sec = max(1, int(self.cfg["lm_studio"].get("idle_image_poll_sec", 10)))
        while not self._stopping.wait(poll_sec):
            try:
                self._idle_image_tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("[识图] 空闲识图检查异常：%s", exc)

__all__ = [name for name in globals() if not name.startswith("__")]
