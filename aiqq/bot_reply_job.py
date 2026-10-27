#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_reply_job：单条触发回复任务的调度与上下文写入。"""
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
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import TimedRotatingFileHandler






from .config import log
from .voice import strip_voice_marker
from .llm import FatalRestartError, LMStudioError
from .onebot import OneBotError
from .reply import strip_speaker_labels
from .text import _preview

class BotReplyJobMixin:
    @staticmethod
    def _reply_fingerprint(text):
        value = str(text or "")
        value = re.sub(r"/\s*(?:SAVIMG|IMG|VOICE)[^/\r\n]*/", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"/Enter/", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"[\s，。！？!?、,.…~～:：]+", "", value)
        return value

    def _is_duplicate_reply(self, group_id, text):
        fingerprint = self._reply_fingerprint(text)
        min_chars = max(1, int(self.cfg["reply"].get("duplicate_reply_min_chars", 20) or 20))
        if len(fingerprint) < min_chars:
            return False
        window = max(0.0, float(self.cfg["reply"].get("duplicate_reply_window_sec", 120) or 120))
        ratio = max(0.0, min(1.0, float(self.cfg["reply"].get("duplicate_reply_ratio", 0.86) or 0.86)))
        now = time.time()
        with self._recent_reply_lock:
            items = [
                item for item in (self._recent_replies.get(group_id) or [])
                if now - item[0] <= window
            ]
            self._recent_replies[group_id] = items
            for _stamp, previous in items:
                if previous == fingerprint:
                    return True
                if SequenceMatcher(None, previous, fingerprint).ratio() >= ratio:
                    return True
        return False

    def _remember_sent_reply(self, group_id, text):
        fingerprint = self._reply_fingerprint(text)
        if not fingerprint:
            return
        now = time.time()
        with self._recent_reply_lock:
            items = list(self._recent_replies.get(group_id) or [])
            items.append((now, fingerprint))
            self._recent_replies[group_id] = items[-5:]

    def _reply_job(self, entry, group_id):
        started = time.time()
        if entry.get("blocked_by_blacklist") or entry.get("blocked_by_context_save"):
            log.info("[维护] 丢弃已排队的回复：群 %s / 用户 %s",
                     group_id, entry.get("user_id") or 0)
            return
        with self._busy_lock:
            self._busy_groups.add(group_id)
        try:
            with self._model_lock:
                reply = self._generate_reply(entry, group_id)
            if entry.get("blocked_by_blacklist") or entry.get("blocked_by_context_save"):
                log.info("[维护] 丢弃运行中的回复：群 %s / 用户 %s",
                         group_id, entry.get("user_id") or 0)
                return
            if reply:
                reply = strip_speaker_labels(reply, (self.self_nick,))
            if not reply:
                self._increment("skipped")
                log.info("[跳过] 模型没有给出可发送的回复。")
                return
            thinking_sec = max(0.0, time.time() - started)
            user_text = entry.get("text") or (
                "[图片]" if entry.get("images") else "[触发]")
            if entry.get("images"):
                self._mark_images_handled(entry, group_id)
            if self._is_duplicate_reply(group_id, reply):
                self._increment("skipped")
                log.info("[去重] 群 %s 短时间内重复回复，已跳过：%s",
                         group_id, _preview(reply, 80))
                return
            self._send_reply(entry, group_id, reply, thinking_sec=thinking_sec)
            self._remember_sent_reply(group_id, reply)
            if self.cfg["lm_studio"].get("context_save_enabled", True):
                if entry.get("professional"):
                    self.professional_context.append_exchange(
                        entry.get("user_id") or 0, group_id, user_text, reply,
                        user_name=entry.get("nickname") or "",
                        mentions=entry.get("mentions") or [])
                else:
                    context_reply = strip_voice_marker(reply)[1] or reply
                    count = self.group_context.append_exchange(
                        group_id, user_text, context_reply,
                        user_name=entry.get("nickname") or "",
                        mentions=entry.get("mentions") or [])
                    if count > int(self.cfg["lm_studio"].get("group_context_max_chars", 5000)):
                        self._start_context_consolidation(group_id)
        except FatalRestartError:
            log.warning("[致命] 已触发重启，停止本条回复")
        except LMStudioError as exc:
            if entry.get("blocked_by_blacklist"):
                log.info("[黑名单] 模型已强制重启，丢弃该用户回复")
                return
            self._record_error(exc)
            log.error("[LM Studio] %s", exc)
            self._fallback_reply(entry, group_id)
        except OneBotError as exc:
            self._record_error(exc)
            log.error("[OneBot] %s", exc)
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            log.error("回复流程异常：%s\n%s", exc, traceback.format_exc())
        finally:
            with self._stats_lock:
                self.stats["last_latency_sec"] = round(time.time() - started, 2)
            try:
                self.limiter.mark_complete(group_id, entry.get("user_id") or 0)
                self.followup.refresh_after_reply(group_id, entry.get("user_id") or 0)
            except Exception as exc:  # noqa: BLE001
                log.warning("[限流] 回复完成计数失败：%s", exc)
            with self._busy_lock:
                self._busy_groups.discard(group_id)
            if entry.get("professional"):
                self._professional_active.clear()
                self._professional_message_id = ""
                log.info("[专业模式] 全部输出完成，恢复其他触发")
            log.debug("[完成] 群 %s 回复流程结束", group_id)


__all__ = [name for name in globals() if not name.startswith("__")]
