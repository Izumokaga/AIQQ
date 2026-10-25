#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ media：CQ 码、图片、引用、转发与语音段解析。"""
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



from .config import BASE_DIR, CQ_RE, MEDIA_PLACEHOLDERS

def _cq_params(raw):
    params = {}
    for item in raw.strip(",").split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            params[key.strip()] = html.unescape(value.strip())
    return params


def cq_string_to_text(message, self_id):
    """把 CQ 码字符串转换成 (纯文本, 是否@了机器人)。"""
    text_parts = []
    mentioned = False
    pos = 0
    for match in CQ_RE.finditer(message):
        text_parts.append(html.unescape(message[pos:match.start()]))
        seg_type = match.group(1)
        params = _cq_params(match.group(2))
        if seg_type == "text":
            text_parts.append(html.unescape(params.get("text", "")))
        elif seg_type == "at":
            qq = str(params.get("qq", ""))
            if self_id and qq == str(self_id):
                mentioned = True
                text_parts.append("@你")
            elif qq == "all":
                text_parts.append("@全体成员")
            else:
                text_parts.append("@" + (params.get("name") or "成员"))
        elif seg_type == "reply":
            pass
        else:
            text_parts.append(MEDIA_PLACEHOLDERS.get(seg_type, ""))
        pos = match.end()
    text_parts.append(html.unescape(message[pos:]))
    return "".join(text_parts), mentioned


def extract_voice_segments(message):
    """提取消息中的语音段，返回 URL/本地路径信息。"""
    voices = []
    seen = set()

    def add(data):
        if not isinstance(data, dict):
            return
        info = {
            "url": str(data.get("url") or "").strip(),
            "file": str(data.get("file") or "").strip(),
        }
        key = (info["url"], info["file"])
        if not any(key) or key in seen:
            return
        seen.add(key)
        voices.append(info)

    if isinstance(message, str):
        for match in CQ_RE.finditer(message):
            if match.group(1) == "record":
                add(_cq_params(match.group(2)))
    elif isinstance(message, list):
        for segment in message:
            if isinstance(segment, dict) and segment.get("type") == "record":
                add(segment.get("data") or {})
    return voices


def extract_mentions(message, self_id):
    """提取消息中被 @ 的用户，用于把用户名写进上下文。"""
    mentions = []
    seen = set()

    def add(qq, name=""):
        qq = str(qq or "").strip()
        if not qq or qq == "all" or qq in seen:
            return
        seen.add(qq)
        mentions.append({"user_id": qq, "name": str(name or "")})

    if isinstance(message, str):
        for match in CQ_RE.finditer(message):
            if match.group(1) != "at":
                continue
            params = _cq_params(match.group(2))
            add(params.get("qq"), params.get("name"))
    elif isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "at":
                continue
            data = segment.get("data") or {}
            add(data.get("qq"), data.get("name"))
    return mentions


def message_to_text(message, self_id):
    """兼容数组消息段和 CQ 码字符串，返回 (纯文本, 是否@了机器人)。"""
    if message is None:
        return "", False
    if isinstance(message, str):
        return cq_string_to_text(message, self_id)
    if not isinstance(message, list):
        return str(message), False

    parts = []
    mentioned = False
    for segment in message:
        if not isinstance(segment, dict):
            continue
        seg_type = segment.get("type")
        data = segment.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        elif seg_type == "at":
            qq = str(data.get("qq", ""))
            if self_id and qq == str(self_id):
                mentioned = True
                parts.append("@你")
            elif qq == "all":
                parts.append("@全体成员")
            else:
                parts.append("@" + str(data.get("name") or "成员"))
        elif seg_type == "reply":
            continue
        elif seg_type in MEDIA_PLACEHOLDERS:
            parts.append(MEDIA_PLACEHOLDERS[seg_type])
    return "".join(parts), mentioned


class ImageInputError(Exception):
    pass


def _image_info(data):
    url = str(data.get("url") or "").strip()
    file_path = str(data.get("file") or data.get("path") or "").strip()
    if not url and not file_path:
        return None
    return {
        "url": url,
        "file": file_path,
        "summary": str(data.get("summary") or "").strip(),
    }


def extract_image_segments(message):
    """从数组消息段或 CQ 字符串里提取图片地址。"""
    images = []
    seen = set()
    if isinstance(message, str):
        for match in CQ_RE.finditer(message):
            if match.group(1) != "image":
                continue
            info = _image_info(_cq_params(match.group(2)))
            if info:
                key = (info["url"], info["file"])
                if key not in seen:
                    seen.add(key)
                    images.append(info)
        return images
    if not isinstance(message, list):
        return images
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "image":
            continue
        info = _image_info(segment.get("data") or {})
        if info:
            key = (info["url"], info["file"])
            if key not in seen:
                seen.add(key)
                images.append(info)
    return images


def extract_reply_id(message):
    """提取当前消息引用的 message_id（数组消息段或 CQ 码）。"""
    if isinstance(message, str):
        for match in CQ_RE.finditer(message):
            if match.group(1) == "reply":
                value = _cq_params(match.group(2)).get("id")
                if value:
                    return str(value)
        return ""
    if not isinstance(message, list):
        return ""
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "reply":
            continue
        data = segment.get("data") or {}
        value = data.get("id") or data.get("message_id")
        if value:
            return str(value)
    return ""


def extract_forward_segments(message):
    """提取 forward（聊天记录）消息段，支持数组和 CQ 码。"""
    forwards = []
    seen = set()

    def add(data):
        forward_id = str(data.get("id") or data.get("message_id") or data.get("resid") or "").strip()
        content = data.get("content") or data.get("messages")
        if isinstance(content, dict):
            content = content.get("messages") or content.get("message") or []
        if not isinstance(content, list):
            content = None
        if not forward_id and not content:
            return
        key = forward_id or ("content:%d" % id(content))
        if key in seen:
            return
        seen.add(key)
        forwards.append({"id": forward_id, "content": content})

    if isinstance(message, str):
        for match in CQ_RE.finditer(message):
            if match.group(1) != "forward":
                continue
            add(_cq_params(match.group(2)))
        return forwards
    if not isinstance(message, list):
        return forwards
    for segment in message:
        if isinstance(segment, dict) and segment.get("type") == "forward":
            add(segment.get("data") or {})
    return forwards


def _image_mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


def image_dimensions(data):
    """读取常见图片格式的宽高；无法识别时返回 None。"""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
        if data.startswith(b"BM") and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return abs(int(width)), abs(int(height))
        if data.startswith(b"\xff\xd8\xff"):
            index = 2
            while index + 4 <= len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                index += 2
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    continue
                if index + 2 > len(data):
                    break
                length = int.from_bytes(data[index:index + 2], "big")
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6,
                              0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if index + 7 <= len(data):
                        height = int.from_bytes(data[index + 3:index + 5], "big")
                        width = int.from_bytes(data[index + 5:index + 7], "big")
                        return int(width), int(height)
                    break
                if length < 2:
                    break
                index += length
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return int(width), int(height)
            if chunk == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 ":
                start = data.find(b"\x9d\x01\x2a", 20)
                if start >= 0 and start + 7 <= len(data):
                    width = int.from_bytes(data[start + 3:start + 5], "little") & 0x3FFF
                    height = int.from_bytes(data[start + 5:start + 7], "little") & 0x3FFF
                    return int(width), int(height)
    except (IndexError, struct.error, ValueError):
        pass
    return None


def image_bytes_to_data_url(data):
    if not data:
        raise ImageInputError("图片内容为空")
    mime = _image_mime(data)
    encoded = base64.b64encode(data).decode("ascii")
    return "data:%s;base64,%s" % (mime, encoded)


def keep_last_images(messages, limit=10):
    """最多保留消息列表中最后 N 张图片，返回 (messages, 图片数)。"""
    limit = max(0, int(limit))
    locations = []
    for message_index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part_index, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") == "image_url":
                locations.append((message_index, part_index))
    keep = set(locations[-limit:]) if limit else set()
    for message_index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        kept = []
        for part_index, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") == "image_url":
                if (message_index, part_index) in keep:
                    kept.append(part)
            else:
                kept.append(part)
        message["content"] = kept or [{"type": "text", "text": "[图片已省略]"}]
    return messages, len(keep)


def _download_image_bytes(url, max_bytes):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.8",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        length = response.headers.get("Content-Length")
        if length:
            try:
                if int(length) > max_bytes:
                    raise ImageInputError("图片超过大小上限（%d 字节）" % max_bytes)
            except ValueError:
                pass
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ImageInputError("图片超过大小上限（%d 字节）" % max_bytes)
        return data


def read_image_bytes(info, max_bytes):
    """按 URL / 本地文件 / base64:// 读取图片字节。"""
    errors = []
    candidates = [info.get("url"), info.get("file")]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            if candidate.startswith("base64://"):
                raw = base64.b64decode(candidate[len("base64://"):], validate=False)
                if len(raw) > max_bytes:
                    raise ImageInputError("图片超过大小上限")
                if raw:
                    return raw
                raise ImageInputError("base64 图片为空")
            if candidate.startswith(("http://", "https://")):
                return _download_image_bytes(candidate, max_bytes)
            path = candidate[len("file://"):] if candidate.startswith("file://") else candidate
            path = urllib.request.url2pathname(path)
            paths = [path]
            if not os.path.isabs(path):
                for sub in ("NapCat.Shell/data/images", "NapCat.Shell/data/image",
                            "NapCat.Shell/data/images/original", "NapCat.Shell/data/images/temp",
                            "data/images", "data/image"):
                    paths.append(os.path.join(BASE_DIR, sub, path))
            for item in paths:
                if os.path.isfile(item):
                    if os.path.getsize(item) > max_bytes:
                        raise ImageInputError("图片超过大小上限")
                    with open(item, "rb") as fh:
                        return fh.read()
            raise ImageInputError("本地文件不存在")
        except Exception as exc:
            errors.append(str(exc))
    raise ImageInputError("；".join(errors) or "没有可读取的图片地址")

__all__ = [name for name in globals() if not name.startswith("__")]
