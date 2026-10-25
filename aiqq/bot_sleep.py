#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_sleep：睡眠、主动发言与睡眠准备流程。"""
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
from .reply import strip_speaker_labels

class BotSleepMixin:
    def _in_sleep_prep_quiet(self, now_dt=None):
        start = int(self.cfg["lm_studio"].get("sleep_prep_quiet_start_hour", 7))
        end = int(self.cfg["lm_studio"].get("sleep_prep_quiet_end_hour", 22))
        if start == end:
            return False
        current = now_dt or datetime.now()
        hour = current.hour
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def _schedule_after_quiet(self, now=None):
        low = int(self.cfg["lm_studio"].get("spontaneous_idle_min_sec", 1200))
        high = int(self.cfg["lm_studio"].get("spontaneous_idle_max_sec", 3600))
        now_dt = datetime.now()
        end = int(self.cfg["lm_studio"].get("sleep_prep_quiet_end_hour", 22))
        target = now_dt.replace(hour=end, minute=0, second=0, microsecond=0)
        if target <= now_dt:
            target += timedelta(days=1)
        delay = max(0.0, (target - now_dt).total_seconds()) + random.uniform(low, max(low, high))
        base = time.time() if now is None else now
        self._next_spontaneous_at = base + delay

    def _schedule_spontaneous(self, now=None):
        if self._sleeping_until > time.time():
            self._next_spontaneous_at = 0.0
            return
        if not self.cfg["lm_studio"].get("spontaneous_enabled", True):
            self._next_spontaneous_at = 0.0
            return
        now = time.time() if now is None else now
        if self._in_sleep_prep_quiet():
            self._schedule_after_quiet(now)
            return
        low = int(self.cfg["lm_studio"].get("spontaneous_idle_min_sec", 1800))
        high = int(self.cfg["lm_studio"].get("spontaneous_idle_max_sec", 7200))
        self._next_spontaneous_at = now + random.uniform(low, max(low, high))

    def _load_sleep_state(self):
        path = self._sleep_state_path
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        try:
            until = float(data.get("until") or 0)
            model_unloaded = bool(data.get("model_unloaded"))
        except (TypeError, ValueError):
            return
        now = time.time()
        self._model_unloaded = model_unloaded
        if until > now:
            self._sleeping_until = until
            log.info("[主动] 恢复睡眠状态，剩余 %.0f 分钟", (until - now) / 60)
        else:
            self._sleeping_until = 0.0
            try:
                os.remove(path)
            except OSError:
                pass

    def _save_sleep_state(self):
        payload = {
            "until": self._sleeping_until,
            "groups": sorted(self._spontaneous_groups),
            "model_unloaded": self._model_unloaded,
        }
        tmp = self._sleep_state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._sleep_state_path)
        except OSError as exc:
            log.warning("[主动] 保存睡眠状态失败：%s", exc)

    def _clear_sleep_state(self):
        try:
            os.remove(self._sleep_state_path)
        except OSError:
            pass

    def _wake(self):
        if self._sleeping_until <= 0:
            return
        self._sleeping_until = 0.0
        self._spontaneous_waiting = False
        self._spontaneous_at = 0.0
        self._spontaneous_groups = set()
        with self._sleep_attempt_lock:
            self._sleep_attempts.clear()
        self._last_trigger_at = time.time()
        self._schedule_spontaneous()
        self._clear_sleep_state()
        log.info("[主动] 已醒来，恢复接受触发")

    def force_wake(self, group_id=0, announce=True):
        with self._autonomous_lock:
            was_sleeping = self._sleeping_until > 0 or self._spontaneous_waiting
            self._sleeping_until = 0.0
            self._spontaneous_waiting = False
            self._spontaneous_at = 0.0
            self._spontaneous_groups = set()
            with self._sleep_attempt_lock:
                self._sleep_attempts.clear()
            self._last_trigger_at = time.time()
            self._schedule_spontaneous()
        self._clear_sleep_state()
        if was_sleeping:
            log.info("[主动] 已强制结束睡眠")
            if announce:
                self._start_wake_greeting(group_id, "panel")
        return was_sleeping

    def _wake_target_group(self, group_id=0):
        group_id = _as_int(group_id, 0)
        if group_id in self.groups:
            return group_id
        with self._autonomous_lock:
            last = self._last_active_group
            triggered = dict(self._group_last_trigger)
        if last in self.groups:
            return last
        candidates = [(stamp, gid) for gid, stamp in triggered.items() if gid in self.groups]
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]
        return 0

    def _wake_prompt(self, reason):
        if reason == "noise":
            return (
                "（你本来在睡觉，但被连续触发吵醒了。现在只向这个群打个招呼，"
                "结合最近的聊天内容，用你的口吻说一句刚醒来的话，不要解释这条提示，"
                "也不要提到系统。）")
        if reason == "panel":
            return (
                "（你被叫醒了。现在只向这个群自然地说一句醒来/回应的短句，"
                "结合最近的聊天内容，不要解释这条提示，也不要提到系统。）")
        return (
            "（你刚睡醒。现在向群里自然打个招呼，结合最近的聊天内容，"
            "用你的口吻说一句醒来后的短句，不要解释这条提示，也不要提到系统。）")

    def _start_wake_greeting(self, group_id=0, reason="natural"):
        if self._wake_greeting_thread is not None and self._wake_greeting_thread.is_alive():
            return
        prompt = self._wake_prompt(reason)
        target = self._wake_target_group(group_id)
        if reason == "natural":
            self._wake_greeting_thread = threading.Thread(
                target=self._announce_wake_all, args=(prompt,),
                name="aiqq-wake-greeting", daemon=True)
        elif target:
            self._wake_greeting_thread = threading.Thread(
                target=self._announce_wake_group, args=(target, prompt),
                name="aiqq-wake-greeting", daemon=True)
        else:
            log.info("[主动] 唤醒没有可用的目标群，跳过唤醒消息")
            return
        self._wake_greeting_thread.start()

    def _sleep_trigger_reason(self, group_id, text, mentioned, reply_to):
        reason = self.trigger.match(text, mentioned)
        if not reason and reply_to and self._is_reply_to_self(reply_to, group_id):
            reason = "睡觉中尝试触发（回复）"
        return reason

    def _record_sleep_attempt(self, group_id):
        now = time.time()
        with self._sleep_attempt_lock:
            attempts = [
                value for value in self._sleep_attempts.get(group_id, [])
                if now - value <= 60
            ]
            attempts.append(now)
            self._sleep_attempts[group_id] = attempts
            return len(attempts)

    def _force_wake_for_noise(self, group_id):
        log.warning("[主动] 群 %s 一分钟内触发超过 5 次，强制唤醒", group_id)
        if self.force_wake(announce=False):
            self._start_wake_greeting(group_id, "noise")

    def _announce_wake_all(self, prompt):
        sent = []
        for group_id in sorted(self.groups):
            if self._send_spontaneous(group_id, prompt=prompt, purpose="wakeup"):
                sent.append(group_id)
        log.info("[主动] 苏醒打招呼已发送到 %d 个群：%s", len(sent), sent)

    def _announce_wake_group(self, group_id, prompt):
        sent = self._send_spontaneous(group_id, prompt=prompt, purpose="wakeup")
        log.info("[主动] 苏醒打招呼发送到群 %s：%s", group_id, bool(sent))

    def _sleep_prep_targets(self, fallback_group_id=0):
        with self._autonomous_lock:
            group_id = 0
            if self._group_last_trigger:
                group_id = max(self._group_last_trigger.items(), key=lambda item: item[1])[0]
        if group_id in self.groups:
            return [group_id]
        fallback_group_id = _as_int(fallback_group_id, 0)
        if fallback_group_id in self.groups:
            return [fallback_group_id]
        return []

    def start_sleep_prep(self, fallback_group_id=0):
        if self._sleeping_until > time.time():
            return {"status": "sleeping"}
        if self._force_sleep_running:
            return {"status": "busy"}
        targets = self._sleep_prep_targets(fallback_group_id)
        if not targets:
            return {"status": "no_group"}
        with self._sleep_prep_lock:
            if self._sleep_prep_running:
                return {"status": "running"}
            self._sleep_prep_running = True
        self._sleep_prep_thread = threading.Thread(
            target=self._sleep_prep_job, args=(targets,),
            name="aiqq-sleep-prep", daemon=True)
        self._sleep_prep_thread.start()
        return {"status": "started", "groups": targets}

    def _sleep_prep_job(self, targets=None):
        try:
            if self._sleeping_until > time.time():
                log.info("[主动] 正在睡觉，忽略睡眠预备请求")
                return
            targets = list(targets or self._sleep_prep_targets())
            if not targets:
                log.warning("[主动] 没有可触发睡眠预备发言的群")
                return
            self._spontaneous_waiting = True
            self._spontaneous_at = time.time()
            self._spontaneous_groups = set(targets)
            prompt = (
                "（你准备去睡了。结合最近的聊天内容，用你的口吻说一句准备睡了、"
                "困了要睡的话，不要解释这条提示，也不要提到系统。）")
            sent = []
            for group_id in targets:
                if not self._spontaneous_waiting:
                    break
                if self._send_spontaneous(
                        group_id, prompt=prompt, purpose="sleep_prep"):
                    sent.append(group_id)
            if self._spontaneous_waiting and sent:
                self._spontaneous_groups = set(sent)
                self._spontaneous_at = time.time()
                log.info("[主动] 手动睡眠预备已发送到 %d 个群，开始等待 %d 秒后入睡：%s",
                         len(sent),
                         int(self.cfg["lm_studio"].get("spontaneous_wait_sec", 300)),
                         sent)
            else:
                self._spontaneous_waiting = False
                self._spontaneous_groups = set()
                self._schedule_spontaneous()
        except Exception as exc:  # noqa: BLE001
            log.warning("[主动] 睡眠预备发言失败：%s", exc)
        finally:
            self._sleep_prep_running = False

    def start_force_sleep(self, fallback_group_id=0):
        if self._sleeping_until > time.time():
            return {"status": "sleeping"}
        targets = self._sleep_prep_targets(fallback_group_id)
        if not targets:
            return {"status": "no_group"}
        with self._force_sleep_lock:
            if self._force_sleep_running:
                return {"status": "running"}
            if self._sleep_prep_running:
                return {"status": "busy"}
            self._force_sleep_running = True
        self._force_sleep_thread = threading.Thread(
            target=self._force_sleep_job, args=(targets,),
            name="aiqq-force-sleep", daemon=True)
        self._force_sleep_thread.start()
        return {"status": "started", "groups": targets}

    def _force_sleep_job(self, targets):
        try:
            if self._sleeping_until > time.time():
                return
            self._spontaneous_waiting = False
            self._spontaneous_at = 0.0
            self._spontaneous_groups = set(targets)
            log.info("[主动] 强制睡眠：群 %s，忽略常规睡眠触发条件", targets)
            self._go_sleep(targets, force=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("[主动] 强制睡眠失败：%s", exc)
        finally:
            self._force_sleep_running = False

    def _note_spontaneous_activity(self, group_id):
        self._last_active_group = group_id
        if (self._spontaneous_waiting
                and group_id in self._spontaneous_groups):
            self._spontaneous_waiting = False
            self._spontaneous_at = 0.0
            self._spontaneous_groups = set()
            self._schedule_spontaneous()
            log.info("[主动] 群 %s 有人回应，取消睡觉", group_id)

    def _send_spontaneous(self, group_id, prompt=None, purpose="spontaneous"):
        entry = {
            "message_id": "%s-%s-%s" % (purpose, group_id, time.time_ns()),
            "user_id": 0,
            "nickname": "",
            "text": prompt or (
                "（群里已经很久没人说话了。结合最近的聊天内容，主动自然地说一句话，"
                "不要解释这条提示，也不要提到系统。）"),
            "mentioned": False,
            "images": [],
            "forwards": [],
            "reply_to": "",
            "time": int(time.time()),
            "is_self": False,
        }
        with self._busy_lock:
            if group_id in self._busy_groups:
                return False
            self._busy_groups.add(group_id)
        try:
            with self._model_lock:
                reply = self._generate_reply(entry, group_id)
            if reply and not entry.get("professional"):
                reply = strip_speaker_labels(reply, (self.self_nick,))
            if not reply:
                return False
            self._send_reply(entry, group_id, reply)
            self._increment(purpose)
            log.info("[主动] 群 %s %s完成", group_id, purpose)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("[主动] 群 %s %s失败：%s", group_id, purpose, exc)
            return False
        finally:
            with self._busy_lock:
                self._busy_groups.discard(group_id)

    def _pick_sleep_sec(self):
        low = int(self.cfg["lm_studio"].get("spontaneous_sleep_min_sec", 7200))
        high = int(self.cfg["lm_studio"].get("spontaneous_sleep_max_sec", 21600))
        return random.uniform(low, max(low, high))

    def _send_farewell(self, group_id, sleep_sec, force=False):
        low = int(self.cfg["lm_studio"].get("spontaneous_sleep_min_sec", 7200))
        high = int(self.cfg["lm_studio"].get("spontaneous_sleep_max_sec", 21600))
        midpoint = (low + high) / 2.0
        hours = max(0.1, float(sleep_sec) / 3600.0)
        if sleep_sec < midpoint:
            style = "你只打算小睡一会儿"
        else:
            style = "你太困了，打算大睡一觉"
        if force:
            opening = "你现在被强制去睡了"
        else:
            opening = "群里没有人回应你的话"
        prompt = (
            "（%s。%s，大约 %.1f 小时。结合最近的聊天内容，"
            "用你的口吻说一句去睡/晚安的话，不要解释这条提示，也不要提到系统。）"
            % (opening, style, hours))
        return self._send_spontaneous(group_id, prompt=prompt, purpose="farewell")

    def _go_sleep(self, targets=None, force=False):
        targets = list(targets if targets is not None else self._spontaneous_groups)
        sleep_sec = self._pick_sleep_sec()
        self._spontaneous_waiting = False
        self._spontaneous_at = 0.0
        with self._sleep_attempt_lock:
            self._sleep_attempts.clear()
        for group_id in targets:
            if not self._send_farewell(group_id, sleep_sec, force=force):
                log.warning("[主动] 群 %s 告别语生成失败，跳过", group_id)
        try:
            self.save_context_now()
        except Exception as exc:  # noqa: BLE001
            log.warning("[主动] 睡觉前保存上下文失败：%s", exc)
        try:
            info = self.lm.unload_model()
            self._model_unloaded = info.get("status") in ("unloaded", "not_loaded")
            log.info("[主动] 模型卸载结果：%s", info.get("status"))
        except Exception as exc:  # noqa: BLE001
            log.warning("[主动] 卸载模型失败：%s", exc)
        self._spontaneous_groups = set(targets)
        self._sleeping_until = time.time() + sleep_sec
        self._next_spontaneous_at = 0.0
        self._save_sleep_state()
        log.info("[主动] 进入睡眠 %.1f 小时，期间不接受触发", sleep_sec / 3600.0)

    def _send_plain_text(self, group_id, text):
        segments = [{"type": "text", "data": {"text": text}}]
        result = self.onebot.send_group_msg(group_id, segments)
        self._increment("replies")
        self._remember_self_reply(
            group_id, text, self._message_id_from_result(result))
        return result

    def _fatal_restart(self, group_id):
        with self._restart_lock:
            if self._fatal_restarting:
                return
            self._fatal_restarting = True
        log.error("[致命] 群 %s 模型输出连续重复，准备重启模型和机器人", group_id)
        try:
            self._send_plain_text(group_id, "致命错误，正在重启")
        except Exception as exc:  # noqa: BLE001
            log.warning("[致命] 发送重启提示失败：%s", exc)
        try:
            self.save_context_now()
        except Exception as exc:  # noqa: BLE001
            log.warning("[致命] 重启前保存上下文失败：%s", exc)
        notice = {
            "group_id": int(group_id),
            "message": "重启完成",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            with open(self._restart_notice_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(notice, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            log.warning("[致命] 写入重启完成提示失败：%s", exc)
        try:
            self.lm.reload_model()
        except Exception as exc:  # noqa: BLE001
            log.warning("[致命] 重启模型失败：%s", exc)
        self.request_restart()

    def _restart_notice_loop(self):
        for _ in range(18):
            if not os.path.isfile(self._restart_notice_path):
                return
            try:
                with open(self._restart_notice_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                group_id = int(data.get("group_id") or 0)
                message = str(data.get("message") or "重启完成")
            except (OSError, ValueError, TypeError):
                try:
                    os.remove(self._restart_notice_path)
                except OSError:
                    pass
                return
            if not group_id:
                return
            try:
                self._send_plain_text(group_id, message)
                os.remove(self._restart_notice_path)
                log.info("[致命] 已在群 %s 发送重启完成提示", group_id)
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("[致命] 发送重启完成提示失败，稍后重试：%s", exc)
                time.sleep(10)

    def _autonomous_tick(self):
        if self._stopping.is_set():
            return
        if not self.cfg["lm_studio"].get("spontaneous_enabled", True):
            return
        if self._professional_active.is_set():
            return
        if self._sleep_prep_running or self._force_sleep_running:
            return
        now = time.time()
        if self._sleeping_until > 0:
            if now < self._sleeping_until:
                self._next_spontaneous_at = 0.0
                return
            self._wake()
            self._start_wake_greeting(0, "natural")
        if self._spontaneous_waiting:
            wait = int(self.cfg["lm_studio"].get("spontaneous_wait_sec", 300))
            if now - self._spontaneous_at >= wait:
                self._go_sleep()
            return
        if self._in_sleep_prep_quiet():
            self._schedule_after_quiet(now)
            return
        if self._next_spontaneous_at <= 0 or now < self._next_spontaneous_at:
            return
        window = int(self.cfg["lm_studio"].get("spontaneous_active_window_sec", 3600))
        with self._autonomous_lock:
            targets = sorted(
                group_id for group_id, last in self._group_last_trigger.items()
                if now - last <= window and group_id in self.groups
            )
        if not targets:
            self._schedule_spontaneous(now)
            return
        self._spontaneous_waiting = True
        self._spontaneous_at = now
        self._spontaneous_groups = set(targets)
        sent = []
        for group_id in targets:
            if not self._spontaneous_waiting:
                break
            if self._send_spontaneous(group_id):
                sent.append(group_id)
        if self._spontaneous_waiting and sent:
            self._spontaneous_groups = set(sent)
            self._spontaneous_at = time.time()
            log.info("[主动] 已向 %d 个活跃群发言，等待回应 %d 秒",
                     len(sent), int(self.cfg["lm_studio"].get("spontaneous_wait_sec", 300)))
        else:
            self._spontaneous_waiting = False
            self._spontaneous_groups = set()
            self._schedule_spontaneous(now)

__all__ = [name for name in globals() if not name.startswith("__")]
