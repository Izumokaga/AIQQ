#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_messages：群消息处理、图片选择与存图。"""
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
from .media import extract_forward_segments, extract_image_segments, extract_mentions, extract_reply_id, extract_voice_segments, message_to_text
from .text import _preview
from .onebot import OneBotError
from .images import extract_professional_command, sanitize_image_name

class BotMessagesMixin:
    def handle_group_message(self, event):
        group_id = _as_int(event.get("group_id"), 0)
        if group_id <= 0 or group_id not in self.groups:
            return

        message_id = event.get("message_id")
        dedup_key = (group_id, message_id)
        if message_id and self.dedup.is_duplicate(dedup_key):
            log.debug("忽略重复事件 message_id=%s", message_id)
            return

        user_id = _as_int(event.get("user_id"), 0)
        if self.self_id and user_id == self.self_id:
            return
        if user_id in self.ignore_users:
            return
        if self._fatal_restarting:
            return

        text, mentioned = message_to_text(event.get("message"), self.self_id)
        text = text.strip()
        voices = extract_voice_segments(event.get("message"))
        voice_texts = []
        for voice_info in voices:
            try:
                transcript = self.voice.transcribe(voice_info)
                if transcript:
                    voice_texts.append(transcript)
            except Exception as exc:  # noqa: BLE001
                log.warning("[语音] 识别失败：%s", exc)
        if voice_texts:
            for transcript in voice_texts:
                if "[语音]" in text:
                    text = text.replace("[语音]", transcript, 1)
                else:
                    text = (text + " " + transcript).strip()
            text = text.strip()
            log.info("[语音] 已转文字：%s", _preview(" ".join(voice_texts), 80))
        professional_content = extract_professional_command(text)
        professional = professional_content is not None
        if professional:
            text = professional_content
            if not text:
                log.info("[专业模式] 内容为空，忽略")
                return
        reply_to = extract_reply_id(event.get("message"))
        self._last_active_group = group_id
        if self._sleeping_until > 0 and not professional:
            if time.time() < self._sleeping_until:
                reason = self._sleep_trigger_reason(group_id, text, mentioned, reply_to)
                if reason:
                    attempts = self._record_sleep_attempt(group_id)
                    log.info("[主动] 群 %s 睡觉中被尝试触发 %d/5", group_id, attempts)
                    if attempts > 5:
                        self._force_wake_for_noise(group_id)
                return
            self._wake()
            self._start_wake_greeting(0, "natural")
        self._note_spontaneous_activity(group_id)

        images = extract_image_segments(event.get("message"))
        forwards = extract_forward_segments(event.get("message"))
        sender = event.get("sender") or {}
        nickname = str(sender.get("card") or sender.get("nickname") or user_id)

        if group_id not in self.group_names:
            try:
                info = self.onebot.get_group_info(group_id)
                name = info.get("group_name")
                if name:
                    self.group_names[group_id] = name
            except OneBotError:
                pass

        mentions = extract_mentions(event.get("message"), self.self_id)
        for mention in mentions:
            if not mention.get("name") or mention.get("name") == "成员":
                mention["name"] = self._resolve_member_name(
                    group_id, mention.get("user_id"))

        entry = {
            "message_id": str(message_id),
            "user_id": user_id,
            "nickname": nickname,
            "text": text,
            "mentioned": mentioned,
            "mentions": mentions,
            "voices": voices,
            "voice_texts": voice_texts,
            "images": images,
            "forwards": forwards,
            "reply_to": reply_to,
            "time": _as_int(event.get("time"), int(time.time())),
            "is_self": bool(self.self_id and user_id == self.self_id),
            "professional": professional,
        }
        self.memory.add(group_id, entry)
        self._mark_context_dirty(group_id)
        log.debug("[消息] 群 %s | %s(%s)：%s%s", group_id, nickname, user_id, _preview(text, 60),
                  ("（图片 %d 张）" % len(images)) if images else "")

        with self._stats_lock:
            self.stats["messages_seen"] += 1

        if self._professional_active.is_set():
            if not professional:
                log.info("[专业模式] 正在输出，暂停其他触发：群 %s | %s",
                         group_id, _preview(text, 60))
                return
            if str(message_id or "") != self._professional_message_id:
                log.info("[专业模式] 已有任务进行中，忽略新的专业模式请求")
                return

        if not professional and self.trigger.blocked(text):
            log.info("[触发] 命中黑名单触发词，忽略：群 %s | %s", group_id, _preview(text, 80))
            return

        reason = "专业模式" if professional else self.trigger.match(text, mentioned)
        reply_to_self = False
        if not reason and reply_to:
            reply_to_self = self._is_reply_to_self(reply_to, group_id)
            if reply_to_self:
                reason = "追问（关键词）"
        followup_reason = None
        if not reason and not mentioned:
            followup_reason = self.followup.match(
                group_id, user_id, text, has_content=bool(images or forwards),
                quoted=reply_to_self)
            if followup_reason:
                reason = followup_reason
                entry["followup"] = True
        if not reason:
            return
        entry["triggered"] = True
        self._cancel_idle_image_tasks()
        if not text and not mentioned and not followup_reason and not reply_to_self:
            return
        trigger_key = (group_id, str(message_id or ""))
        if message_id and self._trigger_once.is_duplicate(trigger_key):
            log.info("[触发] 同一条消息已触发过机器人，忽略：群 %s / %s", group_id, message_id)
            return
        if not followup_reason:
            # 新的基础触发立即抢走当前群的话头；其他人单纯发言不会走到这里
            self.followup.open(group_id, user_id, reason)

        with self._stats_lock:
            self.stats["triggers"] += 1
        self._last_trigger_at = time.time()
        with self._autonomous_lock:
            self._group_last_trigger[group_id] = self._last_trigger_at
        self._schedule_spontaneous()
        log.info("[触发] 群 %s | %s(%s)：%s | 原因：%s", group_id, nickname, user_id,
                 _preview(text), reason)

        allowed, why = self.limiter.try_acquire(
            group_id, user_id, bypass_cooldown=bool(followup_reason),
            bypass_interval=bool(followup_reason))
        if not allowed:
            log.info("[限流] 群 %s：%s", group_id, why)
            self._increment("skipped")
            return

        self._clear_pending_followup(group_id)
        self._note_context_activity(group_id)
        if followup_reason:
            self.followup.consume(group_id)
        priority = 1 if followup_reason else 0
        if professional:
            self._professional_message_id = str(message_id or "")
            self._professional_active.set()
            priority = -1
            log.info("[专业模式] 已暂停其他触发，等待本轮全部输出完成")
        if not self._enqueue_model_request(entry, group_id, priority):
            self._increment("skipped")
            if professional:
                self._professional_active.clear()
                self._professional_message_id = ""

    def _collect_user_images(self, group_id, user_id, limit_messages=5):
        if not user_id:
            return []
        recent = self.memory.recent(group_id, 200)
        messages = [item for item in recent if int(item.get("user_id") or 0) == int(user_id)]
        images = []
        for item in messages[-max(1, int(limit_messages)):]:
            images.extend(item.get("images") or [])
        return images

    def _select_input_images(self, entry, group_id):
        limit = max(1, int(self.cfg["lm_studio"].get("image_user_messages", 5)))
        cap = max(1, int(self.cfg["lm_studio"].get("image_selected_max", 5)))
        trigger_user = int(entry.get("user_id") or 0)
        trigger_images = self._collect_user_images(group_id, trigger_user, limit)
        quoted_id = str(entry.get("reply_to") or "")
        if not quoted_id:
            selected = trigger_images[-cap:]
        else:
            quoted_raw = None
            for item in self.memory.recent(group_id, 200):
                if str(item.get("message_id") or "") == quoted_id:
                    quoted_raw = item
                    break
            if quoted_raw is None:
                try:
                    quoted_raw = self.onebot.get_msg(quoted_id)
                except Exception:
                    quoted_raw = None
            quoted_user = int((quoted_raw or {}).get("user_id") or 0)
            if not quoted_user:
                sender = (quoted_raw or {}).get("sender") or {}
                quoted_user = int(sender.get("user_id") or 0)
            quoted_images = self._collect_user_images(group_id, quoted_user, limit)
            if quoted_raw:
                raw_images = extract_image_segments(quoted_raw.get("message"))
                quoted_images.extend(raw_images)
            quoted_images = self._dedupe_images(quoted_images)
            if len(quoted_images) >= cap:
                selected = quoted_images[-cap:]
            else:
                selected = quoted_images + trigger_images[-(cap - len(quoted_images)):]
        selected = self._dedupe_images(selected)
        log.info("[图片] 群 %s 触发者最近 %d 条消息 %d 张，引用优先后选择 %d 张",
                 group_id, limit, len(trigger_images), len(selected[-cap:]))
        return selected[-cap:]

    @staticmethod
    def _dedupe_images(images):
        seen = set()
        result = []
        for image in images or []:
            if not isinstance(image, dict):
                continue
            key = (image.get("url"), image.get("file"))
            if key in seen:
                continue
            seen.add(key)
            result.append(image)
        return result

    def _collect_save_images(self, entry, group_id):
        infos = []
        seen = set()

        def add(items):
            for info in items or []:
                key = (info.get("url") or "", info.get("file") or "")
                if key not in seen:
                    seen.add(key)
                    infos.append(info)

        add(entry.get("images") or [])
        add(self._expand_forward_images(entry.get("forwards") or [], group_id))

        reply_to = str(entry.get("reply_to") or "")
        if reply_to:
            try:
                raw = self.onebot.get_msg(reply_to)
                sender = raw.get("sender") or {}
                quoted_user = _as_int(raw.get("user_id") or sender.get("user_id"), 0)
                if not (self.self_id and quoted_user == self.self_id):
                    add(extract_image_segments(raw.get("message")))
                    add(self._expand_forward_images(
                        extract_forward_segments(raw.get("message")), group_id))
            except Exception as exc:  # noqa: BLE001
                log.warning("[存图] 读取引用消息失败：%s", exc)

        if not infos:
            for item in reversed(self.memory.recent(group_id, 8)):
                if item.get("is_self"):
                    continue
                if str(item.get("message_id")) == str(entry.get("message_id")):
                    continue
                before = len(infos)
                add(item.get("images") or [])
                add(self._expand_forward_images(item.get("forwards") or [], group_id))
                if len(infos) > before:
                    break

        return infos

    def _mark_image_inputs_failed(self, group_id, message_id):
        key = (group_id, str(message_id or ""))
        with self._image_inputs_lock:
            self._image_inputs_cache[key] = False

    def _save_marked_images(self, entry, group_id, markers):
        key = (group_id, str(entry.get("message_id") or ""))
        with self._image_inputs_lock:
            infos = self._image_inputs_cache.pop(key, None)
        if infos is False:
            log.warning("[存图] 本轮多模态图片输入失败，忽略存图编号，避免顺序错位")
            return []
        if not infos:
            infos = self._collect_save_images(entry, group_id)
        if not infos:
            log.info("[存图] 模型想收图，但当前没有可读取的图片")
            return []
        saved = []
        limit = max(1, int(self.cfg["images"].get("max_per_save", 5)))
        for marker_index, marker in enumerate(markers[:limit]):
            position = marker.get("position") if isinstance(marker, dict) else None
            image_index = (position - 1) if isinstance(position, int) and position > 0 else marker_index
            if image_index < 0 or image_index >= len(infos):
                log.warning("[存图] 第 %s 张图片不存在，当前可用 %d 张", position or image_index + 1, len(infos))
                continue
            name = marker.get("name") if isinstance(marker, dict) else str(marker)
            description = marker.get("description", "") if isinstance(marker, dict) else ""
            favorite = marker.get("favorite", 50) if isinstance(marker, dict) else 50
            if not description:
                description = "肥鱼收下的群图片"
            try:
                raw = self._read_image_bytes(infos[image_index])
                saved.append(self.images.save_bytes(
                    raw,
                    sanitize_image_name(name, "图片_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")),
                    description,
                    {"group_id": group_id, "user_id": entry.get("user_id"),
                     "message_id": entry.get("message_id")},
                    favorite=favorite,
                ))
            except Exception as exc:  # noqa: BLE001
                log.warning("[存图] 模型标记收图失败：%s", exc)
        if saved:
            log.info("[存图] 通过模型标记收下 %d 张：%s", len(saved),
                     "、".join(str(item.get("name")) for item in saved))
        return saved

__all__ = [name for name in globals() if not name.startswith("__")]
