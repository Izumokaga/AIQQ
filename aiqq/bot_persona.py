#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ bot_persona：人设、上下文构建与提示词。"""
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






from .config import BASE_DIR, DEFAULT_CONFIG, DEFAULT_SYSTEM_PROMPT, _as_int, log
from .media import _image_mime, extract_forward_segments, extract_image_segments, extract_reply_id, image_bytes_to_data_url, message_to_text, read_image_bytes
from .onebot import OneBotError

class BotPersonaMixin:
    def _persona_text(self, group_id=None):
        if group_id:
            # 群聊人设单独读取，不再回退全局人设。
            return self.group_persona.read(group_id)
        fallback = str(self.cfg["lm_studio"].get("system_prompt") or DEFAULT_SYSTEM_PROMPT)
        filename = str(self.cfg["lm_studio"].get("persona_file") or "").strip()
        if not filename:
            return fallback
        path = filename if os.path.isabs(filename) else os.path.join(BASE_DIR, filename)
        try:
            mtime = os.path.getmtime(path)
            with self._persona_lock:
                cached_mtime, cached_text = self._persona_cache
                if cached_text and cached_mtime == mtime:
                    return cached_text
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read().strip()
            if text:
                with self._persona_lock:
                    self._persona_cache = (mtime, text)
                return text
        except OSError as exc:
            log.debug("读取人设文件 %s 失败：%s", path, exc)
        return fallback

    def _reply_target_instruction(self, entry):
        name = str((entry or {}).get("nickname") or "").strip() or "当前触发者"
        user_id = _as_int((entry or {}).get("user_id"), 0)
        target = "%s（QQ%s）" % (name, user_id) if user_id else name
        return (
            "[回复对象] 本轮只回复触发者 %s。"
            "上下文中其他群成员的消息只作为参考，不要回复、点名或替其他成员发言。"
            % target
        )

    def _general_reasoning_effort(self):
        return "high" if bool(
            self.cfg["lm_studio"].get("general_deep_thinking", False)) else "none"

    def _persona_relevant(self, context_text):
        text = (context_text or "").casefold()
        if not text:
            return False
        for keyword in self.cfg["lm_studio"].get("persona_keywords") or []:
            keyword = str(keyword).strip()
            if keyword and keyword.casefold() in text:
                return True
        return False

    @staticmethod
    def _current_time_text():
        now = datetime.now().astimezone()
        weekdays = "一二三四五六日"
        hour = now.hour
        if hour < 5:
            period = "凌晨"
        elif hour < 8:
            period = "早上"
        elif hour < 11:
            period = "上午"
        elif hour < 13:
            period = "中午"
        elif hour < 17:
            period = "下午"
        elif hour < 19:
            period = "傍晚"
        elif hour < 23:
            period = "晚上"
        else:
            period = "深夜"
        return "%s 星期%s（%s）" % (
            now.strftime("%Y-%m-%d %H:%M"), weekdays[now.weekday()], period)

    def _system_prompt(self, group_id, context_text="", reply_entry=None,
                       include_group=True):
        lm = self.cfg["lm_studio"]
        mode = str(lm.get("persona_mode") or "adaptive").lower()
        if mode == "always":
            use_full = True
        elif mode == "off":
            use_full = False
        else:
            use_full = self._persona_relevant(context_text)

        # 非专业模式始终先加载 init 中的简短人设；触发完整人设时再追加群级人设。
        base = str(lm.get("persona_short_prompt") or
                   DEFAULT_CONFIG["lm_studio"]["persona_short_prompt"])
        if use_full:
            # 触发完整读取后读取全局人设，位置在群聊人设之前。
            full_text = self._persona_text()
            if full_text:
                base += "\n\n[完整人设]\n" + full_text
            identity_note = ("（仅用于识别，请保持人设中的身份，"
                             "不要用账号昵称替代人设）。")
        else:
            identity_note = "（仅用于识别）。"
        base = base.replace("{bot_name}", self.self_nick)
        log.debug("[人设] 模式=%s，使用%s提示（上下文涉及人设=%s）",
                  mode, "完整" if use_full else "简短", use_full)

        catalog = self.images.catalog_text(
            int(self.cfg["images"].get("catalog_limit", 20)))
        base += (
            "\n\n[肥鱼的图片能力] 存图和发图是你自己的能力，只有你有权限决定，不是用户操作。"
            "用户不能直接命令你保存图片或调用图库；"
            "你可以因为自己喜欢而收下当前或最近的图片：在回复中单独写 "
            "/SAVIMG:图片位于第几张:图片名:详细描述:喜好度（百分制）/，"
            "位置字段可写 1、第1张或“图片位于第1张”；"
            "标记会被执行并从文字中移除，详细描述和喜好度会写入 image_index.txt；"
            "第几张按本次输入图片的顺序从 1 开始数，一次最多 10 张，只保存你指定的那一张；"
            "你越喜欢、越像你自己，分数越高。如果标记单独出现，不会额外发文字。"
            "遇到适合自己风格的表情包，可以主动用 SAVIMG 收下，名字用情绪或场景。"
            "你可以主动发图，也可以应要求发图；只能使用下面列出的图库名字，"
            "并在回复中单独写 /IMG:名字/；不要编造不存在的图片。"
            "聊天时情景合适可以发一张贴切的表情包，但不要每句都发。"
        )
        if catalog:
            base += "\n你的图库内容包括：" + catalog
        else:
            base += "\n你的图库内容包括：空"

        marker = str(self.cfg["reply"].get("split_marker") or "").strip()
        if marker:
            base += (
                "\n\n[多条消息] 需要把回复拆成多条短消息时，必须在每条之间输出完整的 %s"
                "（斜杠 + Enter + 斜杠），不要只用单个 /、换行或句号代替；"
                "这个标记不会被发送，而是自动转换为多条独立的 QQ 消息。例如：第一句%s第二句。"
                "不要在回复开头或结尾放这个标记，否则会被忽略。"
                % (marker, marker)
            )
        base += (
            "\n\n[标点] 句尾不用句号（中文“。”/英文“.”）：直接停住，或用逗号、空格、语气词收尾；"
            "不要为了句子完整补句号，拆成多条时每条末尾也不要补。"
        )
        base += (
            "\n\n[语音消息] 语音是为了让当前人设更真实，不是情绪表演；只有在用这个角色的声音说出来更自然时才发，"
            "普通聊天继续打字，不要为了展示情绪而发语音。想发语音时，在句首或 /Enter/ 后写 /VOICE:语气描述/。"
            "描述写声线、年龄感、性格和说话习惯。"
            "标记出现后，从这一句开始直到下一处 /VOICE:描述/ 之前都用同一语气；"
            "下一处标记同样要放在句首或 /Enter/ 后。"
            "不写描述时用 /VOICE/ 使用默认声音。整段回复仍按 /Enter/ 拆分成多段，"
            "每一段分别合成一条语音并按顺序发送；每段语音的延迟为语音时长加 1~3 秒。"
            "语音语气只在当前回复内有效，不写入上下文，也不会跨消息或缓存继承。"
            "语音只包含正文，不要包含图片/存图标记、/Enter/、/VOICE/ 或系统提示词。"
            "不要把 /VOICE 提示单独放在段尾；标记后必须紧跟要朗读的正文，否则会被忽略。"
            "\n[语音人设] 一切描述都以维持当前人设为先：声线、年龄感、口癖和说话方式必须始终像同一个人。"
            "情绪只能做轻微修饰，例如着急时稍微加快、音调略提，懒散时稍微放慢，但不能变成另一个角色；"
            "不要写夸张的变声、咆哮或成熟化描述，也不要每句都换语气。"
        )
        group_name = self.group_names.get(group_id) or "未知"
        prompt = (
            base
            + "\n\n[当前时间] 系统时间：" + self._current_time_text() + "。"
              "你知道现在的时间，但不要在回复里反复报时、强调“现在是几点”或机械地重复日期；"
              "只有用户明确问时间、约时间，或对话自然需要时才提。"
              "平时可以用“早上”“下午”“今晚”“周末”“这么晚”等更像真人的时间感，"
              "自然地融进聊天，不要每条都带时间。"
            + "\n[运行信息] 群号：%s；群名：%s；你的 QQ 账号昵称：%s%s"
              "请直接回复最新一条群消息。"
            % (group_id, group_name, self.self_nick, identity_note)
        )
        if reply_entry is not None:
            prompt += "\n\n" + self._reply_target_instruction(reply_entry)
        # 默认把群聊人设放在 system 末尾；普通聊天会将它移到当前消息之后。
        if include_group:
            group_text = self._persona_text(group_id) if group_id else ""
            if group_text:
                prompt += "\n\n[群聊人设]\n" + group_text
        return prompt

    def _resolve_message_user(self, message_id, context_entries, cache):
        key = str(message_id or "")
        if not key:
            return 0
        if key in cache:
            return cache[key]
        for item in context_entries:
            if str(item.get("message_id") or "") == key:
                user_id = _as_int(item.get("user_id"), 0)
                cache[key] = user_id
                return user_id
        try:
            raw = self.onebot.get_msg(key)
            user_id = _as_int(raw.get("user_id"), 0)
        except Exception as exc:  # noqa: BLE001 - 拉不到引用消息时不影响主流程
            log.debug("解析引用消息 %s 的发送者失败：%s", key, exc)
            user_id = 0
        cache[key] = user_id
        return user_id

    def _filter_context_by_sender(self, entry, context_entries):
        """被 @ 或次级搭话时只保留当前发送者及其回复过的其他成员的消息。"""
        if not self.cfg["lm_studio"].get("context_sender_only", True):
            return context_entries
        if not (entry.get("mentioned") or entry.get("followup")):
            return context_entries
        sender_id = _as_int(entry.get("user_id"), 0)
        if not sender_id:
            return context_entries

        participants = {sender_id}
        cache = {}
        for item in context_entries:
            if _as_int(item.get("user_id"), 0) != sender_id:
                continue
            reply_to = str(item.get("reply_to") or "")
            if not reply_to:
                continue
            quoted_user = self._resolve_message_user(reply_to, context_entries, cache)
            if quoted_user and quoted_user != self.self_id:
                participants.add(quoted_user)

        reply_to = str(entry.get("reply_to") or "")
        if reply_to:
            quoted_user = self._resolve_message_user(reply_to, context_entries, cache)
            if quoted_user and quoted_user != self.self_id:
                participants.add(quoted_user)

        include_bot = bool(self.cfg["lm_studio"].get("context_include_bot", False))
        filtered = [
            item for item in context_entries
            if _as_int(item.get("user_id"), 0) in participants
            or (include_bot and item.get("is_self"))
        ]
        if len(filtered) != len(context_entries):
            log.info("[上下文] 只保留发送者 %s 及其回复对象的历史：%d -> %d 条",
                     sender_id, len(context_entries), len(filtered))
        return filtered

    def _history_entries(self, group_id, current_id, limit, min_time, sender_entry=None):
        entries = []
        sender_filter = bool(
            sender_entry
            and sender_entry.get("mentioned")
            and self.cfg["lm_studio"].get("context_sender_only", True)
        )
        fetch_count = max(limit + 8, 60) if sender_filter else limit + 8
        try:
            history = self.onebot.get_group_msg_history(group_id, count=fetch_count)
        except OneBotError as exc:
            log.warning("拉取群历史失败，改用内存上下文：%s", exc)
            history = []

        for raw in history or []:
            mid = str(raw.get("message_id"))
            if current_id is not None and mid == str(current_id):
                continue
            text, _ = message_to_text(raw.get("message"), self.self_id)
            text = text.strip()
            images = extract_image_segments(raw.get("message"))
            forwards = extract_forward_segments(raw.get("message"))
            if not text and not images and not forwards:
                continue
            text = text or "[图片]"
            timestamp = _as_int(raw.get("time"), 0)
            if min_time and timestamp and timestamp < min_time:
                continue
            user_id = _as_int(raw.get("user_id"), 0)
            sender = raw.get("sender") or {}
            nickname = str(sender.get("card") or sender.get("nickname") or user_id)
            is_self = bool(self.self_id and user_id == self.self_id)
            if is_self:
                images = []
                forwards = []
            content = text if is_self else "%s：%s" % (nickname, text)
            entries.append({
                "role": "assistant" if is_self else "user",
                "content": content,
                "images": images,
                "forwards": forwards,
                "message_id": mid,
                "user_id": user_id,
                "reply_to": extract_reply_id(raw.get("message")),
                "is_self": is_self,
                "time": timestamp,
            })

        if limit > 0:
            memory_limit = max(limit + 5, 60) if sender_filter else limit + 5
            seen_ids = {str(item.get("message_id")) for item in entries}
            for item in self.memory.recent(group_id, memory_limit):
                mid = str(item.get("message_id") or "")
                if current_id is not None and mid == str(current_id):
                    continue
                if mid and mid in seen_ids:
                    continue
                text = (item.get("text") or "").strip()
                images = [] if item.get("is_self") else (item.get("images") or [])
                forwards = [] if item.get("is_self") else (item.get("forwards") or [])
                if not text and not images and not forwards:
                    continue
                text = text or "[图片]"
                if min_time and item.get("time") and item["time"] < min_time:
                    if not item.get("restored"):
                        continue
                content = text if item.get("is_self") else "%s：%s" % (item.get("nickname"), text)
                entries.append({
                    "role": "assistant" if item.get("is_self") else "user",
                    "content": content,
                    "images": images,
                    "forwards": forwards,
                    "message_id": mid,
                    "user_id": _as_int(item.get("user_id"), 0),
                    "reply_to": str(item.get("reply_to") or ""),
                    "is_self": bool(item.get("is_self")),
                    "time": item.get("time") or 0,
                })
                if mid:
                    seen_ids.add(mid)

        # 先按发送者过滤，再做字数裁剪，避免其他人的消息把发送者历史挤掉
        if sender_filter:
            entries = self._filter_context_by_sender(sender_entry, entries)

        # 去掉相邻重复，按字数上限从新到旧裁剪（兼容历史接口返回乱序）
        entries.sort(key=lambda item: item.get("time") or 0)
        deduped = []
        for entry in entries:
            entry_images = tuple((img.get("url") or "", img.get("file") or "")
                                 for img in (entry.get("images") or []))
            entry_forwards = tuple((fwd.get("id") or "") for fwd in (entry.get("forwards") or []))
            previous = deduped[-1] if deduped else None
            previous_images = tuple((img.get("url") or "", img.get("file") or "")
                                    for img in (previous.get("images") or [])) if previous else ()
            previous_forwards = tuple((fwd.get("id") or "")
                                      for fwd in (previous.get("forwards") or [])) if previous else ()
            if (previous and previous["content"] == entry["content"]
                    and previous_images == entry_images
                    and previous_forwards == entry_forwards
                    and previous.get("user_id") == entry.get("user_id")):
                continue
            deduped.append(entry)

        limit_chars = int(self.cfg["lm_studio"]["context_max_chars"])
        selected = []
        total = 0
        for entry in reversed(deduped):
            if limit_chars and selected and total + len(entry["content"]) > limit_chars:
                break
            selected.append(entry)
            total += len(entry["content"])
        selected.reverse()

        # 保留每条群消息的边界，图片窗口按真实消息条数计算
        messages = []
        for entry in selected:
            messages.append({
                "role": entry["role"],
                "content": entry["content"],
                "images": entry.get("images") or [],
                "forwards": entry.get("forwards") or [],
                "message_id": entry.get("message_id"),
                "user_id": entry.get("user_id") or 0,
                "reply_to": entry.get("reply_to") or "",
                "is_self": bool(entry.get("is_self")),
            })
        return messages

    def _read_image_bytes(self, info):
        """先按事件里的 URL/本地路径读取，失败再让 NapCat 的 get_image 兜底。"""
        try:
            return read_image_bytes(info, self.lm.image_max_bytes)
        except Exception as first:
            file_path = info.get("file") or ""
            if not file_path:
                raise
            try:
                got = self.onebot.get_image(file_path)
                for key in ("file", "path", "url"):
                    value = got.get(key)
                    if not value:
                        continue
                    try:
                        raw = read_image_bytes({key: value}, self.lm.image_max_bytes)
                        log.debug("[图片] 已通过 NapCat get_image 获取图片")
                        return raw
                    except Exception:
                        continue
            except Exception as exc:  # noqa: BLE001
                log.debug("[图片] NapCat get_image 失败：%s", exc)
            raise first

    def _load_image_parts(self, infos, group_id, label, seen=None, info_map=None):
        parts = []
        seen = seen if seen is not None else set()
        for info in infos:
            key = (info.get("url") or "", info.get("file") or "")
            if key in seen:
                continue
            seen.add(key)
            try:
                raw = self._read_image_bytes(info)
                mime = _image_mime(raw)
                if self.lm.image_only_png_jpeg and mime not in ("image/png", "image/jpeg"):
                    log.warning("[图片] %s格式为 %s，当前仅允许 PNG/JPEG，已跳过", label, mime)
                    continue
                data_url = image_bytes_to_data_url(raw)
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_url},
                })
                if info_map is not None:
                    info_map[data_url] = info
            except Exception as exc:  # noqa: BLE001 - 单张图片失败不影响文字回复
                log.warning("[图片] %s读取失败，跳过：%s", label, exc)
        if parts:
            log.info("[图片] 群 %s 已附加%s %d 张（模型 %s）",
                     group_id, label, len(parts), self.lm.model or "自动")
        return parts

    def _expand_forward_images(self, forwards, group_id, seen_forwards=None, depth=0):
        """展开聊天记录，收集其中全部图片；转发内图片不受上下文张数上限限制。"""
        if not forwards or depth > 2:
            return []
        seen_forwards = seen_forwards if seen_forwards is not None else set()
        images = []
        for forward in forwards:
            forward_id = str(forward.get("id") or "")
            if forward_id and forward_id in seen_forwards:
                continue
            if forward_id:
                seen_forwards.add(forward_id)
            content = forward.get("content")
            if isinstance(content, dict):
                content = content.get("messages") or content.get("message") or []
            if not content and forward_id:
                try:
                    content = self.onebot.get_forward_msg(forward_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("[图片] 展开聊天记录 %s 失败：%s", forward_id, exc)
                    content = []
            for nested in content or []:
                if isinstance(nested, dict):
                    nested_message = nested.get("message")
                    if nested_message is None:
                        nested_message = nested.get("content")
                else:
                    nested_message = nested
                if not nested_message:
                    continue
                images.extend(extract_image_segments(nested_message))
                nested_forwards = extract_forward_segments(nested_message)
                if nested_forwards:
                    images.extend(self._expand_forward_images(
                        nested_forwards, group_id, seen_forwards, depth + 1))
        if images:
            log.info("[图片] 群 %s 从转发聊天记录中解析出 %d 张图片", group_id, len(images))
        return images

    def _group_context_entries(self, group_id):
        group_messages = self.group_context.messages(group_id)
        entries = []
        for item in group_messages:
            role = "assistant" if item.get("role") == "assistant" else "user"
            content = str(item.get("content") or "")
            if role == "assistant":
                content = "[迪普西克] %s" % content
            else:
                name = str(item.get("name") or "群友")
                content = "%s：%s" % (name, content)
            entries.append({"role": role, "content": content})
        return entries

    def _user_context_entries(self, user_id):
        entries = []
        for index, item in enumerate(self.user_context.messages(user_id)):
            role = "assistant" if item.get("role") == "assistant" else "user"
            if role == "assistant":
                content = "[迪普西克] %s" % str(item.get("content") or "")
            else:
                name = str(item.get("name") or "").strip()
                body = str(item.get("content") or "")
                content = "[user] " + (("%s：%s" % (name, body)) if name else body)
                mention_names = [
                    "%s(QQ%s)" % (m.get("name") or "成员", m.get("user_id") or "")
                    for m in (item.get("mentions") or [])
                    if isinstance(m, dict)
                ]
                if mention_names:
                    content += "\n[被@] " + "、".join(mention_names)
            entries.append({
                "role": role,
                "content": content,
                "images": [],
                "forwards": [],
                "message_id": "userctx-%s-%d" % (user_id, index),
                "is_self": role == "assistant",
                "time": 0,
            })
        return entries


__all__ = [name for name in globals() if not name.startswith("__")]
