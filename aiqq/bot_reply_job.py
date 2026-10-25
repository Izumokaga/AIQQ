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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import TimedRotatingFileHandler






from .config import log
from .voice import strip_voice_marker
from .llm import FatalRestartError, LMStudioError
from .onebot import OneBotError
from .reply import strip_speaker_labels

class BotReplyJobMixin:
    def _reply_job(self, entry, group_id):
        started = time.time()
        with self._busy_lock:
            self._busy_groups.add(group_id)
        try:
            with self._model_lock:
                reply = self._generate_reply(entry, group_id)
            if reply:
                reply = strip_speaker_labels(reply, (self.self_nick,))
            if not reply:
                self._increment("skipped")
                log.info("[跳过] 模型没有给出可发送的回复。")
                return
            thinking_sec = max(0.0, time.time() - started)
            user_text = entry.get("text") or (
                "[图片]" if entry.get("images") else "[触发]")
            self._send_reply(entry, group_id, reply, thinking_sec=thinking_sec)
            if self.cfg["lm_studio"].get("context_save_enabled", True):
                if entry.get("professional"):
                    self.professional_context.append_exchange(
                        entry.get("user_id") or 0, group_id, user_text, reply,
                        user_name=entry.get("nickname") or "",
                        mentions=entry.get("mentions") or [])
                else:
                    context_reply = strip_voice_marker(reply)[1] or reply
                    self.user_context.append_exchange(
                        entry.get("user_id") or 0, group_id, user_text, context_reply,
                        user_name=entry.get("nickname") or "",
                        mentions=entry.get("mentions") or [])
        except FatalRestartError:
            log.warning("[致命] 已触发重启，停止本条回复")
        except LMStudioError as exc:
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
