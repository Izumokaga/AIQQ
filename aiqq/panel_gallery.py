#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_gallery：图库浏览、更新与删除。"""
import base64
import gzip
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from . import panel_state
import setup as init_setup  # noqa: E402



from .panel_common import PanelError

class PanelGalleryMixin:
    def _raw_images(self):
        path = os.path.join(self.root, "img", "index.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _favorite(self, value, default=50):
        match = re.search(r"\d{1,3}", str(value or ""))
        return max(0, min(100, int(match.group()))) if match else default

    def _write_images(self, entries):
        directory = os.path.join(self.root, "img")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "index.json")
        tmp = path + ".panel.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

        lines = []
        for item in entries:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            description = str(item.get("description") or "").replace("\r", " ").replace("\n", " ").strip()
            favorite = self._favorite(item.get("favorite"), 50)
            if description:
                lines.append("%s：%s（喜爱 %d）" % (name, description, favorite))
            else:
                lines.append("%s（喜爱 %d）" % (name, favorite))
        text_path = os.path.join(self.root, "image_index.txt")
        text_tmp = text_path + ".panel.tmp"
        with open(text_tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines))
            if lines:
                fh.write("\n")
        os.replace(text_tmp, text_path)

    def _images(self):
        items = []
        for item in self._raw_images():
            filename = str(item.get("file") or "")
            path = os.path.join(self.root, "img", os.path.basename(filename))
            size = 0
            try:
                size = os.path.getsize(path)
            except OSError:
                pass
            items.append({
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "favorite": self._favorite(item.get("favorite"), 50),
                "file": filename,
                "size": size,
                "created_at": item.get("created_at", ""),
            })
        return items

    def _image_index(self, entries, name):
        target = str(name or "").strip().casefold()
        if not target:
            return -1
        for index, item in enumerate(entries):
            item_name = str(item.get("name") or "").strip().casefold()
            item_file = os.path.basename(str(item.get("file") or "")).casefold()
            if target in (item_name, item_file, os.path.splitext(item_file)[0]):
                return index
        return -1

    def update_image(self, name, new_name=None, description=None, favorite=None):
        entries = self._raw_images()
        index = self._image_index(entries, name)
        if index < 0:
            raise PanelError("找不到图片：%s" % name)
        item = entries[index]
        if new_name is not None:
            cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', "", str(new_name)).strip()[:60]
            if not cleaned:
                raise PanelError("图片名不能为空")
            for other in range(len(entries)):
                if other != index and str(entries[other].get("name") or "").strip().casefold() == cleaned.casefold():
                    raise PanelError("图片名已存在：%s" % cleaned)
            old_file = os.path.basename(str(item.get("file") or ""))
            if old_file and cleaned.casefold() != str(item.get("name") or "").strip().casefold():
                extension = os.path.splitext(old_file)[1]
                new_file = cleaned + extension
                old_path = os.path.join(self.root, "img", old_file)
                new_path = os.path.join(self.root, "img", new_file)
                if os.path.normcase(old_path) != os.path.normcase(new_path):
                    if os.path.exists(new_path):
                        raise PanelError("目标图片文件已存在：%s" % new_file)
                    if os.path.isfile(old_path):
                        os.rename(old_path, new_path)
                    item["file"] = new_file
                else:
                    item["file"] = new_file
            item["name"] = cleaned
        if description is not None:
            item["description"] = re.sub(r"\s+", " ", str(description)).strip()[:500]
        if favorite is not None:
            item["favorite"] = self._favorite(favorite, item.get("favorite", 50))
        entries[index] = item
        self._write_images(entries)
        return {"ok": True, "name": item.get("name"), "description": item.get("description"),
                "favorite": self._favorite(item.get("favorite"), 50)}

    def delete_image(self, name):
        entries = self._raw_images()
        index = self._image_index(entries, name)
        if index < 0:
            raise PanelError("找不到图片：%s" % name)
        item = entries.pop(index)
        filename = os.path.basename(str(item.get("file") or ""))
        if filename:
            try:
                os.remove(os.path.join(self.root, "img", filename))
            except OSError:
                pass
        self._write_images(entries)
        return {"ok": True, "name": item.get("name"), "deleted": filename}

    def image_info(self, name):
        target = str(name or "").strip()
        if not target:
            return None
        for item in reversed(self._images()):
            filename = str(item.get("file") or "")
            stem = os.path.splitext(filename)[0]
            if target.casefold() not in (
                    str(item.get("name") or "").casefold(),
                    filename.casefold(), stem.casefold()):
                continue
            path = os.path.join(self.root, "img", os.path.basename(filename))
            if not os.path.isfile(path):
                return None
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            return {"path": path, "mime": mime, "item": item}
        return None


__all__ = [name for name in globals() if not name.startswith("__")]
