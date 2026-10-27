#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_engine：Bot 初始化与各领域 mixin 的组合入口。"""
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




# Bot 只保留初始化和入口所需对象，具体流程由各 mixin 提供。
from .config import BASE_DIR, _as_int, log
from .voice import VoiceEngine
from .text import DedupCache, FollowupTracker, RateLimiter, TriggerMatcher
from .storage import ContextStore, GroupMemory, UserContextStore
from .group_context import GroupContextStore, GroupPersonaStore
from .llm import LMStudioClient
from .onebot import OneBotClient
from .images import ImageLibrary
from .bot_sleep import BotSleepMixin
from .bot_idle import BotIdleMixin
from .bot_lifecycle import BotLifecycleMixin
from .bot_dispatch import BotDispatchMixin
from .bot_messages import BotMessagesMixin
from .bot_reply_job import BotReplyJobMixin
from .bot_persona import BotPersonaMixin
from .bot_generation import BotGenerationMixin
from .bot_outgoing import BotOutgoingMixin
from .bot_connectivity import BotConnectivityMixin
from .bot_status import BotStatusMixin
from .bot_context_maintenance import BotContextMaintenanceMixin

class Bot(BotSleepMixin, BotIdleMixin, BotLifecycleMixin, BotDispatchMixin, BotMessagesMixin, BotReplyJobMixin, BotPersonaMixin, BotGenerationMixin, BotOutgoingMixin, BotConnectivityMixin, BotStatusMixin, BotContextMaintenanceMixin):
    def __init__(self, cfg):
        self.cfg = cfg
        self.self_id = _as_int(cfg["qq"].get("self_id"), 0)
        self.self_nick = "AI 助手"
        self.groups = set(cfg["qq"]["groups"])
        self.ignore_users = set(cfg["qq"].get("ignore_users") or [])
        self.admin_users = set(cfg["qq"].get("admin_users") or [])
        self.admin_groups = set(cfg["qq"].get("admin_groups") or [])
        self.group_names = {}
        self._member_names = {}

        self.onebot = OneBotClient(cfg["onebot"])
        self.lm = LMStudioClient(cfg["lm_studio"])
        self.trigger = TriggerMatcher(cfg["trigger"])
        self.followup = FollowupTracker(cfg["trigger"])
        self.limiter = RateLimiter(cfg["rate_limit"])
        self.dedup = DedupCache()
        self._trigger_once = DedupCache(5000)

        # 启动加载顺序固定为：人设 → 上下文 → 图库
        self._persona_lock = threading.Lock()
        self._persona_cache = (0.0, "")
        persona_text = self._persona_text()
        persona_name = str(cfg["lm_studio"].get("persona_file") or "").strip() or "system_prompt"
        log.info("[启动] 1/3 人设：%s（%d 字）", persona_name, len(persona_text))

        memory_size = max(10, int(cfg["lm_studio"].get("context_memory_messages", 60)))
        save_dir = str(cfg["lm_studio"].get("context_save_dir") or "context").strip()
        if not os.path.isabs(save_dir):
            save_dir = os.path.join(BASE_DIR, save_dir)
        self.context_store = ContextStore(
            save_dir,
            maxlen=memory_size,
            keep_files=int(cfg["lm_studio"].get("context_keep_files", 5)),
            max_lines=int(cfg["lm_studio"].get("context_max_lines", 10000)),
        )
        self.user_context = UserContextStore(
            save_dir,
            keep_files=int(cfg["lm_studio"].get("context_keep_files", 5)),
            max_entries=int(cfg["lm_studio"].get("context_max_entries", 500)),
        )
        self.professional_context = UserContextStore(
            os.path.join(save_dir, "professional"),
            keep_files=int(cfg["lm_studio"].get("context_keep_files", 5)),
            max_entries=int(cfg["lm_studio"].get("context_max_entries", 500)),
        )
        self.group_context = GroupContextStore(
            save_dir,
            max_entries=int(cfg["lm_studio"].get("context_max_entries", 500)),
            max_chars=int(cfg["lm_studio"].get("group_context_max_chars", 5000)),
        )
        self.group_persona = GroupPersonaStore(save_dir)
        self.memory = GroupMemory(maxlen=memory_size)
        for group_id in self.groups:
            if self.group_context.messages(group_id):
                continue
            legacy = self.context_store.load(group_id)
            if legacy:
                self.group_context.replace(group_id, legacy)
        loaded_context = self.context_store.load_into(self.memory)
        log.info("[启动] 2/3 上下文：%d 个群，%d 条（%s）",
                 len(loaded_context), sum(loaded_context.values()), save_dir)

        self.images = ImageLibrary(
            os.path.join(BASE_DIR, "img"),
            max_items=int(cfg["images"].get("max_items", 1000)),
        )
        self.voice = VoiceEngine(cfg["voice"], BASE_DIR)
        log.info("[启动] 3/3 图库：%d 张", len(self.images.entries))

        self._context_save_lock = threading.Lock()
        self._context_dirty = {}
        now = time.time()
        self._context_last_activity = {group_id: now for group_id in self.groups}
        self._context_last_save = {}
        self._context_maintenance = threading.Event()
        self._context_maintenance_group = 0
        self._context_maintenance_lock = threading.Lock()
        self._context_pending_groups = set()
        self._context_busy_groups = set()
        self._context_manual_groups = set()
        self._context_disabled_groups = set()
        self._context_thread = None

        self._queue = queue.Queue(maxsize=500)
        self._llm_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aiqq-llm")
        self._model_lock = threading.Lock()
        self._model_queue = []
        self._model_queue_lock = threading.Condition()
        self._model_seq = 0
        self._model_worker = None
        self._model_active_group = 0
        self._model_active_user = 0
        self._model_active_entry = None
        self._model_max_queue = 10
        self._model_preempted = False
        self._workers = []
        self._stopping = threading.Event()
        self._busy_groups = set()
        self._busy_lock = threading.Lock()
        self._pending_followups = {}
        self._pending_lock = threading.Lock()
        self._context_lock = threading.Lock()
        self._context_stats = {}
        self._connectivity_lock = threading.Lock()
        self._connectivity = {
            "onebot_connected": False,
            "lm_connected": False,
            "onebot_error": "",
            "lm_error": "",
            "checked_at": "",
        }
        self._connectivity_thread = None
        self._image_inputs_lock = threading.Lock()
        self._image_inputs_cache = {}
        self._reply_self_lock = threading.Lock()
        self._reply_self_cache = {}
        self._model_reload_lock = threading.Lock()
        self._model_reloading = False
        self._model_reload_error = ""
        self._model_reloaded_at = ""
        self._server = None
        self._started_at = time.time()
        self._lm_connected_since = 0.0

        self.stats = {
            "events_received": 0,
            "messages_seen": 0,
            "triggers": 0,
            "replies": 0,
            "skipped": 0,
            "errors": 0,
            "last_error": "",
            "last_reply_at": "",
            "last_latency_sec": 0.0,
        }
        self._stats_lock = threading.Lock()
        self._raw_reply_lock = threading.Lock()
        self._last_raw_reply = ""
        self._last_raw_reply_group = 0
        self._last_raw_reply_at = ""
        self._raw_history = {}
        self._raw_history_limit = 50
        self._recent_reply_lock = threading.Lock()
        self._recent_replies = {}
        self._autonomous_lock = threading.Lock()
        self._autonomous_thread = None
        sleep_state_file = str(self.cfg["lm_studio"].get("sleep_state_file") or "").strip()
        if sleep_state_file:
            if not os.path.isabs(sleep_state_file):
                sleep_state_file = os.path.join(BASE_DIR, sleep_state_file)
            self._sleep_state_path = sleep_state_file
        else:
            self._sleep_state_path = os.path.join(BASE_DIR, "sleep_state.json")
        self._sleep_attempt_lock = threading.Lock()
        self._sleep_attempts = {}
        self._sleep_prep_lock = threading.Lock()
        self._sleep_prep_thread = None
        self._sleep_prep_running = False
        self._force_sleep_lock = threading.Lock()
        self._force_sleep_thread = None
        self._force_sleep_running = False
        self._wake_greeting_thread = None
        self._restart_notice_path = os.path.join(BASE_DIR, "restart_notice.json")
        self._restart_lock = threading.Lock()
        self._fatal_restarting = False
        self._restart_notice_thread = None
        self._last_trigger_at = time.time()
        self._next_spontaneous_at = 0.0
        self._last_active_group = 0
        self._spontaneous_waiting = False
        self._spontaneous_at = 0.0
        self._spontaneous_groups = set()
        self._idle_image_seen = set()
        self._idle_image_seen_lock = threading.Lock()
        self._idle_image_generation = 0
        self._idle_image_processing = False
        self._idle_image_startup_reset = True
        self._last_idle_image_at = 0.0
        self._last_idle_image_poll_at = time.time()
        self._last_voice_done_at = 0.0
        self._voice_model_unloaded = False
        self._professional_active = threading.Event()
        self._professional_message_id = ""
        self._group_last_trigger = {}
        self._sleeping_until = 0.0
        self._model_unloaded = False
        self._load_sleep_state()
        self._schedule_spontaneous()

__all__ = [name for name in globals() if not name.startswith("__")]
