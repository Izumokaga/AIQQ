#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_lifecycle：启动、Webhook、模型重载、重启与关闭。"""
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





from .config import ConfigError, log
from .webhook import BotHTTPServer, WebhookHandler

class BotLifecycleMixin:
    def _autonomous_loop(self):
        while not self._stopping.wait(10):
            try:
                self._autonomous_tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("[主动] 检查异常：%s", exc)

    def start(self):
        self._start_webhook()
        for index in range(2):
            thread = threading.Thread(target=self._worker_loop, name="aiqq-webhook-%d" % index, daemon=True)
            thread.start()
            self._workers.append(thread)
        self._model_worker = threading.Thread(
            target=self._model_queue_loop, name="aiqq-model-queue", daemon=True)
        self._model_worker.start()
        log.info("Webhook 已启动：http://%s:%d%s",
                 self.cfg["onebot"]["webhook_host"],
                 self.cfg["onebot"]["webhook_port"],
                 self.cfg["onebot"]["webhook_path"])
        if self.cfg["lm_studio"].get("context_save_enabled", True):
            log.info("[上下文] 每次触发全部发送完成后保存对应用户上下文")
        if self.cfg["lm_studio"].get("spontaneous_enabled", True):
            self._autonomous_thread = threading.Thread(
                target=self._autonomous_loop, name="aiqq-autonomous", daemon=True)
            self._autonomous_thread.start()
            log.info("[主动] 已启用长时间无人触发主动发言")
        if self.cfg["lm_studio"].get("idle_image_reply_enabled", True):
            self._idle_image_thread = threading.Thread(
                target=self._idle_image_loop, name="aiqq-idle-image", daemon=True)
            self._idle_image_thread.start()
            log.info("[识图] 已启用 7-22 空闲逐张识图回复")
        if self.voice.enabled and self.voice.qwen_enabled:
            self._voice_idle_thread = threading.Thread(
                target=self._voice_idle_loop, name="aiqq-voice-idle", daemon=True)
            self._voice_idle_thread.start()
            log.info("[语音] 已启用生成完成后空闲卸载 TTS 模型")
        if os.path.isfile(self._restart_notice_path):
            self._restart_notice_thread = threading.Thread(
                target=self._restart_notice_loop, name="aiqq-restart-notice", daemon=True)
            self._restart_notice_thread.start()
        self._connectivity_thread = threading.Thread(
            target=self._connectivity_loop, name="aiqq-connectivity", daemon=True)
        self._connectivity_thread.start()

    def _start_webhook(self):
        address = (self.cfg["onebot"]["webhook_host"], self.cfg["onebot"]["webhook_port"])
        try:
            self._server = BotHTTPServer(address, WebhookHandler, self)
        except OSError as exc:
            raise ConfigError(
                "Webhook 端口 %d 无法监听（%s）。请关闭占用该端口的程序，或修改 onebot.webhook_port。"
                % (address[1], exc)
            )
        thread = threading.Thread(target=self._server.serve_forever, name="aiqq-http", daemon=True)
        thread.start()

    def request_model_reload(self):
        with self._model_reload_lock:
            if self._model_reloading:
                return False
            self._model_reloading = True
            self._model_reload_error = ""
        threading.Thread(target=self._model_reload_job, name="aiqq-model-reload", daemon=True).start()
        return True

    def _model_reload_job(self):
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                with self._busy_lock:
                    busy = bool(self._busy_groups)
                if not busy:
                    break
                time.sleep(0.5)
            info = self.lm.reload_model()
            self.context_store.load_into(self.memory)
            count = self.images.reload()
            with self._persona_lock:
                self._persona_cache = (0.0, "")
            with self._model_reload_lock:
                self._model_reloaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._model_reload_error = ""
            log.info("[模型] 已重新加载 %s（图库 %d 张）", info.get("model"), count)
        except Exception as exc:  # noqa: BLE001
            with self._model_reload_lock:
                self._model_reload_error = str(exc)
            log.error("[模型] 重载失败：%s", exc)
        finally:
            with self._model_reload_lock:
                self._model_reloading = False

    def request_restart(self):
        log.info("[重启] 收到控制面板重启请求，准备重新加载配置…")
        threading.Thread(target=self._restart_process, name="aiqq-restart", daemon=True).start()

    def request_shutdown(self):
        log.info("[关闭] 收到控制面板关闭机器请求…")
        threading.Thread(target=self._shutdown_process, name="aiqq-shutdown", daemon=True).start()

    def _shutdown_process(self):
        time.sleep(0.5)
        try:
            self.shutdown()
        finally:
            os._exit(0)

    def _restart_process(self):
        time.sleep(0.6)
        self._stopping.set()
        try:
            self._save_all_contexts()
        except Exception as exc:  # noqa: BLE001
            log.warning("[重启] 保存上下文失败：%s", exc)
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                self._server = None
        except Exception as exc:  # noqa: BLE001
            log.warning("[重启] 关闭 Webhook 失败：%s", exc)
        try:
            self._llm_executor.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        entry = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")
        args = [sys.executable, entry] + list(sys.argv[1:])
        log.info("[重启] 重新启动：%s", " ".join(args))
        try:
            os.execv(sys.executable, args)
        except Exception as exc:  # noqa: BLE001
            log.error("[重启] 启动新进程失败：%s", exc)
            os._exit(1)

    def shutdown(self):
        self._stopping.set()
        with self._model_queue_lock:
            self._model_queue_lock.notify_all()
        if self._model_worker is not None:
            self._model_worker.join(timeout=3)
        self._save_all_contexts()
        if self._context_thread is not None:
            self._context_thread.join(timeout=2)
        if self._autonomous_thread is not None:
            self._autonomous_thread.join(timeout=2)
        if self._sleep_prep_thread is not None:
            self._sleep_prep_thread.join(timeout=2)
        if self._force_sleep_thread is not None:
            self._force_sleep_thread.join(timeout=2)
        if self._wake_greeting_thread is not None:
            self._wake_greeting_thread.join(timeout=2)
        if self._restart_notice_thread is not None:
            self._restart_notice_thread.join(timeout=2)
        if self._connectivity_thread is not None:
            self._connectivity_thread.join(timeout=2)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._llm_executor.shutdown(wait=False)
        log.info("AIQQ 已停止。")

__all__ = [name for name in globals() if not name.startswith("__")]
