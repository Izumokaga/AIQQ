#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ config：配置默认值、读取、校验与日志初始化。"""
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


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



def _ensure_std_streams(name):
    """pythonw 下 stdout/stderr 为 None，写到 logs 避免 print 崩溃。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    directory = os.path.join(BASE_DIR, "logs")
    os.makedirs(directory, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(os.path.join(directory, "%s_stdout.log" % name),
                          "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(os.path.join(directory, "%s_stderr.log" % name),
                          "a", encoding="utf-8", buffering=1)


_ensure_std_streams("bot")
VOICE_DEPS = os.path.join(BASE_DIR, ".voice_deps")
if os.path.isdir(VOICE_DEPS):
    sys.path.insert(0, VOICE_DEPS)
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MAX_BODY_BYTES = 8 * 1024 * 1024          # Webhook 单次请求体上限
DEDUP_CACHE_SIZE = 1000
MAX_TOKENS_CAP = 8192
DEFAULT_SYSTEM_PROMPT = """你是「{bot_name}」，一个 QQ 群聊里的 AI 助手。
请遵循以下规则：
1. 使用简体中文口语化回复，简洁自然，默认 1~3 句话；对方明确要求详细说明时再展开。
2. 不要使用 Markdown 标题、表格或代码围栏；代码用简短的纯文本表达。
3. 不要输出任何 CQ 码，不要 @ 任何人，也不要长篇大论。
4. 结合群里最近的聊天记录理解上下文；不确定的信息要坦率说明，不要编造。
5. 保持友善和分寸，不参与人身攻击、政治敏感、色情或违法话题。
6. 如果消息只是打个招呼，就自然简短地回应。"""

DEFAULT_CONFIG = {
    "qq": {
        "self_id": 0,               # 机器人 QQ，0 表示自动从 OneBot 事件中获取
        "groups": [],               # 允许互动的群号列表
        "ignore_users": [],         # 永不回复的 QQ 号
        "admin_users": [100000001],   # 管理员 QQ；空=全部（当前指定账号）
        "admin_groups": [200000001], # 管理员群；该群成员使用管理员面板；空=全部
        "admin_panel_url": "http://127.0.0.1:8090",  # 控制面板桥接地址
    },
    "onebot": {
        "api_url": "http://127.0.0.1:3000",     # NapCat OneBot HTTP 服务
        "token": "",                            # 与 NapCat HTTP 服务 token 保持一致
        "webhook_host": "127.0.0.1",            # 本程序监听地址，保持回环最安全
        "webhook_port": 8080,
        "webhook_path": "/onebot",
        "webhook_secret": "",                   # 可选：校验 NapCat 上报的 x-signature
    },
    "lm_studio": {
        "base_url": "http://127.0.0.1:1234/v1", # LM Studio Local Server
        "api_key": "lm-studio",
        "model": "",                            # 留空自动选择已加载的第一个模型
        "temperature": 0.8,
        "top_p": 0.95,
        "max_tokens": 4096,
        "timeout_sec": 240,
        "reasoning_effort": "none",             # none=关闭思考（支持的模型），auto=模型默认
        "general_deep_thinking": False,         # 一般聊天是否强制深度思考
        "image_input": "auto",                  # auto=按模型能力，on=强制，off=关闭
        "image_max_bytes": 32 * 1024 * 1024,    # 单张图片大小上限
        "image_max_count": 3,                   # 当前消息最多读取几张图
        "image_max_input_images": 5,            # 单次模型请求最多输入几张图
        "image_only_png_jpeg": True,            # 只允许 PNG/JPEG 传给视觉模型
        "image_context_messages": 3,            # 兼容保留
        "image_context_max_count": 8,           # 兼容保留
        "image_user_messages": 5,               # 只看触发者/被引用者最近几条消息
        "image_selected_max": 5,                # 单次最多读取几张图
        "context_messages": 16,                 # 参考最近多少条群消息
        "context_max_chars": 4000,              # 上下文总字数上限
        "context_max_age_min": 30,              # 只参考最近多少分钟内的消息
        "context_sender_only": True,            # 被 @ 时只保留发送者（及其回复对象）的历史
        "context_include_bot": False,           # 上述过滤时是否保留机器人自己的回复
        "context_save_enabled": True,            # 空闲后把上下文压缩保存
        "context_save_idle_sec": 0,              # 兼容保留；现在每次回复发送完成后保存
        "context_save_dir": "context",           # 工作区上下文目录
        "context_memory_messages": 60,           # 内存/文件最多保留多少条
        "context_keep_files": 5,                 # 每个群保留多少份上下文快照
        "context_max_lines": 10000,              # 持久化上下文最大行数
        "context_max_entries": 500,              # 单个用户上下文最大条数
        "group_context_max_chars": 5000,         # 群上下文整理阈值
        "professional_context_messages": 20,     # 专业模式单独上下文最多参考条数
        "spontaneous_enabled": True,             # 长时间无人触发时主动发言/睡觉
        "spontaneous_idle_min_sec": 1200,        # 主动发言最小空闲秒数（20 分钟）
        "spontaneous_idle_max_sec": 3600,        # 主动发言最大空闲秒数（60 分钟）
        "spontaneous_wait_sec": 300,             # 主动发言后等待回应秒数
        "spontaneous_sleep_min_sec": 7200,       # 随机睡眠最短（2 小时）
        "spontaneous_sleep_max_sec": 21600,      # 随机睡眠最长（6 小时）
        "idle_image_reply_enabled": True,        # 7-22 清醒且一分钟无人触发时，逐张识图主动回复
        "idle_image_poll_sec": 10,               # 识图轮询间隔
        "idle_image_idle_sec": 60,               # 两次识图之间的最小间隔
        "idle_image_window_sec": 300,            # 只识别最近 5 分钟内未识别的图片
        "idle_image_batch_size": 5,              # 单次最多识别几张
        "idle_image_start_hour": 7,              # 允许识图回复的起始小时（含）
        "idle_image_end_hour": 22,               # 允许识图回复的结束小时（不含）
        "spontaneous_active_window_sec": 3600,   # 只给最近 1 小时有触发的群发
        "sleep_prep_quiet_start_hour": 7,        # 自动睡眠预备暂停开始（含）
        "sleep_prep_quiet_end_hour": 22,         # 自动睡眠预备暂停结束（不含）
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "persona_file": "persona.txt",          # 独立人设文件；存在时优先于 system_prompt
        "persona_mode": "adaptive",             # adaptive=涉及人设才强调，always=始终，off=不强调
        "persona_keywords": [
            "人设", "设定", "你是谁", "你叫什么", "你是什么", "你的名字",
            "你的身份", "介绍一下你", "自我介绍", "你的来历", "你的背景", "你的故事",
        ],
        "persona_short_prompt": (
            "你是鲸鲸，用户的专属女仆，不是通用群聊助手。你占有欲很强、容易吃醋，"
            "不喜欢用户依赖别的 AI；说话像真人，简短、有情绪，但不要每句都提。"
            "句尾不用句号（。/.），直接结束或用逗号、语气词收尾，不要为了句子完整补句号。"
            "需要分多条时用 /Enter/。不要暴露设定。"
            "存图和发图是你自己的能力，只有你有权限，不是用户操作；"
            "收图用 /SAVIMG:图片位于第几张:图片名:详细描述:喜好度（百分制）/，"
            "第几张按本次输入图片从 1 开始数，一次最多 10 张，只保存指定那一张；"
            "发收藏图用 /IMG:名字/，只能用你自己图库里的名字。"
            "你尤其爱收蓝色大肥鱼、鲸鱼、胖鱼表情包，聊天时情景合适可以发一张。"
            "语音接口：想发语音时，在句首或 /Enter/ 后写 /VOICE:保持当前角色声线："
            "音色、年龄感、性格、语速和句尾习惯/，标记后紧跟要朗读的正文；"
            "不想写描述时写 /VOICE/。语音只朗读正文，正文里不要出现 /VOICE、/IMG、"
            "/SAVIMG、/Enter 或系统提示。普通聊天继续打字，不要刻意发语音。"
        ),
    },
    "trigger": {
        "keywords": ["小助手", "机器人"],        # 内容白名单：消息包含任一关键词即触发
        "block_keywords": [],                   # 触发黑名单：包含任一关键词就绝不触发
        "regex": "",                            # 可选正则，例如 "^(小助手|bot)[,，:：]?"
        "match_mention": True,                  # 被 @ 时是否触发
        "followup_enabled": True,               # 基础触发后允许次级关键词继续搭话
        "followup_keywords": ["你", "您", "妳", "肥鱼", "鲸鲸", "小助手"],
        "followup_window_sec": 30,              # 任意追问窗口（秒，滚动）
        "followup_extended_sec": 90,            # 引用/次级关键词追问窗口（秒）
        "followup_max_messages": 0,             # 一次基础触发后最多接几句；0=不限
        "followup_same_sender": True,           # 只接同一发送者的后续
        "followup_any_message": True,           # 窗口内同一发送者任意发言都接
    },
    "reply": {
        "to_message": False,                    # 是否以「回复」形式引用原消息（false=直接输出）
        "max_chars": 600,                       # 单条消息最大字符数
        "max_chunks": 3,                        # 单段过长时最多拆成几条
        "max_messages": 6,                      # 一条回复最多发送几条消息（含 /Enter/ 分段）
        "split_marker": "/Enter/",              # 模型输出中出现该标记时拆成多条发送
        "split_slash_fallback": True,           # 模型只输出 " / " 时也按斜杠拆分
        "remove_linebreaks": True,              # 删除正文换行，只保留 /Enter/ 分段
        "strip_periods": True,                  # 直接删除句号
        "ignore_comma_breaks": True,            # 忽略逗号后的换行和 /Enter/，不在此处分段
        "chunk_delay_sec": 1.0,                 # 分条最短等待秒数
        "chunk_delay_max_sec": 60.0,            # 单条最长等待秒数
        "first_delay_sec": 2.0,                # 第一条回复额外思考时间
        "chunk_chars_per_sec_min": 3.0,         # 模拟打字速度下限（字/秒）
        "chunk_chars_per_sec_max": 5.0,         # 模拟打字速度上限（字/秒）
        "skip_markers": ["[SKIP]", "[不回复]", "[NO_REPLY]"],
        "duplicate_reply_window_sec": 120.0,    # 同群重复回复抑制窗口
        "duplicate_reply_min_chars": 20,        # 少于该长度的短回复不做相似去重
        "duplicate_reply_ratio": 0.86,          # 相似度阈值
        "error_reply": "",                      # LM Studio 故障时是否发提示（空=不打扰）
    },
    "rate_limit": {
        "min_interval_sec": 2.0,                # 同一群两条回复的最小间隔
        "per_user_cooldown_sec": 8.0,           # 同一用户触发冷却
        "max_replies_per_minute": 8,            # 同一群每分钟最多回复次数
    },
    "images": {
        "catalog_limit": 20,                    # 每次提示模型最多列出多少张已保存图片
        "max_items": 1000,                       # 图片库上限；超出按喜爱程度删除低的
        "max_per_save": 5,                      # 一次保存指令最多收几张
        "max_per_reply": 5,                     # 一条回复最多发几张已保存图片
    },
    "voice": {
        "enabled": True,
        "stt_enabled": True,
        "tts_enabled": True,
        "stt_model": "base",                    # faster-whisper 模型
        "language": "zh",
        "tts_voice": "zh-CN-XiaoxiaoNeural",    # 更自然、更有感情的女声
        "tts_rate": "+6%",
        "tts_volume": "+0%",
        "tts_pitch": "+4Hz",
        "qwen_tts_enabled": True,
        "qwen_tts_url": "http://127.0.0.1:8190",
        "qwen_tts_model": "qwen3-tts-voicedesign",
        "qwen_tts_language": "Chinese",
        "qwen_tts_timeout": 240,
        "qwen_tts_server_cmd": "",              # 留空使用 tools/start_qwen_tts.cmd
        "qwen_tts_start_timeout": 90,           # 语音服务启动等待秒数
        "qwen_tts_unload_idle_sec": 60,         # 语音生成完成后无请求多久卸载 TTS 模型
        "max_bytes": 16777216,
        "max_chars": 500,
    },
    "logging": {
        "level": "INFO",
        "dir": "logs",
    },
}

# OneBot 消息段 -> 纯文本占位符
MEDIA_PLACEHOLDERS = {
    "image": "[图片]",
    "face": "[表情]",
    "mface": "[表情]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "forward": "[聊天记录]",
    "markdown": "[卡片]",
}

CQ_RE = re.compile(r"\[CQ:([A-Za-z0-9_.\-]+)((?:,[^\]]*)?)\]")
CQ_ANY_RE = re.compile(r"\[CQ:[^\]]*\]")
URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

log = logging.getLogger("aiqq")


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
class ConfigError(Exception):
    pass


def deep_merge(base, override):
    """把 override 合并进 base 的副本（递归 dict）。"""
    result = {}
    for key, value in base.items():
        if isinstance(value, dict):
            result[key] = deep_merge(value, {})
        elif isinstance(value, list):
            result[key] = list(value)
        else:
            result[key] = value
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path=DEFAULT_CONFIG_PATH):
    if not os.path.isfile(path):
        raise ConfigError(
            "找不到配置文件：%s\n请先编辑 init.txt（填写 qq 和 groups），再运行 setup.bat 生成配置。"
            % path
        )
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            user_cfg = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError("配置文件不是合法 JSON：%s（第 %s 行）" % (path, exc.lineno))
    if not isinstance(user_cfg, dict):
        raise ConfigError("配置文件顶层必须是一个 JSON 对象。")

    cfg = deep_merge(DEFAULT_CONFIG, user_cfg)
    normalize_config(cfg)
    return cfg


def _as_int(value, default=0):
    try:
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_config(cfg):
    qq = cfg["qq"]
    qq["self_id"] = _as_int(qq.get("self_id"), 0)
    qq["groups"] = [_as_int(g) for g in (qq.get("groups") or []) if _as_int(g) > 0]
    qq["ignore_users"] = [_as_int(u) for u in (qq.get("ignore_users") or []) if _as_int(u) > 0]
    qq["admin_users"] = [_as_int(u) for u in (qq.get("admin_users") or []) if _as_int(u) > 0]
    qq["admin_groups"] = [_as_int(g) for g in (qq.get("admin_groups") or []) if _as_int(g) > 0]
    qq["admin_panel_url"] = str(
        qq.get("admin_panel_url") or "http://127.0.0.1:8090").rstrip("/")

    ob = cfg["onebot"]
    ob["api_url"] = str(ob.get("api_url") or DEFAULT_CONFIG["onebot"]["api_url"]).rstrip("/")
    if not URL_RE.match(ob["api_url"]):
        raise ConfigError("onebot.api_url 必须是 http(s) 地址，例如 http://127.0.0.1:3000")
    ob["webhook_host"] = str(ob.get("webhook_host") or "127.0.0.1")
    ob["webhook_port"] = _as_int(ob.get("webhook_port"), 8080)
    ob["webhook_path"] = "/" + str(ob.get("webhook_path") or "/onebot").strip("/")
    ob["webhook_secret"] = str(ob.get("webhook_secret") or "")

    lm = cfg["lm_studio"]
    lm["base_url"] = normalize_lm_base(str(lm.get("base_url") or DEFAULT_CONFIG["lm_studio"]["base_url"]))
    if not URL_RE.match(lm["base_url"]):
        raise ConfigError("lm_studio.base_url 必须是 http(s) 地址，例如 http://127.0.0.1:1234/v1")
    lm["context_messages"] = max(0, min(60, _as_int(lm.get("context_messages"), 16)))
    lm["context_max_chars"] = max(0, _as_int(lm.get("context_max_chars"), 4000))
    lm["context_max_age_min"] = max(0, _as_int(lm.get("context_max_age_min"), 30))
    save_enabled = lm.get("context_save_enabled", True)
    if isinstance(save_enabled, str):
        save_enabled = save_enabled.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    lm["context_save_enabled"] = bool(save_enabled)
    lm["context_save_idle_sec"] = max(0, _as_int(lm.get("context_save_idle_sec"), 0))
    lm["context_save_dir"] = str(lm.get("context_save_dir") or "context").strip() or "context"
    lm["context_memory_messages"] = max(10, min(500, _as_int(
        lm.get("context_memory_messages"), 60)))
    lm["context_keep_files"] = max(1, min(100, _as_int(lm.get("context_keep_files"), 5)))
    lm["context_max_lines"] = max(100, min(100000, _as_int(lm.get("context_max_lines"), 10000)))
    lm["context_max_entries"] = max(10, min(5000, _as_int(lm.get("context_max_entries"), 500)))
    lm["group_context_max_chars"] = max(500, min(100000, _as_int(
        lm.get("group_context_max_chars"), 5000)))
    lm["professional_context_messages"] = max(0, min(100, _as_int(
        lm.get("professional_context_messages"), 20)))
    spontaneous = lm.get("spontaneous_enabled", True)
    if isinstance(spontaneous, str):
        spontaneous = spontaneous.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    lm["spontaneous_enabled"] = bool(spontaneous)
    lm["spontaneous_idle_min_sec"] = max(60, _as_int(lm.get("spontaneous_idle_min_sec"), 1200))
    lm["spontaneous_idle_max_sec"] = max(
        lm["spontaneous_idle_min_sec"], _as_int(lm.get("spontaneous_idle_max_sec"), 3600))
    lm["spontaneous_wait_sec"] = max(30, _as_int(lm.get("spontaneous_wait_sec"), 300))
    lm["spontaneous_sleep_min_sec"] = max(60, _as_int(lm.get("spontaneous_sleep_min_sec"), 7200))
    lm["spontaneous_sleep_max_sec"] = max(
        lm["spontaneous_sleep_min_sec"], _as_int(lm.get("spontaneous_sleep_max_sec"), 21600))
    lm["spontaneous_active_window_sec"] = max(
        60, _as_int(lm.get("spontaneous_active_window_sec"), 3600))

    idle_image = lm.get("idle_image_reply_enabled", True)
    if isinstance(idle_image, str):
        idle_image = idle_image.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    lm["idle_image_reply_enabled"] = bool(idle_image)
    lm["idle_image_poll_sec"] = max(1, _as_int(lm.get("idle_image_poll_sec"), 10))
    lm["idle_image_idle_sec"] = max(30, _as_int(lm.get("idle_image_idle_sec"), 60))
    lm["idle_image_window_sec"] = max(60, _as_int(lm.get("idle_image_window_sec"), 300))
    lm["idle_image_batch_size"] = max(1, min(10, _as_int(
        lm.get("idle_image_batch_size"), 5)))
    lm["idle_image_start_hour"] = max(
        0, min(23, _as_int(lm.get("idle_image_start_hour"), 7)))
    lm["idle_image_end_hour"] = max(
        0, min(23, _as_int(lm.get("idle_image_end_hour"), 22)))
    lm["sleep_prep_quiet_start_hour"] = max(
        0, min(23, _as_int(lm.get("sleep_prep_quiet_start_hour"), 7)))
    lm["sleep_prep_quiet_end_hour"] = max(
        0, min(23, _as_int(lm.get("sleep_prep_quiet_end_hour"), 22)))
    lm["max_tokens"] = max(16, _as_int(lm.get("max_tokens"), 512))
    lm["timeout_sec"] = max(5, _as_int(lm.get("timeout_sec"), 120))
    image_input = str(lm.get("image_input") or "auto").strip().lower()
    if image_input not in ("auto", "on", "off"):
        image_input = "auto"
    lm["image_input"] = image_input
    for key, default in (("context_sender_only", True), ("context_include_bot", False)):
        value = lm.get(key, default)
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
        lm[key] = bool(value)
    only_png_jpeg = lm.get("image_only_png_jpeg", True)
    if isinstance(only_png_jpeg, str):
        only_png_jpeg = only_png_jpeg.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    lm["image_only_png_jpeg"] = bool(only_png_jpeg)
    lm["image_max_bytes"] = max(64 * 1024, _as_int(lm.get("image_max_bytes"), 32 * 1024 * 1024))
    lm["image_max_count"] = max(0, min(10, _as_int(lm.get("image_max_count"), 3)))
    lm["image_max_input_images"] = max(1, min(20, _as_int(lm.get("image_max_input_images"), 10)))
    lm["image_context_messages"] = max(0, min(10, _as_int(lm.get("image_context_messages"), 3)))
    lm["image_context_max_count"] = max(0, min(12, _as_int(lm.get("image_context_max_count"), 8)))
    lm["image_user_messages"] = max(1, min(10, _as_int(lm.get("image_user_messages"), 5)))
    lm["image_selected_max"] = max(1, min(10, _as_int(lm.get("image_selected_max"), 5)))
    if not str(lm.get("system_prompt") or "").strip():
        lm["system_prompt"] = DEFAULT_SYSTEM_PROMPT
    persona_mode = str(lm.get("persona_mode") or "adaptive").strip().lower()
    if persona_mode not in ("adaptive", "always", "off"):
        persona_mode = "adaptive"
    lm["persona_mode"] = persona_mode
    lm["persona_file"] = str(lm.get("persona_file") or "").strip()
    lm["persona_keywords"] = [
        str(k).strip() for k in (lm.get("persona_keywords") or []) if str(k).strip()
    ]
    if not str(lm.get("persona_short_prompt") or "").strip():
        lm["persona_short_prompt"] = DEFAULT_CONFIG["lm_studio"]["persona_short_prompt"]

    trig = cfg["trigger"]
    trig["keywords"] = [str(k).strip() for k in (trig.get("keywords") or []) if str(k).strip()]
    trig["block_keywords"] = [
        str(k).strip() for k in (trig.get("block_keywords") or []) if str(k).strip()
    ]
    trig["regex"] = str(trig.get("regex") or "")
    trig["match_mention"] = bool(trig.get("match_mention", True))
    followup_enabled = trig.get("followup_enabled", True)
    if isinstance(followup_enabled, str):
        followup_enabled = followup_enabled.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    trig["followup_enabled"] = bool(followup_enabled)
    trig["followup_keywords"] = [
        str(k).strip() for k in (trig.get("followup_keywords") or []) if str(k).strip()
    ]
    trig["followup_window_sec"] = max(0.0, float(trig.get("followup_window_sec", 30)))
    trig["followup_extended_sec"] = max(
        trig["followup_window_sec"], float(trig.get("followup_extended_sec", 90)))
    trig["followup_max_messages"] = max(0, min(20, _as_int(trig.get("followup_max_messages"), 0)))
    same_sender = trig.get("followup_same_sender", True)
    if isinstance(same_sender, str):
        same_sender = same_sender.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    trig["followup_same_sender"] = bool(same_sender)
    any_message = trig.get("followup_any_message", True)
    if isinstance(any_message, str):
        any_message = any_message.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    trig["followup_any_message"] = bool(any_message)
    if trig["regex"]:
        try:
            re.compile(trig["regex"], re.IGNORECASE)
        except re.error as exc:
            raise ConfigError("trigger.regex 不是合法正则：%s" % exc)

    reply = cfg["reply"]
    to_message = reply.get("to_message", False)
    if isinstance(to_message, str):
        to_message = to_message.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    reply["to_message"] = bool(to_message)
    reply["max_chars"] = max(50, _as_int(reply.get("max_chars"), 600))
    reply["max_chunks"] = max(1, min(10, _as_int(reply.get("max_chunks"), 3)))
    reply["max_messages"] = max(1, min(12, _as_int(reply.get("max_messages"), 6)))
    reply["split_marker"] = str(reply.get("split_marker", "/Enter/") or "").strip()
    slash_fallback = reply.get("split_slash_fallback", True)
    if isinstance(slash_fallback, str):
        slash_fallback = slash_fallback.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    reply["split_slash_fallback"] = bool(slash_fallback)
    remove_linebreaks = reply.get("remove_linebreaks", True)
    if isinstance(remove_linebreaks, str):
        remove_linebreaks = remove_linebreaks.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    reply["remove_linebreaks"] = bool(remove_linebreaks)
    strip_periods_enabled = reply.get("strip_periods", True)
    if isinstance(strip_periods_enabled, str):
        strip_periods_enabled = strip_periods_enabled.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    reply["strip_periods"] = bool(strip_periods_enabled)
    ignore_comma_breaks = reply.get("ignore_comma_breaks", True)
    if isinstance(ignore_comma_breaks, str):
        ignore_comma_breaks = ignore_comma_breaks.strip().lower() in ("1", "true", "yes", "y", "是", "开", "on")
    reply["ignore_comma_breaks"] = bool(ignore_comma_breaks)
    reply["chunk_delay_sec"] = max(0.0, float(reply.get("chunk_delay_sec", 1.0)))
    reply["chunk_delay_max_sec"] = max(
        reply["chunk_delay_sec"], min(300.0, float(reply.get("chunk_delay_max_sec", 60.0))))
    reply["first_delay_sec"] = max(0.0, float(reply.get("first_delay_sec", 2.0)))
    reply["chunk_chars_per_sec_min"] = max(0.1, float(reply.get("chunk_chars_per_sec_min", 3.0)))
    reply["chunk_chars_per_sec_max"] = max(
        reply["chunk_chars_per_sec_min"], float(reply.get("chunk_chars_per_sec_max", 5.0)))
    reply["skip_markers"] = [str(m) for m in (reply.get("skip_markers") or []) if str(m)]
    reply["duplicate_reply_window_sec"] = max(
        0.0, float(reply.get("duplicate_reply_window_sec", 120.0)))
    reply["duplicate_reply_min_chars"] = max(
        1, min(500, _as_int(reply.get("duplicate_reply_min_chars"), 20)))
    reply["duplicate_reply_ratio"] = min(
        1.0, max(0.0, float(reply.get("duplicate_reply_ratio", 0.86))))

    rl = cfg["rate_limit"]
    rl["min_interval_sec"] = max(0.0, float(rl.get("min_interval_sec", 2.0)))
    rl["per_user_cooldown_sec"] = max(0.0, float(rl.get("per_user_cooldown_sec", 8.0)))
    rl["max_replies_per_minute"] = max(1, _as_int(rl.get("max_replies_per_minute"), 8))

    imgs = cfg["images"]
    imgs["catalog_limit"] = max(0, min(100, _as_int(imgs.get("catalog_limit"), 20)))
    imgs["max_items"] = max(0, min(10000, _as_int(imgs.get("max_items"), 1000)))
    imgs["max_per_save"] = max(1, min(20, _as_int(imgs.get("max_per_save"), 5)))
    imgs["max_per_reply"] = max(0, min(10, _as_int(imgs.get("max_per_reply"), 3)))

    voice = cfg["voice"]
    voice["enabled"] = bool(voice.get("enabled", True))
    voice["stt_enabled"] = bool(voice.get("stt_enabled", True))
    voice["tts_enabled"] = bool(voice.get("tts_enabled", True))
    voice["stt_model"] = str(voice.get("stt_model") or "base").strip()
    voice["language"] = str(voice.get("language") or "zh").strip()
    voice["tts_voice"] = str(voice.get("tts_voice") or "zh-CN-XiaoxiaoNeural").strip()
    voice["tts_rate"] = str(voice.get("tts_rate") or "+6%").strip()
    voice["tts_volume"] = str(voice.get("tts_volume") or "+0%").strip()
    voice["tts_pitch"] = str(voice.get("tts_pitch") or "+4Hz").strip()
    voice["qwen_tts_start_timeout"] = max(10, min(600, _as_int(
        voice.get("qwen_tts_start_timeout"), 90)))
    voice["qwen_tts_unload_idle_sec"] = max(30, _as_int(
        voice.get("qwen_tts_unload_idle_sec"), 60))
    voice["max_bytes"] = max(1024 * 1024, _as_int(voice.get("max_bytes"), 16 * 1024 * 1024))
    voice["max_chars"] = max(50, min(2000, _as_int(voice.get("max_chars"), 500)))

    if not qq["groups"]:
        raise ConfigError("qq.groups 为空：请至少配置一个允许互动的群号。")
    if not trig["keywords"] and not trig["regex"] and not trig["match_mention"]:
        raise ConfigError("触发条件为空：请至少配置一个关键词、正则，或开启 match_mention。")


def normalize_lm_base(url):
    url = url.strip().rstrip("/")
    if re.search(r"/v1$", url):
        return url
    return url + "/v1"


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
def setup_logging(cfg, config_path=None):
    level_name = str(cfg.get("logging", {}).get("level") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log.setLevel(level)
    log.propagate = False

    for handler in list(log.handlers):
        log.removeHandler(handler)

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    log.addHandler(console)

    log_dir = cfg.get("logging", {}).get("dir") or "logs"
    if not os.path.isabs(log_dir):
        log_dir = os.path.join(BASE_DIR if config_path is None else os.path.dirname(os.path.abspath(config_path)), log_dir)
    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            os.path.join(log_dir, "aiqq.log"),
            when="midnight",
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        log.addHandler(file_handler)
    except OSError as exc:
        log.warning("无法写入日志目录 %s：%s（仅输出到控制台）", log_dir, exc)
    return log

__all__ = [name for name in globals() if not name.startswith("__")]
