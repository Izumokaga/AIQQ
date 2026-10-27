#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ llm：LM Studio 客户端、模型选择与推理参数。"""
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



from .config import MAX_TOKENS_CAP, log

class LMStudioError(Exception):
    pass


class FatalRestartError(Exception):
    pass


def pick_chat_model(models):
    """自动挑选对话模型：跳过 embedding / rerank 模型。"""
    for model_id in models:
        lowered = str(model_id).lower()
        if not any(token in lowered for token in ("embed", "rerank", "tts")):
            return model_id
    return models[0]


class LMStudioClient(object):
    def __init__(self, cfg):
        self.base_url = cfg["base_url"].rstrip("/")
        self.native_root = self.base_url[:-3].rstrip("/") if self.base_url.endswith("/v1") else self.base_url
        self.api_key = str(cfg.get("api_key") or "").strip()
        self.model = str(cfg.get("model") or "").strip()
        self.temperature = float(cfg.get("temperature", 0.8))
        self.top_p = float(cfg.get("top_p", 0.95))
        self.max_tokens = int(cfg.get("max_tokens", 512))
        self.timeout = int(cfg.get("timeout_sec", 120))
        self.reasoning_effort = str(cfg.get("reasoning_effort") or "").strip().lower()
        self.image_input = str(cfg.get("image_input") or "auto").strip().lower()
        self.image_only_png_jpeg = bool(cfg.get("image_only_png_jpeg", True))
        self.image_max_bytes = int(cfg.get("image_max_bytes", 8 * 1024 * 1024))
        self.image_max_count = int(cfg.get("image_max_count", 3))
        self.image_max_input_images = int(cfg.get("image_max_input_images", 10))
        self.image_context_messages = int(cfg.get("image_context_messages", 3))
        self.image_context_max_count = int(cfg.get("image_context_max_count", 8))
        self.image_user_messages = int(cfg.get("image_user_messages", 5))
        self.image_selected_max = int(cfg.get("image_selected_max", 5))
        self._model_lock = threading.Lock()
        self._vision_lock = threading.Lock()
        self._vision_cache = (0.0, "", None)
        self._auto_model = not bool(self.model)

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _native_request(self, method, path, payload=None, timeout=None):
        url = self.native_root + path
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:800]
            except Exception:  # noqa: BLE001
                pass
            raise LMStudioError("LM Studio 原生接口 HTTP %s：%s" % (exc.code, body or exc.reason))
        except urllib.error.URLError as exc:
            raise LMStudioError("无法连接 LM Studio 原生接口：%s" % exc.reason)
        except (TimeoutError, OSError) as exc:
            raise LMStudioError("LM Studio 原生接口超时或失败：%s" % exc)

    def reload_model(self):
        """通过 LM Studio v1 REST API 卸载并重新加载当前模型。"""
        data = self._native_request("GET", "/api/v1/models", timeout=min(self.timeout, 30))
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise LMStudioError("LM Studio 没有返回模型列表，可能需要 0.4.0 或更高版本")

        loaded = []
        for model in models:
            if not isinstance(model, dict):
                continue
            for instance in model.get("loaded_instances") or []:
                if isinstance(instance, dict) and instance.get("id"):
                    loaded.append((model, instance))

        target = self.model
        chosen = None
        if target:
            for model, instance in loaded:
                if target in (str(instance.get("id")), str(model.get("key"))):
                    chosen = (model, instance)
                    break
        if chosen is None and not target and len(loaded) == 1:
            chosen = loaded[0]
        if chosen is None and target:
            for model in models:
                if isinstance(model, dict) and str(model.get("key")) == target:
                    chosen = (model, None)
                    break
        if chosen is None:
            raise LMStudioError("找不到当前模型：%s" % (target or "未指定"))

        model, instance = chosen
        model_key = str(model.get("key") or instance.get("id"))
        config = (instance or {}).get("config") if isinstance(instance, dict) else {}
        config = config if isinstance(config, dict) else {}
        instance_id = str(instance.get("id")) if isinstance(instance, dict) and instance.get("id") else ""

        if instance_id:
            self._native_request(
                "POST", "/api/v1/models/unload", {"instance_id": instance_id},
                timeout=min(self.timeout, 60))
        payload = {"model": model_key}
        for key in ("context_length", "eval_batch_size", "flash_attention",
                    "num_experts", "offload_kv_cache_to_gpu"):
            if config.get(key) is not None:
                payload[key] = config[key]
        result = self._native_request(
            "POST", "/api/v1/models/load", payload, timeout=max(self.timeout, 300))
        loaded_id = result.get("instance_id") if isinstance(result, dict) else None
        self.model = str(loaded_id or model_key)
        with self._vision_lock:
            self._vision_cache = (0.0, "", None)
        return {
            "model": model_key,
            "instance_id": loaded_id or instance_id,
            "load_time_seconds": result.get("load_time_seconds") if isinstance(result, dict) else None,
        }

    def unload_model(self):
        """卸载当前模型；已经卸载时返回 not_loaded。"""
        data = self._native_request("GET", "/api/v1/models", timeout=min(self.timeout, 30))
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return {"status": "not_loaded"}
        target = self.model
        chosen = None
        loaded = []
        for model in models:
            if not isinstance(model, dict):
                continue
            for instance in model.get("loaded_instances") or []:
                if not isinstance(instance, dict) or not instance.get("id"):
                    continue
                loaded.append((model, instance))
                if target and target in (str(instance.get("id")), str(model.get("key"))):
                    chosen = instance
                    break
            if chosen is not None:
                break
        if chosen is None and not target and loaded:
            chosen = loaded[0][1]
        if chosen is None:
            return {"status": "not_loaded"}
        instance_id = str(chosen.get("id"))
        self._native_request(
            "POST", "/api/v1/models/unload", {"instance_id": instance_id},
            timeout=min(self.timeout, 60))
        with self._vision_lock:
            self._vision_cache = (0.0, "", None)
        return {"status": "unloaded", "instance_id": instance_id}

    def _request(self, method, path, payload=None):
        url = self.base_url + path
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:800]
            except Exception:
                pass
            raise LMStudioError("LM Studio 返回 HTTP %s：%s" % (exc.code, body or exc.reason))
        except urllib.error.URLError as exc:
            raise LMStudioError(
                "无法连接 LM Studio（%s）。请确认 LM Studio 已打开并启动了 Local Server（Developer → Start Server）。"
                % exc.reason
            )
        except (TimeoutError, OSError) as exc:
            raise LMStudioError("请求 LM Studio 超时或失败：%s" % exc)

    def list_models(self):
        data = self._request("GET", "/models")
        models = []
        for item in data.get("data") or []:
            model_id = item.get("id")
            if model_id:
                models.append(str(model_id))
        if not models:
            raise LMStudioError("LM Studio 没有返回可用模型，请先在 LM Studio 中加载一个模型。")
        return models

    def _native_base(self):
        base = self.base_url
        if base.endswith("/v1"):
            base = base[:-3]
        return base.rstrip("/")

    def supports_vision(self):
        """auto 模式下查询 LM Studio 模型能力；vlm 或 capabilities 含 vision 即支持。"""
        mode = self.image_input
        if mode == "off":
            return False
        if mode == "on":
            return True
        try:
            model = self.resolve_model()
        except LMStudioError:
            return False

        now = time.time()
        with self._vision_lock:
            cached_at, cached_model, cached_value = self._vision_cache
            if cached_value is not None and cached_model == model and now - cached_at < 60:
                return bool(cached_value)

        supported = False
        try:
            request = urllib.request.Request(
                self._native_base() + "/api/v0/models",
                headers=self._headers(),
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            for item in data.get("data") or []:
                if str(item.get("id")) != model:
                    continue
                model_type = str(item.get("type") or "").lower()
                capabilities = [str(cap).lower() for cap in (item.get("capabilities") or [])]
                supported = model_type in ("vlm", "vision") or "vision" in capabilities
                break
        except Exception as exc:  # noqa: BLE001 - 能力探测失败不应影响文字回复
            log.debug("查询 LM Studio 视觉能力失败：%s", exc)

        with self._vision_lock:
            self._vision_cache = (now, model, supported)
        log.debug("模型 %s 视觉能力：%s", model, supported)
        return supported

    def resolve_model(self):
        with self._model_lock:
            if self.model:
                return self.model
            models = self.list_models()
            self.model = pick_chat_model(models)
            log.info("LM Studio 自动选择模型：%s", self.model)
            return self.model

    def chat(self, messages, reasoning_effort=None, max_tokens=None):
        last_error = None
        active_effort = self.reasoning_effort if reasoning_effort is None else reasoning_effort
        for attempt in range(2):
            model = self.resolve_model()
            payload = {
                "model": model,
                "messages": messages,
                "temperature": self.temperature,
                "top_p": self.top_p,
                "max_tokens": max_tokens or self.max_tokens,
                "stream": False,
            }
            if active_effort and active_effort != "auto":
                payload["reasoning_effort"] = active_effort
            try:
                data = self._request("POST", "/chat/completions", payload)
                if isinstance(data, dict) and data.get("error"):
                    raise LMStudioError("LM Studio 返回错误：%s" % str(data.get("error"))[:500])
                choices = data.get("choices") or []
                if not choices:
                    raise LMStudioError("LM Studio 响应中没有 choices：%s" % json.dumps(data, ensure_ascii=False)[:300])
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
                text = str(content or "").strip()
                finish_reason = choices[0].get("finish_reason")
                reasoning = message.get("reasoning_content") or ""
                if not text and finish_reason == "length":
                    if attempt == 0 and self.max_tokens < MAX_TOKENS_CAP:
                        new_limit = min(MAX_TOKENS_CAP, max(self.max_tokens * 2, 2048))
                        log.warning(
                            "模型把 max_tokens（%d）全部用在思考过程上，没有产生正文；"
                            "提高到 %d 重试…",
                            self.max_tokens,
                            new_limit,
                        )
                        self.max_tokens = new_limit
                        continue
                    log.warning(
                        "模型在 max_tokens=%d 下仍没有产生正文；"
                        "请调大 lm_studio.max_tokens（例如 4096~8192）或更换模型。",
                        self.max_tokens,
                    )
                elif finish_reason == "length" and text and not text.endswith(
                        ("…", "。", "！", "？", ".", "!", "?")):
                    text += "…"
                elif not text:
                    log.warning("模型没有返回正文（finish_reason=%s）。", finish_reason)
                if reasoning:
                    log.debug("模型思考内容 %d 字符", len(reasoning))
                usage = data.get("usage") or {}
                if usage:
                    log.debug("LM 用量：%s", usage)
                return text
            except LMStudioError as exc:
                last_error = exc
                lowered = str(exc).lower()
                if attempt == 0 and self._auto_model and ("model" in lowered or "404" in lowered):
                    log.warning("模型 %s 不可用，重新选择已加载的模型…", self.model)
                    with self._model_lock:
                        self.model = ""
                    continue
                if attempt == 0 and active_effort and "reasoning" in lowered and "HTTP 400" in str(exc):
                    if reasoning_effort is not None:
                        log.warning("当前模型不支持 reasoning_effort=%s，本次回退为普通模式。",
                                    active_effort)
                        active_effort = self.reasoning_effort or "none"
                    else:
                        log.warning("当前模型不支持 reasoning_effort=%s，回退为 none。",
                                    active_effort)
                        active_effort = "none"
                    continue
                if attempt == 0 and "HTTP 5" in str(exc):
                    time.sleep(1.0)
                    continue
                break
        raise last_error

__all__ = [name for name in globals() if not name.startswith("__")]
