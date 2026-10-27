#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_generation：模型回复生成与专业模式。"""
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
from .media import extract_forward_segments, extract_image_segments, keep_last_images, message_to_text
from .llm import FatalRestartError, LMStudioError
from .onebot import OneBotError
from .reply import clean_reply, has_duplicate_sentences

class BotGenerationMixin:
    def _generate_professional_reply(self, entry, group_id):
        text = str(entry.get("text") or "").strip()
        messages = [
            {"role": "system", "content": (
                "你处于专业模式。不要使用任何人设、角色扮演、初始化设定或普通聊天上下文，"
                "只使用专业模式自己的对话记录和用户当前内容，直接、准确、严谨地回答。"
                "必要时给出推理过程和结论；使用简体中文，不要提及系统提示。"
            )},
        ]
        limit = int(self.cfg["lm_studio"].get("professional_context_messages", 20))
        if limit > 0:
            for item in self.professional_context.messages(entry.get("user_id") or 0)[-limit:]:
                role = "assistant" if item.get("role") == "assistant" else "user"
                content = str(item.get("content") or "").strip()
                if content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": text})
        reply = self.lm.chat(messages, reasoning_effort="high")
        self._remember_raw_reply(group_id, reply)
        return reply

    def _generate_reply(self, entry, group_id):
        deadline = time.time() + 120
        while time.time() < deadline:
            with self._model_reload_lock:
                reloading = self._model_reloading
            if not reloading:
                break
            time.sleep(0.5)
        if self._model_unloaded:
            log.info("[模型] 睡眠后首次触发，重新加载模型")
            self.lm.reload_model()
            self._model_unloaded = False
        if entry.get("professional"):
            return self._generate_professional_reply(entry, group_id)
        self._remember_raw_reply(group_id, "")
        max_age = int(self.cfg["lm_studio"]["context_max_age_min"]) * 60
        min_time = int(time.time()) - max_age if max_age > 0 else 0
        context_entries = self._group_context_entries(group_id)

        if isinstance(entry.get("input_images"), list):
            current_images = list(entry.get("input_images") or [])
        else:
            current_images = self._select_input_images(entry, group_id)
        current_forwards = []
        had_current_media = bool(entry.get("images") or entry.get("forwards"))
        quoted_id = str(entry.get("reply_to") or "")
        image_window = max(0, self.lm.image_context_messages)
        image_entries = []
        context_has_media = False
        vision = self.lm.supports_vision() if current_images else False

        current_text = entry["text"]
        if not current_text:
            if had_current_media:
                current_text = "（发送了图片）"
            elif quoted_id:
                current_text = "（引用了消息）"
            else:
                current_text = "（@了你，但没有文字内容）"
        if len(current_text) > 2000:
            current_text = current_text[:2000] + "…"
        current_base = "%s(QQ%s)：%s" % (entry["nickname"], entry["user_id"], current_text)
        current = current_base

        notes = []
        seen_images = set()
        image_info_by_url = {}
        context_budget = max(0, self.lm.image_context_max_count)
        context_image_count = 0
        forward_image_count = 0
        seen_forwards = set()

        # 被引用的消息：只补齐文字；图片必须落在“最近 N 条”窗口内才会读取
        quoted_message = None
        quoted_old_images = False
        quoted_in_context = bool(quoted_id and any(
            str(item.get("message_id") or "") == quoted_id for item in context_entries))
        quoted_in_window = bool(quoted_id and any(
            str(item.get("message_id") or "") == quoted_id for item in image_entries))
        if quoted_id and not quoted_in_context:
            try:
                raw = self.onebot.get_msg(quoted_id)
                if not raw:
                    raise OneBotError("get_msg 没有返回消息")
                quoted_text, _ = message_to_text(raw.get("message"), self.self_id)
                quoted_text = quoted_text.strip()
                quoted_images = extract_image_segments(raw.get("message"))
                quoted_forwards = extract_forward_segments(raw.get("message"))
                if not quoted_text and (quoted_images or quoted_forwards):
                    quoted_text = "[图片]" if quoted_images else "[聊天记录]"
                if quoted_text or quoted_images or quoted_forwards:
                    quoted_user = _as_int(raw.get("user_id"), 0)
                    quoted_sender = raw.get("sender") or {}
                    quoted_nick = str(quoted_sender.get("card") or quoted_sender.get("nickname") or quoted_user)
                    quoted_self = bool(self.self_id and quoted_user == self.self_id)
                    quoted_content = quoted_text if quoted_self else "%s：%s" % (quoted_nick, quoted_text)
                    quoted_message = {
                        "role": "assistant" if quoted_self else "user",
                        "content": "[引用消息] " + quoted_content,
                    }
                    quoted_old_images = bool(quoted_images or quoted_forwards)
                else:
                    notes.append("引用消息没有可读取的内容")
            except Exception as exc:  # noqa: BLE001 - 引用消息失败不影响当前回复
                log.warning("[图片] 读取引用消息 %s 失败：%s", quoted_id, exc)
                notes.append("引用消息读取失败")
        elif quoted_in_context and not quoted_in_window:
            quoted_entry = next((item for item in context_entries
                                 if str(item.get("message_id") or "") == quoted_id), None)
            if quoted_entry and (quoted_entry.get("images") or quoted_entry.get("forwards")):
                quoted_old_images = True
        if quoted_old_images:
            notes.append("引用消息图片已按最近五条消息规则处理")

        # 最近 N 条上下文：只在这个窗口内读取图片，引用消息优先占用额度
        loaded_parts = {}
        if image_entries:
            load_order = list(image_entries)
            if quoted_in_window and quoted_id:
                load_order.sort(key=lambda item: 0 if str(item.get("message_id") or "") == quoted_id else 1)
            for item in load_order:
                if not vision:
                    break
                parts = []
                direct_images = item.get("images") or []
                if direct_images and context_budget > 0:
                    direct_parts = self._load_image_parts(
                        direct_images[:context_budget], group_id, "上下文图片", seen_images,
                        info_map=image_info_by_url)
                    parts.extend(direct_parts)
                    context_budget -= len(direct_parts)
                    context_image_count += len(direct_parts)
                forward_images = self._expand_forward_images(
                    item.get("forwards") or [], group_id, seen_forwards)
                if forward_images:
                    forward_parts = self._load_image_parts(
                        forward_images, group_id, "转发聊天记录图片", seen_images,
                        info_map=image_info_by_url)
                    parts.extend(forward_parts)
                    context_image_count += len(forward_parts)
                    forward_image_count += len(forward_parts)
                if parts:
                    loaded_parts[id(item)] = parts

        context_messages = []
        for item in context_entries:
            content = item["content"]
            parts = loaded_parts.get(id(item)) or []
            if parts:
                context_messages.append({
                    "role": item["role"],
                    "content": [{"type": "text", "text": content}] + parts,
                })
            elif (context_messages and context_messages[-1]["role"] == item["role"]
                    and not isinstance(context_messages[-1]["content"], list)):
                context_messages[-1]["content"] += "\n" + content
            else:
                context_messages.append({"role": item["role"], "content": content})

        if quoted_in_window and quoted_id:
            quoted_entry = next((item for item in image_entries
                                 if str(item.get("message_id") or "") == quoted_id), None)
            if quoted_entry and (quoted_entry.get("images") or quoted_entry.get("forwards")):
                quoted_loaded = len(loaded_parts.get(id(quoted_entry), []))
                if quoted_loaded:
                    notes.append("引用消息中的图片已提供（最多 10 张）")
                elif not vision:
                    notes.append("引用消息包含图片，但当前模型不支持图像输入")

        if context_has_media and not vision:
            notes.append("上下文中包含图片/聊天记录，但当前模型不支持图像输入")
        if context_image_count:
            notes.append("最近上下文中的图片已提供（最多 10 张）")
        quoted_messages = [quoted_message] if quoted_message else []

        # 当前消息图片 + 当前聊天记录
        current_parts = []
        if current_images or current_forwards:
            if vision:
                if current_images and self.lm.image_max_input_images > 0:
                    direct_parts = self._load_image_parts(
                        current_images[:self.lm.image_max_input_images], group_id, "当前消息图片", seen_images,
                        info_map=image_info_by_url)
                    current_parts.extend(direct_parts)
                    if direct_parts:
                        notes.append("当前消息中的图片已提供（最多 10 张）")
                    else:
                        notes.append("当前消息图片读取失败")
                forward_images = self._expand_forward_images(
                    current_forwards, group_id, seen_forwards)
                if forward_images:
                    forward_parts = self._load_image_parts(
                        forward_images, group_id, "转发聊天记录图片", seen_images,
                        info_map=image_info_by_url)
                    current_parts.extend(forward_parts)
                    forward_image_count += len(forward_parts)
            else:
                notes.append("当前消息包含图片/聊天记录，但当前模型不支持图像输入")

        if forward_image_count:
            notes.append("转发聊天记录中的图片已读取（最多 10 张）")
        if notes:
            unique_notes = []
            for note in notes:
                if note not in unique_notes:
                    unique_notes.append(note)
            current += "\n（" + "；".join(unique_notes) + "）"

        persona_context = []
        for message in context_messages:
            if message.get("role") == "assistant":
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            persona_context.append(str(content or ""))
        persona_context.append(current_base)

        system_prompt = self._system_prompt(
            group_id, "\n".join(persona_context), reply_entry=entry,
            include_group=False)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(quoted_messages)
        if current_parts:
            messages.append({
                "role": "user",
                "content": [{"type": "text", "text": current}] + current_parts,
            })
        else:
            messages.append({"role": "user", "content": current})

        # 群聊人设放在引用消息和当前触发者消息之后。
        # 注意：部分 Qwen Jinja 模板要求 system 只能出现在开头，
        # 因此这里用 user 消息承载最后的人设补充，避免 Channel Error。
        group_text = self._persona_text(group_id)
        if group_text:
            messages.append({
                "role": "user",
                "content": "[群聊人设]\n" + group_text,
            })

        # 群聊历史上下文放在最后。
        messages.extend(context_messages)

        total_images = context_image_count + len(current_parts)
        messages, total_images = keep_last_images(messages, self.lm.image_max_input_images)
        input_infos = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    info = image_info_by_url.get(part.get("image_url", {}).get("url"))
                    if info:
                        input_infos.append(info)
        cache_key = (group_id, str(entry.get("message_id") or ""))
        with self._image_inputs_lock:
            self._image_inputs_cache[cache_key] = input_infos
            while len(self._image_inputs_cache) > 200:
                self._image_inputs_cache.pop(next(iter(self._image_inputs_cache)))

        def _text_chars(content):
            if isinstance(content, list):
                return sum(
                    len(str(part.get("text", "")))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            return len(str(content or ""))

        context_chars = sum(_text_chars(item.get("content")) for item in context_messages)
        prompt_chars = sum(_text_chars(item.get("content")) for item in messages)
        self._record_context(group_id, {
            "messages": len(context_messages),
            "history_messages": len(context_entries),
            "chars": context_chars,
            "current_chars": len(current),
            "prompt_chars": prompt_chars,
            "system_chars": len(str(messages[0].get("content") or "")) if messages else 0,
            "images": total_images,
            "followup": bool(entry.get("followup")),
        })
        log.info("[生成] 群 %s，上下文 %d 条，图片 %d 张（最多 %d；候选 当前 %d / 上下文 %d / 转发 %d），请求模型 %s…",
                 group_id, len(context_messages), total_images, self.lm.image_max_input_images,
                 len(current_parts), context_image_count, forward_image_count,
                 self.lm.model or "自动")

        def mark_images_failed():
            if total_images:
                self._mark_image_inputs_failed(group_id, entry.get("message_id"))

        def text_only(fallback_text):
            output = []
            for message in messages:
                content = message.get("content")
                if isinstance(content, list):
                    content = "\n".join(
                        str(part.get("text", ""))
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                output.append({"role": message["role"], "content": str(content or "")})
            if output:
                output[-1] = {"role": "user", "content": fallback_text}
            return output

        try:
            reply = self.lm.chat(
                messages, reasoning_effort=self._general_reasoning_effort())
        except LMStudioError as exc:
            lowered = str(exc).lower()
            if total_images and any(key in lowered for key in
                                    ("image", "vision", "mmproj", "multimodal", "image_url",
                                     "projector", "mtmd", "channel error", "terminated",
                                     "engine protocol", "predict stream", "chunk")):
                log.warning("[图片] 模型拒绝或无法处理图片输入，改为纯文本重试：%s", exc)
                mark_images_failed()
                reply = self.lm.chat(
                    text_only(current_base + "\n（图片输入失败，请只根据文字回复。）"),
                    reasoning_effort=self._general_reasoning_effort())
            elif any(key in lowered for key in
                     ("channel error", "terminated", "engine protocol", "predict stream")):
                log.warning("[LM Studio] 推理通道异常，1 秒后重试一次：%s", exc)
                time.sleep(1.0)
                reply = self.lm.chat(
                    messages, reasoning_effort=self._general_reasoning_effort())
            else:
                raise
        if total_images and not (reply or "").strip():
            log.warning("[图片] 模型没有返回内容，改为纯文本重试。")
            mark_images_failed()
            reply = self.lm.chat(
                text_only(current_base + "\n（图片无法读取，请只根据文字回复。）"),
                reasoning_effort=self._general_reasoning_effort())
        if has_duplicate_sentences(reply):
            try:
                self._fatal_restart(group_id)
            except Exception as exc:  # noqa: BLE001
                log.error("[致命] 重启流程异常：%s", exc)
            raise FatalRestartError("模型输出连续重复")
        self._remember_raw_reply(group_id, reply)
        cleaned = clean_reply(
            reply,
            skip_markers=self.cfg["reply"]["skip_markers"],
            max_chars=0,  # 先不截断，交给分条逻辑统一处理
        )
        return cleaned

    def _message_id_from_result(self, result):
        if not isinstance(result, dict):
            return None
        value = result.get("message_id")
        if value is None and isinstance(result.get("data"), dict):
            value = result["data"].get("message_id")
        return str(value) if value is not None else None


__all__ = [name for name in globals() if not name.startswith("__")]
