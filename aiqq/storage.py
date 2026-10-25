#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ storage：群上下文、用户上下文与专业模式上下文存储。"""
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

class GroupMemory(object):
    """进程内的群消息环形缓冲，历史接口失败时作为上下文兜底。"""

    def __init__(self, maxlen=60):
        self.maxlen = maxlen
        self._lock = threading.Lock()
        self._data = defaultdict(lambda: deque(maxlen=maxlen))

    def add(self, group_id, entry):
        with self._lock:
            self._data[group_id].append(entry)

    def recent(self, group_id, limit):
        with self._lock:
            items = list(self._data[group_id])
        return items[-limit:] if limit > 0 else []

    def snapshot(self, group_id):
        with self._lock:
            return [dict(item) for item in self._data[group_id]]

    def replace(self, group_id, entries):
        items = [item for item in (entries or []) if isinstance(item, dict)]
        with self._lock:
            self._data[group_id] = deque(items[-self.maxlen:], maxlen=self.maxlen)

    def groups(self):
        with self._lock:
            return [group_id for group_id, items in self._data.items() if items]


class ContextStore(object):
    """把群上下文压缩保存为 context/<群号>_<时间>.json.gz，并只保留最近 N 份。"""

    VERSION = 1

    def __init__(self, directory, maxlen=60, keep_files=5, max_lines=10000):
        self.directory = os.path.abspath(directory)
        self.maxlen = max(1, int(maxlen))
        self.keep_files = max(1, int(keep_files))
        self.max_lines = max(1, int(max_lines))
        os.makedirs(self.directory, exist_ok=True)

    def _legacy_path(self, group_id):
        return os.path.join(self.directory, "%s.json.gz" % int(group_id))

    def _new_path(self, group_id):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return os.path.join(self.directory, "%s_%s.json.gz" % (int(group_id), stamp))

    def _files_for_group(self, group_id):
        group_id = int(group_id)
        legacy = "%s.json.gz" % group_id
        prefix = "%s_" % group_id
        try:
            names = os.listdir(self.directory)
        except OSError:
            return []
        return [
            os.path.join(self.directory, name)
            for name in names
            if name == legacy or (name.startswith(prefix) and name.endswith(".json.gz"))
        ]

    def latest_path(self, group_id):
        files = self._files_for_group(group_id)
        if not files:
            return ""
        return max(files, key=lambda path: (os.path.getmtime(path), os.path.basename(path)))

    def path_for(self, group_id):
        return self.latest_path(group_id) or self._legacy_path(group_id)

    def _prune(self, group_id):
        files = self._files_for_group(group_id)
        files.sort(key=lambda path: (os.path.getmtime(path), os.path.basename(path)), reverse=True)
        for old in files[self.keep_files:]:
            try:
                os.remove(old)
                log.info("[上下文] 删除较早记录：%s", os.path.basename(old))
            except OSError as exc:
                log.warning("[上下文] 删除旧记录 %s 失败：%s", old, exc)
        self._dedupe_identical(group_id)

    @staticmethod
    def _snapshot_hash(items):
        raw = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _dedupe_identical(self, group_id):
        files = self._files_for_group(group_id)
        files.sort(key=lambda path: (os.path.getmtime(path), os.path.basename(path)), reverse=True)
        seen = set()
        for path in files:
            items = self._read_file(path)
            digest = self._snapshot_hash(items)
            if digest in seen:
                try:
                    os.remove(path)
                    log.info("[上下文] 删除重复快照：%s", os.path.basename(path))
                except OSError:
                    pass
            else:
                seen.add(digest)

    def clear(self, group_id):
        for path in self._files_for_group(group_id):
            try:
                os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _clean_entries(items):
        return [
            item for item in (items or [])
            if isinstance(item, dict)
            and not (item.get("is_self") and str(item.get("text") or "").strip() == "[发送了图片]")
        ]

    @staticmethod
    def _entry_key(item):
        message_id = str(item.get("message_id") or "")
        if message_id:
            return ("id", message_id)
        return ("value", item.get("user_id"), item.get("time"),
                item.get("text"), item.get("is_self"))

    @classmethod
    def _collapse_consecutive(cls, entries):
        collapsed = []
        last_key = None
        for item in entries:
            key = cls._entry_key(item)
            if key == last_key:
                continue
            collapsed.append(item)
            last_key = key
        return collapsed

    @classmethod
    def merge_entries(cls, older, newer):
        """拼接两份上下文；只忽略连续重复和尾部/头部重叠。"""
        base = cls._collapse_consecutive([item for item in (older or []) if isinstance(item, dict)])
        incoming = cls._collapse_consecutive([item for item in (newer or []) if isinstance(item, dict)])
        if not base:
            return incoming
        if not incoming:
            return base
        overlap = 0
        limit = min(len(base), len(incoming))
        for size in range(limit, 0, -1):
            if [cls._entry_key(item) for item in base[-size:]] == \
                    [cls._entry_key(item) for item in incoming[:size]]:
                overlap = size
                break
        return cls._collapse_consecutive(base + incoming[overlap:])

    @staticmethod
    def _line_count(entries):
        total = 0
        for item in entries:
            text = str(item.get("text") or "")
            total += max(1, text.count("\n") + 1)
            total += len(item.get("images") or [])
            total += len(item.get("forwards") or [])
        return total

    @classmethod
    def compress_entries(cls, entries):
        """把相邻同发送者、间隔不超过 5 分钟的消息合并，减少上下文行数。"""
        merged = []
        for item in entries:
            item = dict(item)
            if merged:
                left = merged[-1]
                same_sender = _as_int(left.get("user_id"), 0) == _as_int(item.get("user_id"), 0)
                same_role = bool(left.get("is_self")) == bool(item.get("is_self"))
                left_time = _as_int(left.get("time"), 0)
                right_time = _as_int(item.get("time"), 0)
                close_enough = not (left_time and right_time) or abs(right_time - left_time) <= 300
                if same_sender and same_role and close_enough:
                    left_text = str(left.get("text") or "").strip()
                    right_text = str(item.get("text") or "").strip()
                    left["text"] = (left_text + " " + right_text).strip()
                    left["time"] = right_time or left_time
                    left["mentioned"] = bool(left.get("mentioned")) or bool(item.get("mentioned"))
                    left["followup"] = bool(left.get("followup")) or bool(item.get("followup"))
                    left["restored"] = bool(left.get("restored")) or bool(item.get("restored"))
                    if not left.get("reply_to") and item.get("reply_to"):
                        left["reply_to"] = item.get("reply_to")
                    for key in ("images", "forwards"):
                        combined = list(left.get(key) or [])
                        seen = {(entry.get("url"), entry.get("file")) for entry in combined
                                if isinstance(entry, dict)}
                        for extra in item.get(key) or []:
                            marker = (extra.get("url"), extra.get("file")) if isinstance(extra, dict) else extra
                            if marker not in seen:
                                seen.add(marker)
                                combined.append(extra)
                        left[key] = combined
                    continue
            merged.append(item)
        return cls._collapse_consecutive(merged)

    def fit_entries(self, entries):
        """超过 max_lines 时先压缩；仍超限则从最早记录开始移除。"""
        items = self._collapse_consecutive([item for item in (entries or []) if isinstance(item, dict)])
        self.last_fit_action = ""
        if self._line_count(items) <= self.max_lines:
            return items
        compressed = self.compress_entries(items)
        if self._line_count(compressed) < self._line_count(items):
            items = compressed
            self.last_fit_action = "compressed"
        if self._line_count(items) <= self.max_lines:
            return items
        total = self._line_count(items)
        start = 0
        while start < len(items) and total > self.max_lines:
            total -= self._line_count([items[start]])
            start += 1
        self.last_fit_action = "trimmed"
        return items[start:]

    def _read_file(self, path):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            log.warning("读取上下文 %s 失败：%s", path, exc)
            return []
        items = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return self._clean_entries([item for item in items if isinstance(item, dict)])

    def load(self, group_id):
        files = sorted(self._files_for_group(group_id),
                       key=lambda path: (os.path.getmtime(path), os.path.basename(path)))
        merged = []
        for path in files:
            merged = self.merge_entries(merged, self._read_file(path))
        return self.fit_entries(merged)

    def save(self, group_id, entries):
        current = self._clean_entries(entries)
        if not current:
            self.clear(group_id)
            return None
        items = self.fit_entries(self.merge_entries(self.load(group_id), current))
        if not items:
            self.clear(group_id)
            return None
        latest = self.latest_path(group_id)
        if latest and self._read_file(latest) == items:
            self.last_saved_count = len(items)
            self.last_fit_action = ""
            self._dedupe_identical(group_id)
            return latest
        payload = {
            "version": self.VERSION,
            "group_id": int(group_id),
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": items,
        }
        path = self._new_path(group_id)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        self.last_saved_count = len(items)
        self._prune(group_id)
        return path

    def load_into(self, memory):
        loaded = {}
        try:
            names = [name for name in os.listdir(self.directory) if name.endswith(".json.gz")]
        except OSError as exc:
            log.warning("读取上下文目录失败：%s", exc)
            return loaded
        groups = set()
        for name in names:
            stem = name[:-len(".json.gz")]
            try:
                groups.add(int(stem.split("_", 1)[0]))
            except ValueError:
                continue
        for group_id in groups:
            self._prune(group_id)
            # 启动时只加载每个群最新的一份上下文
            entries = self.load(group_id)
            if entries:
                entries = [dict(item, restored=True) for item in entries]
                memory.replace(group_id, entries)
                loaded[group_id] = len(entries)
        return loaded


# --------------------------------------------------------------------------- #
# LM Studio 客户端
# --------------------------------------------------------------------------- #
class UserContextStore(object):
    """按 群组/用户 保存触发消息与模型回复；同一用户跨群共享。"""

    VERSION = 1

    def __init__(self, directory, keep_files=5, max_entries=500):
        self.directory = os.path.abspath(directory)
        self.keep_files = max(1, int(keep_files))
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._entries = defaultdict(list)
        self._groups = defaultdict(set)
        self._loaded = False
        os.makedirs(self.directory, exist_ok=True)

    def _user_dir(self, group_id, user_id):
        return os.path.join(self.directory, str(int(group_id)), str(int(user_id)))

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            groups = os.listdir(self.directory)
        except OSError:
            return
        candidates = defaultdict(list)
        for group_name in groups:
            group_dir = os.path.join(self.directory, group_name)
            if not os.path.isdir(group_dir) or not group_name.isdigit():
                continue
            try:
                users = os.listdir(group_dir)
            except OSError:
                continue
            for user_name in users:
                user_dir = os.path.join(group_dir, user_name)
                if not os.path.isdir(user_dir) or not user_name.isdigit():
                    continue
                try:
                    names = [name for name in os.listdir(user_dir) if name.endswith(".json.gz")]
                except OSError:
                    continue
                for name in names:
                    path = os.path.join(user_dir, name)
                    try:
                        candidates[int(user_name)].append(
                            (os.path.getmtime(path), int(group_name), path))
                    except OSError:
                        continue
                if names:
                    self._groups[int(user_name)].add(int(group_name))
        for user_id, files in candidates.items():
            files.sort(reverse=True)
            for _, _, path in files:
                data = self._read(path)
                if data:
                    self._entries[user_id] = data
                    break

    @staticmethod
    def _read(path):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data.get("entries") if isinstance(data, dict) else None
            return [dict(item) for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []
        except (OSError, ValueError, EOFError):
            return []

    def _fit(self, entries):
        entries = list(entries)
        while len(entries) > self.max_entries:
            index = next((i for i, item in enumerate(entries)
                          if item.get("role") == "assistant"), None)
            if index is None:
                break
            entries.pop(index)
        if len(entries) > self.max_entries:
            merged = []
            for item in entries:
                if merged and merged[-1].get("role") == item.get("role"):
                    merged[-1]["content"] = (
                        str(merged[-1].get("content") or "") + "\n" +
                        str(item.get("content") or ""))
                    continue
                merged.append(dict(item))
            entries = merged
        if len(entries) > self.max_entries:
            entries = entries[-self.max_entries:]
        return entries

    def append_exchange(self, user_id, group_id, user_text, bot_text,
                        user_name="", mentions=None):
        user_id = int(user_id or 0)
        group_id = int(group_id or 0)
        if user_id <= 0 or group_id <= 0:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._load()
            self._groups[user_id].add(group_id)
            entries = self._entries[user_id]
            entries.append({
                "role": "user",
                "content": str(user_text or "[触发]"),
                "time": stamp,
                "group_id": group_id,
                "name": str(user_name or ""),
                "mentions": [
                    {"user_id": str(item.get("user_id") or ""),
                     "name": str(item.get("name") or "")}
                    for item in (mentions or []) if isinstance(item, dict)
                ],
            })
            entries.append({
                "role": "assistant",
                "content": str(bot_text or ""),
                "time": stamp,
                "group_id": group_id,
            })
            entries = self._fit(entries)
            self._entries[user_id] = entries
            self._save_user(user_id, entries)

    def _save_user(self, user_id, entries):
        payload = json.dumps({
            "version": self.VERSION,
            "user_id": user_id,
            "groups": sorted(self._groups[user_id]),
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entries": entries,
        }, ensure_ascii=False, indent=2).encode("utf-8")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        for group_id in self._groups[user_id]:
            directory = self._user_dir(group_id, user_id)
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, "%s.json.gz" % stamp)
            try:
                with gzip.open(path, "wb") as fh:
                    fh.write(payload)
            except OSError as exc:
                log.warning("[用户上下文] 保存 %s/%s 失败：%s", group_id, user_id, exc)
                continue
            try:
                names = sorted(
                    name for name in os.listdir(directory) if name.endswith(".json.gz"))
                for name in names[:-self.keep_files]:
                    os.remove(os.path.join(directory, name))
            except OSError:
                pass

    def messages(self, user_id):
        with self._lock:
            self._load()
            return [dict(item) for item in self._entries.get(int(user_id or 0), [])]

    def save_all(self):
        with self._lock:
            for user_id, entries in self._entries.items():
                if entries:
                    self._save_user(user_id, entries)

    def count(self):
        with self._lock:
            self._load()
            return len([items for items in self._entries.values() if items])

    def clear_all(self):
        with self._lock:
            self._load()
            removed = 0
            for root, dirs, files in os.walk(self.directory, topdown=False):
                for name in files:
                    if not name.endswith(".json.gz"):
                        continue
                    try:
                        os.remove(os.path.join(root, name))
                        removed += 1
                    except OSError:
                        pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except OSError:
                        pass
            self._entries.clear()
            self._groups.clear()
            self._loaded = True
            return removed

    def clear_user(self, user_id):
        """删除某个用户在所有群目录下的共享上下文。"""
        user_id = int(user_id or 0)
        with self._lock:
            self._load()
            removed = 0
            for group_id in list(self._groups.get(user_id, set())):
                directory = self._user_dir(group_id, user_id)
                for root, dirs, files in os.walk(directory, topdown=False):
                    for name in files:
                        if not name.endswith(".json.gz"):
                            continue
                        try:
                            os.remove(os.path.join(root, name))
                            removed += 1
                        except OSError:
                            pass
                    for name in dirs:
                        try:
                            os.rmdir(os.path.join(root, name))
                        except OSError:
                            pass
                try:
                    os.rmdir(directory)
                except OSError:
                    pass
                group_dir = os.path.join(self.directory, str(int(group_id)))
                try:
                    os.rmdir(group_dir)
                except OSError:
                    pass
            self._entries.pop(user_id, None)
            self._groups.pop(user_id, None)
            return removed

    def clear_group(self, group_id):
        """删除某个群目录下的所有用户上下文快照。"""
        group_id = int(group_id or 0)
        with self._lock:
            self._load()
            directory = os.path.join(self.directory, str(group_id))
            if not os.path.isdir(directory):
                return 0
            removed = 0
            for root, dirs, files in os.walk(directory, topdown=False):
                for name in files:
                    if not name.endswith(".json.gz"):
                        continue
                    try:
                        os.remove(os.path.join(root, name))
                        removed += 1
                    except OSError:
                        pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except OSError:
                        pass
            try:
                os.rmdir(directory)
            except OSError:
                pass
            for user_id in list(self._groups):
                if group_id in self._groups[user_id]:
                    self._groups[user_id].discard(group_id)
                    if not self._groups[user_id]:
                        self._groups.pop(user_id, None)
                        self._entries.pop(user_id, None)
            return removed

__all__ = [name for name in globals() if not name.startswith("__")]
