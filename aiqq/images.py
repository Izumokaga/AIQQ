#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ images：图片库与收图/发图标记。"""
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



IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}

_IMAGE_MARKER_RE = re.compile(
    r"/\s*IMG\s*[:：]\s*([^/\r\n]{1,60}?)\s*(?:/|(?=\r?\n|$))",
    re.IGNORECASE,
)
_SAVE_IMAGE_MARKER_RE = re.compile(
    r"/\s*SAVIMG\s*[:：]\s*(.*?:\s*\d{1,3})\s*/\s*"
    r"|/\s*SAVIMG\s*[:：]\s*(.*?:\s*\d{1,3})\s*(?=\r?\n|$)"
    r"|/\s*SAVIMG\s*[:：]\s*(.*?)\s*/\s*"
    r"|/\s*SAVIMG\s*[:：]\s*(.*?)\s*(?=\r?\n|$)",
    re.IGNORECASE,
)
_LOOSE_IMAGE_MARKER_RE = re.compile(
    r"(?<![A-Za-z])\[\s*IMG\s*[:：][^\]\r\n]{1,60}[\]]"
    r"|(?<![A-Za-z])【\s*IMG\s*[:：][^】\r\n]{1,60}[】]"
    r"|(?<![A-Za-z])(?:\(\s*|（\s*)IMG\s*[:：][^\)\r\n]{1,60}[\)）]"
    r"|(?<![A-Za-z])(?:/／\s*)?IMG\s*[:：][^\]】)）／/\r\n]{1,60}\s*(?:[\]】)）／]|$)",
    re.IGNORECASE,
)
_LOOSE_SAVE_MARKER_RE = re.compile(
    r"(?<![A-Za-z])\[\s*SAVIMG\s*[:：][^\]\r\n]{1,300}[\]]"
    r"|(?<![A-Za-z])【\s*SAVIMG\s*[:：][^】\r\n]{1,300}[】]"
    r"|(?<![A-Za-z])(?:\(\s*|（\s*)SAVIMG\s*[:：][^\)\r\n]{1,300}[\)）]"
    r"|(?<![A-Za-z])(?:/／\s*)?SAVIMG\s*[:：][^\]】)）／/\r\n]{1,300}\s*(?:[\]】)）／]|$)",
    re.IGNORECASE,
)


from .config import log
from .media import ImageInputError, _image_mime, image_dimensions

def sanitize_image_name(name, fallback="image"):
    value = str(name or "").strip()
    value = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    value = re.sub(r"\.(png|jpe?g|gif|webp|bmp)$", "", value, flags=re.IGNORECASE)
    value = value[:40].strip()
    return value or fallback


def strip_incomplete_enter_markers(text):
    value = re.sub(r"[\[【(（]\s*Enter\s*[\]】)）]", " ", str(text or ""), flags=re.IGNORECASE)
    return re.sub(r"(?<![A-Za-z/／])Enter(?![A-Za-z/／])", " ", value, flags=re.IGNORECASE)


_PROFESSIONAL_PREFIX_RE = re.compile(r"^\s*/\s*专业模式\s*[:：]\s*", re.IGNORECASE)


def extract_professional_command(text):
    """返回专业模式内容；不是该指令时返回 None。"""
    value = str(text or "")
    match = _PROFESSIONAL_PREFIX_RE.match(value)
    if not match:
        return None
    return value[match.end():].strip()


def _shared_slash_suffix(text, match):
    """标记闭合斜杠同时作为下一个标记的起始斜杠时，保留该斜杠。"""
    raw = str(text or "")
    end = match.end()
    if end <= 0 or raw[end - 1:end] != "/":
        return " "
    rest = raw[end:]
    if re.match(r"(?:Enter|VOICE|IMG|SAVIMG)(?:\s*[:：/])", rest, re.IGNORECASE):
        return "/"
    return " "


def parse_image_markers(text):
    names = []

    def replace(match):
        name = sanitize_image_name(match.group(1).rstrip("]】"), "")
        if name and name not in names:
            names.append(name)
        return _shared_slash_suffix(text, match)

    cleaned = _IMAGE_MARKER_RE.sub(replace, text or "")
    cleaned = _LOOSE_IMAGE_MARKER_RE.sub(" ", cleaned)
    return cleaned, names


def parse_favorite(value, default=50):
    match = re.search(r"\d{1,3}", str(value or ""))
    if not match:
        return max(0, min(100, int(default)))
    return max(0, min(100, int(match.group())))


def parse_save_markers(text):
    markers = []
    seen = set()

    def replace(match):
        raw = next((group for group in match.groups() if group is not None), "").rstrip("]】")
        position = None
        first = re.split(r"[:：]", raw, maxsplit=1)[0].strip()
        position_match = re.match(
            r"^\s*(?:图片\s*)?(?:位于\s*)?第?\s*(\d+)\s*张?\s*$", first)
        if position_match:
            parts = re.split(r"[:：]", raw, maxsplit=3)
            if len(parts) < 4:
                return " "
            position = int(position_match.group(1))
            if position < 1:
                return " "
            name = sanitize_image_name(parts[1], "")
            description = parts[2].strip() if len(parts) > 2 else ""
            favorite_text = parts[3] if len(parts) > 3 else ""
        else:
            parts = re.split(r"[:：]", raw, maxsplit=2)
            name = sanitize_image_name(parts[0], "")
            description = parts[1].strip() if len(parts) > 1 else ""
            favorite_text = parts[2] if len(parts) > 2 else ""
        if not name:
            return " "
        description = re.sub(r"\s+", " ", description)[:200]
        favorite = parse_favorite(favorite_text, 50)
        key = (name, position)
        if key not in seen:
            seen.add(key)
            markers.append({
                "position": position,
                "name": name,
                "description": description,
                "favorite": favorite,
            })
        return _shared_slash_suffix(text, match)

    cleaned = _SAVE_IMAGE_MARKER_RE.sub(replace, text or "")
    cleaned = _LOOSE_SAVE_MARKER_RE.sub(" ", cleaned)
    return cleaned, markers


class ImageLibrary(object):
    """工作区 img/ 目录下的图片库：文件 + JSON 索引。"""

    def __init__(self, directory, max_items=1000):
        self.directory = os.path.abspath(directory)
        self.index_path = os.path.join(self.directory, "index.json")
        self.text_index_path = os.path.join(os.path.dirname(self.directory), "image_index.txt")
        self.max_items = max(0, int(max_items))
        self._lock = threading.RLock()
        os.makedirs(self.directory, exist_ok=True)
        self.entries = self._load()
        self._index_mtime = self._get_index_mtime()
        self._prune_lowest()
        self._write_text_index()

    def _get_index_mtime(self):
        try:
            return os.path.getmtime(self.index_path)
        except OSError:
            return 0.0

    def reload(self):
        with self._lock:
            self.entries = self._load()
            self._index_mtime = self._get_index_mtime()
            self._write_text_index()
            return len(self.entries)

    def _reload_if_changed(self):
        mtime = self._get_index_mtime()
        with self._lock:
            if mtime <= self._index_mtime:
                return
            self.entries = self._load()
            self._index_mtime = mtime
            self._write_text_index()
            log.info("[图片库] 检测到索引变化，已重新加载 %d 张", len(self.entries))

    def _load(self):
        try:
            with open(self.index_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                entries = [item for item in data if isinstance(item, dict)]
                for entry in entries:
                    entry["favorite"] = parse_favorite(entry.get("favorite"), 50)
                return entries
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("读取图片库索引失败：%s", exc)
        return []

    def _write(self):
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.entries, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.index_path)
        self._index_mtime = self._get_index_mtime()

    def text_index(self):
        with self._lock:
            entries = list(self.entries)
        lines = []
        for entry in entries:
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            description = str(entry.get("description") or "").replace("\r", " ").replace("\n", " ").strip()
            favorite = parse_favorite(entry.get("favorite"), 50)
            if description:
                lines.append("%s：%s（喜爱 %d）" % (name, description, favorite))
            else:
                lines.append("%s（喜爱 %d）" % (name, favorite))
        return "\n".join(lines)

    def _write_text_index(self):
        try:
            tmp = self.text_index_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self.text_index())
                if self.entries:
                    fh.write("\n")
            os.replace(tmp, self.text_index_path)
        except OSError as exc:
            log.warning("写入 image_index.txt 失败：%s", exc)

    def _prune_lowest(self, protect=None):
        if self.max_items <= 0 or len(self.entries) <= self.max_items:
            return []
        candidates = [entry for entry in self.entries if entry is not protect]
        candidates.sort(key=lambda item: (
            parse_favorite(item.get("favorite"), 50),
            str(item.get("created_at") or ""),
            str(item.get("name") or ""),
        ))
        remove_count = max(0, len(self.entries) - self.max_items)
        removed = []
        for entry in candidates[:remove_count]:
            try:
                os.remove(os.path.join(self.directory, str(entry.get("file") or "")))
            except OSError:
                pass
            self.entries.remove(entry)
            removed.append(entry.get("name"))
        if removed:
            self._write()
            self._write_text_index()
            log.info("[图片库] 超出上限，按喜爱程度删除：%s", "、".join(str(x) for x in removed))
        return removed

    def save_bytes(self, data, name, description="", source=None, favorite=50):
        if not data:
            raise ImageInputError("图片内容为空")
        self._reload_if_changed()
        favorite = parse_favorite(favorite, 50)
        mime = _image_mime(data)
        extension = IMAGE_EXTENSIONS.get(mime, ".jpg")
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            for entry in self.entries:
                if entry.get("sha256") == digest:
                    entry = dict(entry)
                    entry["duplicate"] = True
                    return entry

            base = sanitize_image_name(name, "image")
            base_filename = base + extension
            filename = base_filename
            path = os.path.join(self.directory, base_filename)
            if os.path.exists(path):
                # 走到这里说明 SHA256 不同，即并非完全相同的图片；一律另存后缀。
                new_size = image_dimensions(data)
                try:
                    with open(path, "rb") as fh:
                        old_size = image_dimensions(fh.read())
                except OSError:
                    old_size = None
                if new_size and old_size and new_size == old_size:
                    reason = "同名图片尺寸相同但内容不同"
                elif new_size and old_size:
                    reason = "同名图片尺寸不一致"
                else:
                    reason = "同名图片尺寸无法确认"
                counter = 2
                while os.path.exists(os.path.join(
                        self.directory, "%s_%d%s" % (base, counter, extension))):
                    counter += 1
                filename = "%s_%d%s" % (base, counter, extension)
                path = os.path.join(self.directory, filename)
                log.info("[图片库] %s，另存为 %s", reason, filename)
            # 同名文件可能已有索引项：覆盖或替换时只保留最新一条。
            self.entries = [item for item in self.entries if item.get("file") != filename]
            with open(path, "wb") as fh:
                fh.write(data)
            entry = {
                "name": base if filename == base_filename else os.path.splitext(filename)[0],
                "file": filename,
                "description": str(description or "").replace("\r", " ").replace("\n", " ")[:200],
                "mime": mime,
                "size": len(data),
                "sha256": digest,
                "favorite": favorite,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": source or {},
            }
            self.entries.append(entry)
            self._write()
            self._write_text_index()
            self._prune_lowest(protect=entry)
            result = dict(entry)
            result["duplicate"] = False
            return result

    def path_for(self, name):
        target = sanitize_image_name(name, "")
        if not target:
            return ""
        self._reload_if_changed()
        with self._lock:
            for entry in reversed(self.entries):
                entry_name = str(entry.get("name") or "")
                file_name = str(entry.get("file") or "")
                if target.casefold() in (entry_name.casefold(), os.path.splitext(file_name)[0].casefold()):
                    path = os.path.join(self.directory, file_name)
                    if os.path.isfile(path):
                        return path
        return ""

    def count(self):
        self._reload_if_changed()
        return len(self.entries)

    def name_catalog_text(self, limit=0):
        """只返回 [图片名:喜爱度]，用于人设总结，不包含文件名和描述。"""
        self._reload_if_changed()
        with self._lock:
            entries = list(self.entries)
        if limit and limit > 0:
            entries = entries[-limit:]
        lines = []
        for entry in entries:
            filename = str(entry.get("file") or "").strip()
            path = os.path.join(self.directory, filename)
            if not filename or not os.path.isfile(path):
                continue
            name = str(entry.get("name") or "").strip() or os.path.splitext(filename)[0]
            favorite = parse_favorite(entry.get("favorite"), 50)
            lines.append("[%s:%d]" % (name, favorite))
        return "".join(lines)

    def catalog_text(self, limit=20):
        if limit <= 0:
            return ""
        self._reload_if_changed()
        with self._lock:
            entries = list(self.entries)[-limit:][::-1]
        lines = []
        for entry in entries:
            filename = str(entry.get("file") or "").strip()
            path = os.path.join(self.directory, filename)
            if not filename or not os.path.isfile(path):
                continue
            favorite = parse_favorite(entry.get("favorite"), 50)
            lines.append("[%s:%d]" % (filename, favorite))
        return "".join(lines)

__all__ = [name for name in globals() if not name.startswith("__")]
