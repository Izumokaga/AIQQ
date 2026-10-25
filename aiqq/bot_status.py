#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_status：上下文保存、统计与运行状态快照。"""
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

class BotStatusMixin:
    def _note_context_activity(self, group_id):
        with self._context_save_lock:
            self._context_last_activity[group_id] = time.time()

    def _mark_context_dirty(self, group_id):
        with self._context_save_lock:
            self._context_dirty[group_id] = self._context_dirty.get(group_id, 0) + 1

    def _save_context_group(self, group_id, version=None):
        self.user_context.save_all()
        with self._context_save_lock:
            self._context_dirty.pop(group_id, None)
            self._context_last_save[group_id] = time.time()
        return "user"

    def _save_idle_contexts(self):
        lm = self.cfg["lm_studio"]
        if not lm.get("context_save_enabled", True):
            return 0
        idle = max(0, int(lm.get("context_save_idle_sec", 300)))
        if idle <= 0:
            return 0
        now = time.time()
        with self._context_save_lock:
            candidates = []
            for group_id, version in self._context_dirty.items():
                last_activity = self._context_last_activity.get(group_id, 0)
                last_save = self._context_last_save.get(group_id, 0)
                if now - max(last_activity, last_save) >= idle:
                    candidates.append((group_id, version, last_activity))
        saved = 0
        for group_id, version, _ in candidates:
            try:
                if self._save_context_group(group_id, version):
                    saved += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("[上下文] 保存群 %s 失败：%s", group_id, exc)
        return saved

    def _save_all_contexts(self):
        if not self.cfg["lm_studio"].get("context_save_enabled", True):
            return
        self.user_context.save_all()
        with self._context_save_lock:
            self._context_dirty.clear()

    def _context_saver_loop(self):
        idle = max(0, int(self.cfg["lm_studio"].get("context_save_idle_sec", 300)))
        interval = max(5, min(30, idle / 5.0)) if idle else 30
        while not self._stopping.wait(interval):
            try:
                self._save_idle_contexts()
            except Exception as exc:  # noqa: BLE001
                log.warning("[上下文] 空闲保存异常：%s", exc)

    def save_context_now(self):
        """立即保存按用户归档的上下文。"""
        self.user_context.save_all()
        count = self.user_context.count()
        log.info("[上下文] 手动保存完成：%d 个用户上下文", count)
        return {"ok": True, "saved": count, "groups": []}

    def delete_user_context(self, user_id):
        removed = self.user_context.clear_user(user_id)
        with self._context_lock:
            self._context_stats.clear()
        log.info("[上下文] 已删除用户 %s 的上下文：%d 个快照文件", user_id, removed)
        return {"ok": True, "removed": removed, "user_id": int(user_id or 0)}

    def delete_professional_context(self, user_id):
        removed = self.professional_context.clear_user(user_id)
        log.info("[上下文] 已删除用户 %s 的专业模式上下文：%d 个快照文件",
                 int(user_id or 0), removed)
        return {"ok": True, "removed": removed, "user_id": int(user_id or 0)}

    def delete_group_context(self, group_id):
        removed = self.user_context.clear_group(group_id)
        with self._context_lock:
            self._context_stats.clear()
        log.info("[上下文] 已删除群 %s 的上下文：%d 个快照文件", group_id, removed)
        return {"ok": True, "removed": removed, "group_id": int(group_id or 0)}

    def clear_context_snapshots(self):
        """清空上下文快照文件和内存缓存，不影响人设与图库。"""
        users = self.user_context.count()
        removed = self.user_context.clear_all()
        with self._context_save_lock:
            self._context_dirty.clear()
            self._context_last_save.clear()
        with self._context_lock:
            self._context_stats.clear()
        log.info("[上下文] 已清空上下文快照：%d 个用户，删除 %d 个快照文件", users, removed)
        return {"ok": True, "removed": removed, "users": users}

    def _record_context(self, group_id, data):
        data = dict(data)
        data["group_id"] = group_id
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._context_lock:
            self._context_stats[group_id] = data

    def stats_snapshot(self):
        with self._stats_lock:
            snapshot = dict(self.stats)
        with self._context_lock:
            contexts = {str(key): dict(value) for key, value in self._context_stats.items()}
        latest_context = max(contexts.values(), key=lambda item: item.get("updated_at", "")) if contexts else {}
        with self._busy_lock:
            busy_groups = len(self._busy_groups)
        with self._connectivity_lock:
            connectivity = dict(self._connectivity)
        with self._model_reload_lock:
            model_reloading = self._model_reloading
            model_reload_error = self._model_reload_error
            model_reloaded_at = self._model_reloaded_at
        with self._raw_reply_lock:
            raw_reply = self._last_raw_reply
            raw_group = self._last_raw_reply_group
            raw_at = self._last_raw_reply_at
        with self._autonomous_lock:
            sleeping_until = self._sleeping_until
            next_spontaneous_at = self._next_spontaneous_at
            spontaneous_waiting = self._spontaneous_waiting
            spontaneous_at = self._spontaneous_at
            spontaneous_groups = sorted(self._spontaneous_groups)
            model_unloaded = self._model_unloaded
        now = time.time()
        wait_sec = int(self.cfg["lm_studio"].get("spontaneous_wait_sec", 300))
        if sleeping_until > now:
            sleep_countdown_sec = 0
            sleep_countdown_mode = "sleeping"
        elif spontaneous_waiting:
            sleep_countdown_sec = max(0, int(spontaneous_at + wait_sec - now))
            sleep_countdown_mode = "waiting"
        elif next_spontaneous_at > now:
            sleep_countdown_sec = max(0, int(next_spontaneous_at + wait_sec - now))
            sleep_countdown_mode = "scheduled"
        else:
            sleep_countdown_sec = 0
            sleep_countdown_mode = "none"
        if self._fatal_restarting:
            model_status = "restarting"
        elif model_reloading:
            model_status = "reloading"
        elif model_unloaded:
            model_status = "unloaded"
        elif busy_groups > 0:
            model_status = "generating"
        else:
            model_status = "loaded"
        quiet_active = self._in_sleep_prep_quiet()
        idle_enabled = bool(self.cfg["lm_studio"].get("idle_image_reply_enabled", True))
        idle_interval = int(self.cfg["lm_studio"].get("idle_image_idle_sec", 60))
        idle_poll = int(self.cfg["lm_studio"].get("idle_image_poll_sec", 10))
        idle_next_at = self._last_idle_image_at + idle_interval
        if not idle_enabled:
            idle_state = "已关闭"
            idle_countdown = 0
        elif not self._idle_image_hour_allowed():
            idle_state = "非识图时段（7-22）"
            idle_countdown = 0
        elif sleeping_until > now:
            idle_state = "睡眠中"
            idle_countdown = 0
        elif spontaneous_waiting:
            idle_state = "睡眠等待中"
            idle_countdown = 0
        elif self._idle_image_processing:
            idle_state = "识图中"
            idle_countdown = 0
        elif self._idle_image_startup_reset:
            idle_state = "等待图片"
            idle_next_at = self._last_idle_image_poll_at + idle_poll
            idle_countdown = max(0, int(idle_next_at - now))
        elif now - self._last_idle_image_at < idle_interval:
            idle_state = "识图冷却"
            idle_next_at = self._last_idle_image_at + idle_interval
            idle_countdown = max(0, int(idle_next_at - now))
        else:
            idle_state = "等待图片"
            idle_next_at = self._last_idle_image_poll_at + idle_poll
            idle_countdown = max(0, int(idle_next_at - now))
        voice_enabled = bool(self.cfg["voice"].get("enabled", True) and
                             self.cfg["voice"].get("qwen_tts_enabled", False))
        voice_idle = int(self.cfg["voice"].get("qwen_tts_unload_idle_sec", 60))
        voice_next_at = self._last_voice_done_at + voice_idle if self._last_voice_done_at else 0
        if not voice_enabled:
            voice_state = "未启用"
            voice_countdown = 0
        elif self.voice.is_busy():
            voice_state = "语音生成中"
            voice_countdown = 0
        elif self._voice_model_unloaded:
            voice_state = "已卸载"
            voice_countdown = 0
        elif not self._last_voice_done_at:
            voice_state = "等待语音"
            voice_countdown = 0
        elif now < voice_next_at:
            voice_state = "卸载倒计时"
            voice_countdown = max(0, int(voice_next_at - now))
        else:
            voice_state = "等待卸载"
            voice_countdown = 0
        snapshot.update({
            "voice_model_enabled": voice_enabled,
            "voice_model_state": voice_state,
            "voice_model_countdown_sec": voice_countdown,
            "voice_model_next_at": voice_next_at,
            "voice_model_unload_idle_sec": voice_idle,
            "voice_model_unloaded": self._voice_model_unloaded,
            "idle_image_enabled": idle_enabled,
            "idle_image_state": idle_state,
            "idle_image_countdown_sec": idle_countdown,
            "idle_image_next_at": idle_next_at,
            "idle_image_interval_sec": idle_interval,
            "idle_image_poll_sec": idle_poll,
            "idle_image_processing": self._idle_image_processing,
            "idle_image_last_at": self._last_idle_image_at,
            "idle_image_last_poll_at": self._last_idle_image_poll_at,

            "running_sec": int(time.time() - self._started_at),
            "lm_runtime_sec": (
                int(time.time() - self._lm_connected_since)
                if self._lm_connected_since else 0
            ),
            "pid": os.getpid(),
            "self_id": self.self_id,
            "self_nick": self.self_nick,
            "groups": sorted(self.groups),
            "group_names": {str(group_id): self.group_names.get(group_id, "")
                            for group_id in sorted(self.groups)},
            "model": self.lm.model,
            "model_status": model_status,
            "model_reloading": model_reloading,
            "model_reload_error": model_reload_error,
            "model_reloaded_at": model_reloaded_at,
            "last_raw_reply": raw_reply,
            "last_raw_reply_group": raw_group,
            "last_raw_reply_at": raw_at,
            "sleeping_until": sleeping_until,
            "next_spontaneous_at": next_spontaneous_at,
            "spontaneous_waiting": spontaneous_waiting,
            "spontaneous_at": spontaneous_at,
            "spontaneous_groups": spontaneous_groups,
            "sleep_countdown_sec": sleep_countdown_sec,
            "sleep_countdown_mode": sleep_countdown_mode,
            "sleep_prep_quiet_active": quiet_active,
            "sleep_prep_quiet_start_hour": int(
                self.cfg["lm_studio"].get("sleep_prep_quiet_start_hour", 7)),
            "sleep_prep_quiet_end_hour": int(
                self.cfg["lm_studio"].get("sleep_prep_quiet_end_hour", 22)),
            "model_unloaded": model_unloaded,
            "lm_studio": self.lm.base_url,
            "onebot_api": self.onebot.api_url,
            "busy_groups": busy_groups,
            "queue_size": self._queue.qsize(),
            "model_queue_size": self._model_queue_size(),
            "image_count": self.images.count(),
            "context": latest_context,
            "contexts": contexts,
        })
        snapshot.update(connectivity)
        return snapshot

    def _remember_raw_reply(self, group_id, text):
        text = str(text or "")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._raw_reply_lock:
            self._last_raw_reply = text
            self._last_raw_reply_group = int(group_id)
            self._last_raw_reply_at = stamp
            if text:
                history = self._raw_history.get(int(group_id))
                if history is None:
                    history = deque(maxlen=self._raw_history_limit)
                    self._raw_history[int(group_id)] = history
                history.append({"at": stamp, "text": text})

    def raw_history_snapshot(self):
        with self._raw_reply_lock:
            groups = []
            for group_id, history in self._raw_history.items():
                if not history:
                    continue
                groups.append({
                    "group_id": int(group_id),
                    "name": self.group_names.get(int(group_id), ""),
                    "entries": [dict(item) for item in history],
                })
        groups.sort(key=lambda item: item["entries"][-1]["at"], reverse=True)
        return {"groups": groups, "limit": self._raw_history_limit}

    def _increment(self, key):
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + 1

    def _record_error(self, exc):
        with self._stats_lock:
            self.stats["errors"] += 1
            self.stats["last_error"] = "%s: %s" % (type(exc).__name__, exc)


__all__ = [name for name in globals() if not name.startswith("__")]
