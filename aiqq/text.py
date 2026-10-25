#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ text：触发匹配、追问、限流、去重与群消息缓存。"""
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



from .config import CQ_ANY_RE, DEDUP_CACHE_SIZE

def strip_cq(text):
    return CQ_ANY_RE.sub("", text or "")


# --------------------------------------------------------------------------- #
# 触发与限流
# --------------------------------------------------------------------------- #
def contains_blocked_keyword(text, keywords):
    folded = str(text or "").casefold()
    return any(keyword.casefold() in folded for keyword in keywords if keyword)


class TriggerMatcher(object):
    def __init__(self, cfg):
        self.keywords = list(cfg.get("keywords") or [])
        self.block_keywords = list(cfg.get("block_keywords") or [])
        self.match_mention = bool(cfg.get("match_mention", True))
        self.regex = re.compile(cfg["regex"], re.IGNORECASE) if cfg.get("regex") else None

    def blocked(self, text):
        return contains_blocked_keyword(text, self.block_keywords)

    def match(self, text, mentioned):
        """命中返回可读原因，否则返回 None。黑名单优先于所有触发方式。"""
        if self.blocked(text):
            return None
        if mentioned and self.match_mention:
            return "@了你"
        folded = (text or "").casefold()
        for keyword in self.keywords:
            if keyword and keyword.casefold() in folded:
                return "关键词「%s」" % keyword
        if self.regex is not None and self.regex.search(text or ""):
            return "正则匹配"
        return None


class FollowupTracker(object):
    """主触发后：30 秒任意接话，90 秒内引用/次级关键词接话。"""

    def __init__(self, cfg, clock=None):
        self.enabled = bool(cfg.get("followup_enabled", True))
        self.keywords = [str(k).strip() for k in (cfg.get("followup_keywords") or []) if str(k).strip()]
        self.block_keywords = [
            str(k).strip() for k in (cfg.get("block_keywords") or []) if str(k).strip()
        ]
        self.window = max(0.0, float(cfg.get("followup_window_sec", 30)))
        self.extended = max(self.window, float(cfg.get("followup_extended_sec", 90)))
        self.max_messages = max(0, int(cfg.get("followup_max_messages", 0)))
        self.same_sender = bool(cfg.get("followup_same_sender", True))
        self.any_message = bool(cfg.get("followup_any_message", True))
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._states = {}

    def open(self, group_id, user_id, reason):
        if not self.enabled or self.extended <= 0:
            return
        now = self._clock()
        with self._lock:
            self._states[group_id] = {
                "expires_at": now + self.window,
                "expires_soft": now + self.extended,
                "remaining": self.max_messages if self.max_messages > 0 else -1,
                "sender_id": user_id,
                "reason": reason,
            }

    def match(self, group_id, user_id, text, has_content=False, quoted=False):
        if not self.enabled:
            return None
        if contains_blocked_keyword(text, self.block_keywords):
            return None
        if not text and not has_content and not quoted:
            return None
        now = self._clock()
        folded = text.casefold()
        with self._lock:
            state = self._states.get(group_id)
            if not state:
                return None
            if now > state["expires_soft"] or state["remaining"] == 0:
                self._states.pop(group_id, None)
                return None
            if self.same_sender and user_id != state["sender_id"]:
                return None
            if self.any_message and now <= state["expires_at"]:
                return "追问（关键词）"
            if quoted:
                return "追问（引用）"
            if not text:
                return None
            for keyword in self.keywords:
                if keyword and keyword.casefold() in folded:
                    return "追问（次级关键词「%s」）" % keyword
        return None

    def consume(self, group_id):
        if not self.enabled:
            return
        now = self._clock()
        with self._lock:
            state = self._states.get(group_id)
            if not state:
                return
            if state["remaining"] > 0:
                state["remaining"] -= 1
                if state["remaining"] <= 0:
                    self._states.pop(group_id, None)
                    return
            state["expires_at"] = now + self.window
            state["expires_soft"] = now + self.extended

    def refresh_after_reply(self, group_id, user_id):
        """等当前回复真正发完后，再重新计算 30/90 秒窗口。"""
        if not self.enabled:
            return
        now = self._clock()
        with self._lock:
            state = self._states.get(group_id)
            if not state or state.get("sender_id") != user_id:
                return
            state["expires_at"] = now + self.window
            state["expires_soft"] = now + self.extended


class RateLimiter(object):
    def __init__(self, cfg):
        self.min_interval = float(cfg.get("min_interval_sec", 2.0))
        self.cooldown = float(cfg.get("per_user_cooldown_sec", 8.0))
        self.max_per_minute = int(cfg.get("max_replies_per_minute", 8))
        self._lock = threading.Lock()
        self._user_last = {}
        self._group_times = defaultdict(deque)
        self._group_last = {}

    def try_acquire(self, group_id, user_id, bypass_cooldown=False, bypass_interval=False):
        now = time.monotonic()
        with self._lock:
            user_key = (group_id, user_id)
            last_user = self._user_last.get(user_key, 0.0)
            if not bypass_cooldown and now - last_user < self.cooldown:
                return False, "该用户仍在冷却（%.0f 秒）" % (self.cooldown - (now - last_user))
            last_group = self._group_last.get(group_id, 0.0)
            if not bypass_interval and now - last_group < self.min_interval:
                return False, "距离上一条回复不足 %.1f 秒" % self.min_interval
            times = self._group_times[group_id]
            while times and now - times[0] > 60.0:
                times.popleft()
            if len(times) >= self.max_per_minute:
                return False, "本群每分钟回复数已达上限（%d）" % self.max_per_minute
            # 占用配额，避免并发触发绕过限制
            self._user_last[user_key] = now
            self._group_last[group_id] = now
            times.append(now)
            return True, ""

    def mark_complete(self, group_id, user_id):
        """回复全部发完后，才从此刻开始计算用户/群冷却。"""
        now = time.monotonic()
        with self._lock:
            self._user_last[(group_id, user_id)] = now
            self._group_last[group_id] = now
            times = self._group_times[group_id]
            while times and now - times[0] > 60.0:
                times.popleft()
            if times:
                times[-1] = now
            else:
                times.append(now)


class DedupCache(object):
    def __init__(self, size=DEDUP_CACHE_SIZE):
        self.size = size
        self._lock = threading.Lock()
        self._order = deque()
        self._seen = set()

    def is_duplicate(self, key):
        if key is None:
            return False
        with self._lock:
            if key in self._seen:
                return True
            self._seen.add(key)
            self._order.append(key)
            while len(self._order) > self.size:
                self._seen.discard(self._order.popleft())
            return False

def _preview(text, limit=80):
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [name for name in globals() if not name.startswith("__")]
