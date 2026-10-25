#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_outgoing：图片、语音、文字与专业模式输出。"""
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





from .config import log
from .voice import has_voice_marker, split_voice_units, strip_voice_marker
from .text import _preview
from .onebot import OneBotError
from .reply import cap_messages, ignore_comma_breaks, remove_linebreaks, segment_delay, split_by_marker, split_text, strip_leading_breaks, strip_media_placeholders, strip_periods, strip_speaker_labels, strip_trailing_breaks
from .images import parse_image_markers, parse_save_markers, strip_incomplete_enter_markers

class BotOutgoingMixin:
    def _remember_self_reply(self, group_id, text, message_id=None):
        text = str(text or "").strip()
        if not text:
            return
        entry = {
            "message_id": str(message_id or "self-%s-%s" % (group_id, time.time_ns())),
            "user_id": self.self_id,
            "nickname": self.self_nick,
            "text": text,
            "mentioned": False,
            "images": [],
            "forwards": [],
            "reply_to": "",
            "time": int(time.time()),
            "is_self": True,
        }
        self.memory.add(group_id, entry)
        self._mark_context_dirty(group_id)

    def _send_gallery_images(self, entry, group_id, image_names, quote_used=False):
        to_message = bool(self.cfg["reply"].get("to_message", False))
        sent_images = 0
        for name in image_names:
            image_path = self.images.path_for(name)
            if not image_path:
                log.warning("[图片库] 找不到图片：%s", name)
                continue
            segments = []
            if not quote_used and to_message:
                segments.append({"type": "reply", "data": {"id": str(entry["message_id"])}})
            image_ref = "file:///" + image_path.replace("\\", "/")
            segments.append({"type": "image", "data": {"file": image_ref}})
            try:
                result = self.onebot.send_group_msg(group_id, segments)
            except OneBotError as exc:
                try:
                    with open(image_path, "rb") as fh:
                        raw_image = fh.read()
                    segments[-1] = {
                        "type": "image",
                        "data": {"file": "base64://" + base64.b64encode(raw_image).decode("ascii")},
                    }
                    result = self.onebot.send_group_msg(group_id, segments)
                except Exception as fallback_exc:  # noqa: BLE001
                    log.error("[图片库] 发送图片 %s 失败：%s；base64 重试也失败：%s",
                              name, exc, fallback_exc)
                    continue
            quote_used = True
            sent_images += 1
            self._increment("replies")
            with self._stats_lock:
                self.stats["last_reply_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info("[回复] 群 %s：[图片 %s]", group_id, name)
        return sent_images, quote_used

    def _send_voice_reply(self, entry, group_id, text, quote_used=False,
                          voice_params=None):
        try:
            params = voice_params or {}
            path, duration = self.voice.synthesize(
                text,
                rate=params.get("rate"),
                pitch=params.get("pitch"),
                volume=params.get("volume"),
                instruct=params.get("instruct"))
        except Exception as exc:  # noqa: BLE001
            log.warning("[语音] 合成失败，回退为文字：%s", exc)
            return False
        self._last_voice_done_at = time.time()
        self._voice_model_unloaded = False
        try:
            delay = max(1.0, duration) + random.uniform(1.0, 3.0)
            log.info("[语音] 时长 %.1f 秒，等待 %.1f 秒后发送", duration, delay)
            time.sleep(delay)
            segments = []
            if not quote_used and self.cfg["reply"].get("to_message", False):
                segments.append({"type": "reply", "data": {"id": str(entry["message_id"])}})
            with open(path, "rb") as fh:
                raw = fh.read()
            segments.append({
                "type": "record",
                "data": {"file": "base64://" + base64.b64encode(raw).decode("ascii")},
            })
            try:
                result = self.onebot.send_group_msg(group_id, segments)
            except OneBotError:
                file_ref = "file:///" + path.replace("\\", "/")
                segments[-1] = {"type": "record", "data": {"file": file_ref}}
                result = self.onebot.send_group_msg(group_id, segments)
            self._increment("replies")
            self._remember_self_reply(
                group_id, "[语音] " + text, self._message_id_from_result(result))
            with self._stats_lock:
                self.stats["last_reply_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info("[语音] 已发送语音：%s", _preview(text, 80))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("[语音] 发送失败，回退为文字：%s", exc)
            return False
        finally:
            self.voice.cleanup(path)

    def _send_single_text(self, entry, group_id, text, quote_used=False):
        segments = []
        if not quote_used and self.cfg["reply"].get("to_message", False):
            segments.append({"type": "reply", "data": {"id": str(entry["message_id"])}})
        segments.append({"type": "text", "data": {"text": text}})
        result = self.onebot.send_group_msg(group_id, segments)
        self._increment("replies")
        self._remember_self_reply(
            group_id, text, self._message_id_from_result(result))
        with self._stats_lock:
            self.stats["last_reply_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log.info("[回复] 群 %s：%s", group_id, _preview(text, 120))

    def _send_professional_reply(self, entry, group_id, text, thinking_sec=0.0):
        # 专业模式只按换行分段；不解析、不删除任何标记或标点。
        parts = [part for part in re.split(r"\r\n|\r|\n", str(text or "")) if part.strip()]
        if not parts:
            log.info("[专业模式] 回复没有可发送内容")
            return
        delay_args = {
            "min_sec": self.cfg["reply"].get("chunk_delay_sec", 1.0),
            "max_sec": self.cfg["reply"].get("chunk_delay_max_sec", 60.0),
            "rate_min": self.cfg["reply"].get("chunk_chars_per_sec_min", 3.0),
            "rate_max": self.cfg["reply"].get("chunk_chars_per_sec_max", 5.0),
        }
        first_delay = segment_delay(
            parts[0], base_sec=self.cfg["reply"].get("first_delay_sec", 2.0), **delay_args)
        wait_delay = max(0.0, first_delay - max(0.0, float(thinking_sec or 0.0)))
        log.info("[专业模式] 首条模拟等待 %.1f 秒（已思考 %.1f 秒，实际等待 %.1f 秒）",
                 first_delay, float(thinking_sec or 0.0), wait_delay)
        time.sleep(wait_delay)
        quote_used = False
        for index, part in enumerate(parts):
            self._send_single_text(entry, group_id, part, quote_used=quote_used)
            quote_used = True
            if index < len(parts) - 1:
                delay = segment_delay(part, **delay_args)
                log.debug("[专业模式] 本段 %d 字，等待 %.1f 秒", len(part), delay)
                time.sleep(delay)

    def _send_reply(self, entry, group_id, text, thinking_sec=0.0):
        limit = int(self.cfg["reply"]["max_chars"])
        max_chunks = int(self.cfg["reply"]["max_chunks"])
        max_messages = int(self.cfg["reply"]["max_messages"])
        marker = self.cfg["reply"].get("split_marker") or ""
        slash_fallback = bool(self.cfg["reply"].get("split_slash_fallback", True))
        to_message = bool(self.cfg["reply"].get("to_message", False))

        if entry.get("professional"):
            self._send_professional_reply(
                entry, group_id, text, thinking_sec=thinking_sec)
            return

        text, save_names = parse_save_markers(text)
        text, image_names = parse_image_markers(text)
        text = strip_incomplete_enter_markers(text)
        text = strip_leading_breaks(text, marker)
        text = strip_trailing_breaks(text)
        text = strip_media_placeholders(text)
        text = strip_speaker_labels(text, (self.self_nick,))
        voice_requested = has_voice_marker(text, marker)
        if save_names:
            # 标记单独出现时只静默收图，不额外发确认文字
            self._save_marked_images(entry, group_id, save_names)
        max_images = max(0, int(self.cfg["images"].get("max_per_reply", 3)))
        image_names = list(image_names[:max_images])
        text = (text or "").strip()
        if not text:
            sent_images, _ = self._send_gallery_images(entry, group_id, image_names)
            if sent_images:
                return
            log.info("[跳过] 回复只包含分段/图片标记，没有可发送内容。")
            return

        if voice_requested:
            voice_units = split_voice_units(text, marker, slash_fallback)
            if voice_units:
                quote_used = False
                image_slot = random.randint(0, len(voice_units)) if image_names else -1
                if image_slot >= 0:
                    log.info("[图片时机] 语音共 %d 段，图片将在第 %d 段前发送",
                             len(voice_units), image_slot)
                for index, (voice_part, voice_instruct) in enumerate(voice_units):
                    voice_part = strip_media_placeholders(voice_part)
                    voice_part = re.sub(r"\s+", " ", voice_part).strip()
                    if index == image_slot and image_names:
                        _, quote_used = self._send_gallery_images(
                            entry, group_id, image_names, quote_used=quote_used)
                    if not voice_part:
                        continue
                    if self._send_voice_reply(
                            entry, group_id, voice_part, quote_used=quote_used,
                            voice_params={"instruct": voice_instruct}):
                        quote_used = True
                    else:
                        # 单段合成失败只把这一段作为一条文字发送，不重新拆分。
                        self._send_single_text(
                            entry, group_id, voice_part, quote_used=quote_used)
                        quote_used = True
                if image_slot == len(voice_units) and image_names:
                    self._send_gallery_images(
                        entry, group_id, image_names, quote_used=quote_used)
                return
            text = strip_voice_marker(text, marker)[1]
        else:
            # 段尾或单独出现的语音提示不触发语音，但仍需从正文中删除。
            text = strip_voice_marker(text, marker)[1]
        text = (text or "").strip()
        if not text:
            sent_images, _ = self._send_gallery_images(entry, group_id, image_names)
            if not sent_images:
                log.info("[跳过] 回复只包含语音提示，没有可发送文字。")
            return

        if self.cfg["reply"].get("ignore_comma_breaks", True):
            text = ignore_comma_breaks(text)
        if self.cfg["reply"].get("remove_linebreaks", True):
            text = remove_linebreaks(text)
        parts = split_by_marker(text, marker, slash_fallback)
        if not parts:
            self._send_gallery_images(entry, group_id, image_names)
            log.info("[跳过] 回复只包含分段标记，没有可发送内容。")
            return
        if len(parts) > 1:
            log.info("[拆分] 拆成 %d 段（标记 %s）", len(parts), marker or "/")

        chunks = []
        for part in parts:
            chunks.extend(split_text(part, limit, max_chunks))
        raw_count = len(chunks)
        chunks = cap_messages(chunks, limit, max_messages)
        if self.cfg["reply"].get("strip_periods", True):
            chunks = [strip_periods(chunk).strip() for chunk in chunks]
            chunks = [chunk for chunk in chunks if chunk]
        if raw_count > len(chunks):
            log.warning("[拆分] 共 %d 条，超过 max_messages=%d，已合并为 %d 条。",
                        raw_count, max_messages, len(chunks))
        if not chunks:
            self._send_gallery_images(entry, group_id, image_names)
            return

        first_delay = segment_delay(
            chunks[0],
            min_sec=self.cfg["reply"].get("chunk_delay_sec", 1.0),
            max_sec=self.cfg["reply"].get("chunk_delay_max_sec", 60.0),
            rate_min=self.cfg["reply"].get("chunk_chars_per_sec_min", 3.0),
            rate_max=self.cfg["reply"].get("chunk_chars_per_sec_max", 5.0),
            base_sec=self.cfg["reply"].get("first_delay_sec", 2.0),
        )
        wait_delay = max(0.0, first_delay - max(0.0, float(thinking_sec or 0.0)))
        log.info("[首句] 输出 %d 字，模拟等待 %.1f 秒（已思考 %.1f 秒，实际等待 %.1f 秒）",
                 len(chunks[0]), first_delay, float(thinking_sec or 0.0), wait_delay)
        time.sleep(wait_delay)

        quote_used = False
        image_slot = random.randint(0, len(chunks)) if image_names else -1
        if image_slot >= 0:
            log.info("[图片时机] 本轮共 %d 段，图片将在第 %d 段前发送（0=开头，%d=结尾）",
                     len(chunks), image_slot, len(chunks))
        for index, chunk in enumerate(chunks):
            if index == image_slot and image_names:
                _, quote_used = self._send_gallery_images(
                    entry, group_id, image_names, quote_used=quote_used)
            segments = []
            if not quote_used and to_message:
                segments.append({"type": "reply", "data": {"id": str(entry["message_id"])}})
                quote_used = True
            segments.append({"type": "text", "data": {"text": chunk}})
            try:
                result = self.onebot.send_group_msg(group_id, segments)
            except OneBotError:
                raise
            self._increment("replies")
            self._remember_self_reply(group_id, chunk, self._message_id_from_result(result))
            with self._stats_lock:
                self.stats["last_reply_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info("[回复] 群 %s：%s", group_id, _preview(chunk, 120))
            if index < len(chunks) - 1:
                delay = segment_delay(
                    chunk,
                    min_sec=self.cfg["reply"].get("chunk_delay_sec", 1.0),
                    max_sec=self.cfg["reply"].get("chunk_delay_max_sec", 60.0),
                    rate_min=self.cfg["reply"].get("chunk_chars_per_sec_min", 3.0),
                    rate_max=self.cfg["reply"].get("chunk_chars_per_sec_max", 5.0),
                )
                log.debug("[分段] 本段 %d 字，等待 %.1f 秒", len(chunk), delay)
                time.sleep(delay)
        if image_slot == len(chunks) and image_names:
            self._send_gallery_images(entry, group_id, image_names, quote_used=quote_used)

    def _fallback_reply(self, entry, group_id):
        error_reply = str(self.cfg["reply"].get("error_reply") or "").strip()
        if not error_reply:
            return
        if self.cfg["reply"].get("remove_linebreaks", True):
            error_reply = remove_linebreaks(error_reply)
        if self.cfg["reply"].get("strip_periods", True):
            error_reply = strip_periods(error_reply)
        try:
            segments = [{"type": "text", "data": {"text": error_reply}}]
            result = self.onebot.send_group_msg(group_id, segments)
            self._increment("replies")
            self._remember_self_reply(group_id, error_reply, self._message_id_from_result(result))
        except OneBotError as exc:
            log.error("发送故障提示失败：%s", exc)

__all__ = [name for name in globals() if not name.startswith("__")]
