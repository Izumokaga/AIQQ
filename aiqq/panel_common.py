#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_common：面板公共常量、异常与 setup 模块引用。"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import setup as init_setup  # noqa: E402

MAX_BODY = 2 * 1024 * 1024


class PanelError(Exception):
    pass


__all__ = [name for name in globals() if not name.startswith("__")]
