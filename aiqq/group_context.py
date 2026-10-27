# -*- coding: utf-8 -*-
"""AIQQ 群级上下文与群级人设存储。

每个群在 context/<群号>/ 下只有一个当前上下文文件 context.json，
以及一份或多份 <预设名>.persona.txt。上下文达到阈值后由
bot_context_maintenance 模块整理并覆盖人设文件。
"""

import json
import os
import re
import threading
import time
from datetime import datetime

from .config import _as_int, log


def _safe_preset(name):
    value = str(name or "").strip()
    value = re.sub(r'[\\/:*?"<>|\r\n]+', "", value)[:80].strip()
    return value or "persona"


class GroupContextStore:
    VERSION = 2

    def __init__(self, root, max_entries=500, max_chars=5000):
        self.root = os.path.abspath(root)
        self.max_entries = max(1, int(max_entries))
        self.max_chars = max(500, int(max_chars))
        self._lock = threading.RLock()
        os.makedirs(self.root, exist_ok=True)

    def group_dir(self, group_id):
        return os.path.join(self.root, str(int(group_id)))

    def context_path(self, group_id):
        return os.path.join(self.group_dir(group_id), "context.json")

    def _read(self, group_id):
        path = self.context_path(group_id)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        entries = data.get("entries") if isinstance(data, dict) else None
        return [dict(item) for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []

    def _write(self, group_id, entries):
        directory = self.group_dir(group_id)
        os.makedirs(directory, exist_ok=True)
        path = self.context_path(group_id)
        payload = {
            "version": self.VERSION,
            "group_id": int(group_id),
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entries": entries,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path

    @staticmethod
    def _clean(entries):
        return [dict(item) for item in (entries or []) if isinstance(item, dict)]

    def _fit(self, entries):
        entries = self._clean(entries)
        if len(entries) > self.max_entries:
            entries = entries[-self.max_entries:]
        return entries

    @staticmethod
    def char_count(entries):
        total = 0
        for item in entries or []:
            total += len(str(item.get("content") or ""))
        return total

    def append_exchange(self, group_id, user_text, bot_text, user_name="", mentions=None):
        group_id = int(group_id or 0)
        if group_id <= 0:
            return 0
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            entries = self._read(group_id)
            entries.append({
                "role": "user",
                "content": str(user_text or "[触发]"),
                "time": stamp,
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
            })
            entries = self._fit(entries)
            self._write(group_id, entries)
            count = self.char_count(entries)
        log.debug("[群上下文] 群 %s 当前 %d 字", group_id, count)
        return count

    def replace(self, group_id, entries):
        group_id = int(group_id or 0)
        if group_id <= 0:
            return []
        with self._lock:
            items = self._fit(entries)
            if items:
                self._write(group_id, items)
            else:
                self.clear(group_id)
            return items

    def messages(self, group_id):
        with self._lock:
            return self._read(group_id)

    def prompt_text(self, group_id):
        lines = []
        for item in self.messages(group_id):
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if item.get("role") == "assistant":
                lines.append("机器人：%s" % content)
            else:
                name = str(item.get("name") or "群友")
                lines.append("%s：%s" % (name, content))
        return "\n".join(lines)

    def needs_consolidation(self, group_id):
        return self.char_count(self.messages(group_id)) > self.max_chars

    def clear(self, group_id):
        path = self.context_path(group_id)
        try:
            os.remove(path)
            return 1
        except OSError:
            return 0

    def clear_all(self):
        removed = 0
        for name in os.listdir(self.root):
            group_dir = os.path.join(self.root, name)
            if not os.path.isdir(group_dir) or not name.isdigit():
                continue
            path = os.path.join(group_dir, "context.json")
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        return removed

    def count(self):
        total = 0
        for name in os.listdir(self.root):
            if name.isdigit() and os.path.isfile(os.path.join(self.root, name, "context.json")):
                total += 1
        return total


class GroupPersonaStore:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def group_dir(self, group_id):
        return os.path.join(self.root, str(int(group_id)))

    def active_preset_path(self):
        return os.path.join(self.root, "active_preset.txt")

    def active_preset(self):
        try:
            with open(self.active_preset_path(), encoding="utf-8-sig") as fh:
                return _safe_preset(fh.read().strip())
        except OSError:
            return ""

    def set_active_preset(self, name):
        value = _safe_preset(name)
        with open(self.active_preset_path(), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(value + "\n")
        return value

    def path(self, group_id, preset=None):
        name = _safe_preset(preset or self.active_preset())
        return os.path.join(self.group_dir(group_id), name + ".persona.txt")

    def read(self, group_id, preset=None):
        path = self.path(group_id, preset)
        try:
            with open(path, encoding="utf-8-sig") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def ensure(self, group_id, preset, content):
        path = self.path(group_id, preset)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path):
            return False
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(str(content or "").rstrip() + "\n")
        return True

    def ensure_groups(self, groups, preset, content):
        created = []
        for group_id in groups or []:
            try:
                if self.ensure(int(group_id), preset, content):
                    created.append(int(group_id))
            except (TypeError, ValueError):
                continue
        return created

    def write(self, group_id, content, preset=None):
        path = self.path(group_id, preset)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(str(content or "").rstrip() + "\n")
        os.replace(tmp, path)
        return path


__all__ = ["GroupContextStore", "GroupPersonaStore", "_safe_preset"]
