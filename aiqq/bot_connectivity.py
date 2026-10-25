#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_connectivity：OneBot 与 LM Studio 连通性检查。"""
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
from .llm import LMStudioError, pick_chat_model
from .onebot import OneBotError

class BotConnectivityMixin:
    def _record_connectivity(self, onebot_ok, lm_ok, onebot_error="", lm_error=""):
        with self._connectivity_lock:
            self._connectivity = {
                "onebot_connected": bool(onebot_ok),
                "lm_connected": bool(lm_ok),
                "onebot_error": str(onebot_error),
                "lm_error": str(lm_error),
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    def _check_connectivity(self, verbose=False):
        onebot_ok = False
        onebot_error = ""
        try:
            info = self.onebot.get_login_info()
            user_id = _as_int(info.get("user_id"), 0)
            if user_id:
                self.self_id = user_id
            self.self_nick = str(info.get("nickname") or self.self_nick)
            onebot_ok = True
            if verbose:
                log.info("OneBot 已连接：QQ %s（%s）", self.self_id, self.self_nick)
        except OneBotError as exc:
            onebot_error = str(exc)
            if verbose:
                log.warning("%s", exc)

        lm_ok = False
        lm_error = ""
        try:
            models = self.lm.list_models()
            lm_ok = True
            if not self.lm.model:
                self.lm.model = pick_chat_model(models)
            if verbose:
                log.info("LM Studio 已连接，可用模型 %d 个：%s", len(models), ", ".join(models[:5]))
        except LMStudioError as exc:
            lm_error = str(exc)
            if verbose:
                log.warning("%s", exc)
        if lm_ok and not self._lm_connected_since:
            self._lm_connected_since = time.time()
        elif not lm_ok:
            self._lm_connected_since = 0.0
        if onebot_ok:
            for group_id in sorted(self.groups):
                if self.group_names.get(group_id):
                    continue
                try:
                    info = self.onebot.get_group_info(group_id)
                    name = info.get("group_name")
                    if name:
                        self.group_names[group_id] = name
                except OneBotError:
                    pass
        self._record_connectivity(onebot_ok, lm_ok, onebot_error, lm_error)
        return onebot_ok, lm_ok

    def _connectivity_loop(self):
        while not self._stopping.wait(5):
            try:
                self._check_connectivity(verbose=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("[连接检查] 异常：%s", exc)

    def probe(self):
        """启动前探测，返回 (onebot_ok, lm_ok)。"""
        onebot_ok, lm_ok = self._check_connectivity(verbose=True)
        return onebot_ok, lm_ok


__all__ = [name for name in globals() if not name.startswith("__")]
