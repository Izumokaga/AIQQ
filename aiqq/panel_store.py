#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_store：PanelStore 初始化与各领域 mixin 的组合入口。"""
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


from .panel_common import MAX_BODY, PanelError, init_setup, BASE_DIR
from .panel_config import PanelConfigMixin
from .panel_presets import PanelPresetsMixin
from .panel_process import PanelProcessMixin
from .panel_bridge import PanelBridgeMixin
from .panel_napcat import PanelNapcatMixin
from .panel_commands import PanelCommandsMixin
from .panel_contexts import PanelContextsMixin
from .panel_gallery import PanelGalleryMixin

class PanelStore(PanelConfigMixin, PanelPresetsMixin, PanelProcessMixin, PanelBridgeMixin, PanelNapcatMixin, PanelCommandsMixin, PanelContextsMixin, PanelGalleryMixin):
    EDITABLE = ("persona.txt", "init.txt")
    READONLY = ("image_index.txt",)
    ALLOWED = EDITABLE + READONLY

    STANDARD_COMMANDS = {
        "帮助", "图库", "上下文", "上下文.保存", "上下文.删除本用户上下文",
        "上下文.删除本用户专业模式上下文",
        "监控", "重启.模型", "睡眠", "预备", "唤醒", "人设预设",
    }
    ADMIN_ONLY_COMMANDS = {
        "上下文.删除本群上下文", "上下文.删除所有上下文", "重启.机器人",
    }

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.backup_dir = os.path.join(self.root, "backups", "panel")
        self.settings_path = os.path.join(self.root, "panel_settings.json")
        self.password_path = os.path.join(self.root, "panel_password.txt")
        os.makedirs(self.backup_dir, exist_ok=True)

__all__ = [name for name in globals() if not name.startswith("__")]
