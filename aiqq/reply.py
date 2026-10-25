#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ reply：回复清理、分段、延迟与文本处理。"""
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



from .text import strip_cq

def clean_reply(text, skip_markers=None, max_chars=600):
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    text = text.replace("```", "")
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    text = text.replace("**", "").replace("`", "")
    text = strip_cq(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = re.sub(r"^(AI|助手|机器人|Bot)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    if any(marker and marker in text for marker in (skip_markers or [])):
        return ""
    if max_chars > 0 and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text.strip()


def split_text(text, limit, max_chunks=3):
    """按换行 / 句末标点切分，长回复拆成多条。"""
    text = (text or "").strip()
    if not text:
        return []
    if limit <= 0 or len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while remaining and len(chunks) < max_chunks:
        if len(remaining) <= limit:
            chunks.append(remaining.strip())
            remaining = ""
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            best = -1
            for mark in "。！？!?；;":
                idx = remaining.rfind(mark, 0, limit)
                if idx > best:
                    best = idx
            cut = best
        if cut < limit // 2:
            piece = remaining[:limit]
            remaining = remaining[limit:]
        else:
            piece = remaining[: cut + 1]
            remaining = remaining[cut + 1:]
        piece = piece.strip()
        if piece:
            chunks.append(piece)

    if remaining:
        if not chunks:
            chunks = [remaining]
        else:
            last = chunks[-1]
            if len(last) >= limit:
                last = last[: limit - 1].rstrip()
            chunks[-1] = (last + "…") if not last.endswith("…") else last
    return [chunk for chunk in chunks if chunk]


def strip_periods(text):
    """删除句号；保留小数、网址和省略号中的点。"""
    if not text:
        return ""
    text = text.replace("。", "").replace("．", "")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\.(?=[\u4e00-\u9fff])", "", text)
    return re.sub(r"(?<![.\/])\.(?=\s|$)", "", text)


_PAIR_PLACEHOLDER_RE = re.compile(
    r"\[[^\[\]\r\n]{0,100}\]|【[^【】\r\n]{0,100}】|"
    r"(?<![:/A-Za-z0-9])/[^/\r\n]{1,100}/(?![/A-Za-z0-9])",
    re.IGNORECASE,
)
_PLACEHOLDER_WORD_RE = re.compile(
    r"^(?:发送了图片.*|发送图片.*|图片.*|聊天记录.*|存图.*|保存图片.*|IMG.*|SAVIMG.*)$",
    re.IGNORECASE,
)


def _is_media_placeholder(value):
    value = str(value or "").strip()
    if not value:
        return True
    if value.lower() == "enter":
        return False
    return bool(_PLACEHOLDER_WORD_RE.match(value))


def strip_speaker_labels(text, extra_names=()):
    """删除模型误带的 [迪普西克]/[肥鱼] 等说话人标签。"""
    if not text:
        return ""
    labels = {
        "迪普西克", "肥鱼", "鲸鲸", "蓝色大肥鱼", "你",
        "assistant", "user", "bot", "ai", "机器人", "用户",
    }
    labels.update(str(name).strip() for name in (extra_names or []) if str(name).strip())
    result = text
    for label in sorted(labels, key=len, reverse=True):
        pattern = re.compile(
            r"[\[【（(]\s*" + re.escape(label) + r"\s*[\]】）)]\s*[:：,，]?\s*",
            re.IGNORECASE,
        )
        result = pattern.sub("", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result


def strip_media_placeholders(text):
    """删除成对 []、【】、// 中残留的无效发图/存图占位。"""
    if not text:
        return ""

    def replace(match):
        return "" if _is_media_placeholder(match.group(0)[1:-1]) else match.group(0)

    previous = None
    while text != previous:
        previous = text
        text = _PAIR_PLACEHOLDER_RE.sub(replace, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([，。！？、；：,.!?;:])", r"\1", text)
    return text.strip()


_DUPLICATE_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[。！？!?；;…])|\n+|[/／]\s*Enter\s*[/／]", re.IGNORECASE)


def has_duplicate_sentences(text):
    """相邻两句话内容完全相同时返回 True。"""
    previous = None
    for part in _DUPLICATE_SENTENCE_SPLIT_RE.split(str(text or "")):
        sentence = (part or "").strip().rstrip("。！？!?…；;")
        if not sentence:
            continue
        if previous is not None and sentence == previous:
            return True
        previous = sentence
    return False


def remove_linebreaks(text):
    """删除正文换行（含 CRLF/Unicode 行分隔符），只保留 /Enter/ 等文本标记。"""
    if not text:
        return ""
    text = re.sub(r"[ \t]*[\r\n\u2028\u2029]+[ \t]*", " ", text)
    return text.strip()


_LEADING_BREAK_RE = re.compile(
    r"^(?:\s*(?:[/／]\s*Enter\s*(?:[/／]|$)|\\n|<br\s*/?>|\[(?:换行|br|BR)\]))+",
    re.IGNORECASE,
)
_TRAILING_BREAK_RE = re.compile(
    r"(?:\s*(?:[/／]\s*Enter\s*[/／]|\\n|<br\s*/?>|\[(?:换行|br|BR)\]))+\s*$",
    re.IGNORECASE,
)


def strip_leading_breaks(text, marker=""):
    """只清理回复开头的换行/分段标记，正文中间的标记保持原样。"""
    if not text:
        return ""
    text = text.lstrip()
    while True:
        cleaned = _LEADING_BREAK_RE.sub("", text).lstrip()
        if cleaned == text:
            return cleaned
        text = cleaned


def strip_trailing_breaks(text):
    """删除回复末尾单独出现的 /Enter/ 等分段/换行标记。"""
    if not text:
        return ""
    return _TRAILING_BREAK_RE.sub("", text).rstrip()


def segment_delay(text, min_sec=1.0, max_sec=60.0, rate_min=3.0, rate_max=5.0, rng=None, base_sec=0.0):
    """按随机 3~5 字/秒估算打字等待时间，并套用最短/最长限制。"""
    min_sec = max(0.0, float(min_sec))
    max_sec = max(min_sec, float(max_sec))
    rate_min = max(0.1, float(rate_min))
    rate_max = max(rate_min, float(rate_max))
    base_sec = max(0.0, float(base_sec))
    chars = len((text or "").strip())
    if chars <= 0:
        return min_sec + base_sec
    generator = rng or random
    rate = generator.uniform(rate_min, rate_max)
    return min(max_sec, max(min_sec, chars / rate + base_sec))


_ENTER_TOKEN = r"(?:[\[【(（/／]\s*)?Enter\s*(?:[\]】)）/／])?"
_AFTER_COMMA_BREAK_RE = re.compile(
    r"([,，])[ \t]*(?:"
    r"[\r\n\u2028\u2029]+[ \t]*(?:" + _ENTER_TOKEN + r"[ \t]*(?:[\r\n\u2028\u2029]+[ \t]*)?)?"
    r"|" + _ENTER_TOKEN + r"[ \t]*(?:[\r\n\u2028\u2029]+[ \t]*)?"
    r")",
    re.IGNORECASE,
)


def ignore_comma_breaks(text):
    """逗号后的换行和 /Enter/ 不参与分段，也不保留。"""
    if not text:
        return ""
    return _AFTER_COMMA_BREAK_RE.sub(r"\1", text)


def split_by_marker(text, marker, slash_fallback=False):
    """
    按模型输出的显式分段符拆分。

    默认标记 /Enter/，同时兼容 ／Enter／、/ ENTER / 等写法；
    标记本身不会出现在发送内容里。
    """
    text = (text or "").strip()
    if not text:
        return []
    marker = (marker or "").strip()
    if not marker:
        return [text]

    wrapped = re.fullmatch(r"[/／](.+?)[/／]", marker)
    if wrapped and wrapped.group(1).strip():
        core = wrapped.group(1).strip()
        if core.lower() == "enter":
            pattern = re.compile(_ENTER_TOKEN, re.IGNORECASE)
        else:
            core_pattern = r"\s+".join(re.escape(part) for part in core.split())
            pattern = re.compile(r"[/／]\s*" + core_pattern + r"\s*[/／]", re.IGNORECASE)
    elif marker.strip().lower() == "enter":
        pattern = re.compile(_ENTER_TOKEN, re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(marker), re.IGNORECASE)

    parts = [part.strip() for part in pattern.split(text) if part.strip()]
    if len(parts) == 1 and slash_fallback and marker.lower() == "/enter/":
        parts = split_slash_fallback(text)
    return parts


def split_slash_fallback(text):
    """
    兼容模型把 /Enter/ 写成裸 / 的情况。

    只拆分“中文/英文单词两侧”的斜杠：
      - 有空格：那是 / 我这儿 / 省得你多掏钱
      - 无空格：在的哈/我是小助手/没啥好介绍的
    不会拆 https://...、C:/...、1 / 2、单个“他/她”。
    """
    text = (text or "").strip()
    if not text:
        return []

    spaced = re.compile(r"(?<=[\u4e00-\u9fffA-Za-z])\s+/\s+(?=[\u4e00-\u9fffA-Za-z])")
    if spaced.search(text):
        return [part.strip() for part in re.split(r"\s+/\s+", text) if part.strip()]

    cjk = re.compile(r"(?<=[\u4e00-\u9fff])\s*/\s*(?=[\u4e00-\u9fff])")
    if len(cjk.findall(text)) >= 2:
        return [part.strip() for part in cjk.split(text) if part.strip()]

    return [text]


def cap_messages(messages, limit, max_messages):
    """限制一条回复最多发几条；超出的部分合并到最后一条。"""
    if max_messages <= 0 or len(messages) <= max_messages:
        return list(messages)
    head = list(messages[: max_messages - 1])
    tail = "\n".join(messages[max_messages - 1:]).strip()
    if not tail:
        return head
    if limit > 0 and len(tail) > limit:
        tail = tail[: limit - 1].rstrip() + "…"
    head.append(tail)
    return head

__all__ = [name for name in globals() if not name.startswith("__")]
