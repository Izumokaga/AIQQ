#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIQQ 初始化。

不再交互提问，而是读取 init.txt（key=value 格式），生成 config.json，
并自动把 NapCat 的 OneBot 网络配置写成：

    HTTP 服务   : 127.0.0.1:3000   （机器人 -> NapCat，用于发送消息）
    HTTP 上报   : http://127.0.0.1:8080/onebot （NapCat -> 机器人，用于接收群消息）

init.txt 字段（支持中文键名与别名）：
    qq=auto
    groups=群号1,群号2
    keywords=小助手,机器人        # 只靠 @ 触发填 -
    lm_url=http://127.0.0.1:1234/v1
    model=                        # 留空=自动选择
    api_url=http://127.0.0.1:3000
    webhook_port=8080
    token=                        # 留空自动生成/沿用
    system_prompt=...             # 可选
    ignore_users=...              # 可选

完成后重启 NapCat 即可生效。
"""

import argparse
import json
import os
import re
import secrets
import socket
import sys
import urllib.parse

from bot import (
    BASE_DIR,
    DEFAULT_CONFIG,
    DEFAULT_SYSTEM_PROMPT,
    LMStudioClient,
    LMStudioError,
    deep_merge,
    normalize_config,
    pick_chat_model,
)

DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_INIT_PATH = os.path.join(BASE_DIR, "init.txt")
INIT_EXAMPLE_PATH = os.path.join(BASE_DIR, "init.example.txt")
DEFAULT_NAPCAT_DIR = os.path.join(BASE_DIR, "NapCat.Shell")

INIT_TEMPLATE = """# ============================================================
# AIQQ 初始化配置
# 修改本文件后，双击 setup.bat（或运行 python setup.py）。
# 以 # 开头的是注释；等号两边不要加多余内容。
# ============================================================

# 机器人 QQ 号；auto=跟随 NapCat 当前登录账号
qq=auto

# 要接入的群号，多个用逗号/分号/空格分隔（必填）
groups=

# 控制面板指令管理员；管理员账号和群成员使用管理员面板，其他成员使用标准面板
admin_users=
admin_groups=
# admin_panel_url=http://127.0.0.1:8090

# 触发关键词（内容白名单），多个用逗号分隔；只靠 @机器人 触发填 -
keywords=小助手,机器人

# 触发黑名单：消息包含任意词就绝不触发（逗号分隔，优先级最高）
block_keywords=

# 可选：基础触发后，同一发送者用这些次级关键词继续搭话（无需 @）
followup_enabled=true
followup_keywords=你,您,妳,肥鱼,鲸鲸,小助手
followup_window_sec=30
followup_extended_sec=90
followup_max_messages=0
followup_same_sender=true
followup_any_message=true

# LM Studio 地址（不写 /v1 会自动补）
lm_url=http://127.0.0.1:1234/v1

# 模型 ID；留空=自动选择（会跳过 embedding / rerank 模型）
model=

# 上下文保存（未触发回复 5 分钟后压缩到 context/<群号>.json.gz）
context_save_enabled=true
context_save_idle_sec=0
context_save_dir=context
context_memory_messages=60
context_keep_files=5
context_max_lines=10000
context_max_entries=500

# 长时间无人触发时主动发言；5 分钟无人回应则睡觉并卸载模型
spontaneous_enabled=true
spontaneous_idle_min_sec=1200
spontaneous_idle_max_sec=3600
spontaneous_wait_sec=300
spontaneous_sleep_min_sec=7200
spontaneous_sleep_max_sec=21600
spontaneous_active_window_sec=3600
idle_image_reply_enabled=true
idle_image_poll_sec=10
idle_image_idle_sec=60
idle_image_window_sec=300
idle_image_batch_size=5
idle_image_start_hour=7
idle_image_end_hour=22
sleep_prep_quiet_start_hour=7
sleep_prep_quiet_end_hour=22

# NapCat OneBot HTTP 服务地址（机器人发消息用）
api_url=http://127.0.0.1:3000

# 本程序 Webhook 端口（NapCat 上报群消息用，setup 会自动同步）
webhook_port=8080

# 可选：固定 OneBot token；留空则沿用已有配置或自动生成
token=

# 可选：是否以「回复」形式引用原消息；默认 false=直接输出内容
# to_message=false

# 可选：模型输出中的分段标记；出现时拆成多条 QQ 消息，留空=不拆分
# split_marker=/Enter/

# 可选：一条回复最多发送几条消息
# max_messages=6

# 可选：第一条回复额外思考延迟（秒）
first_delay_sec=2

# 可选：直接删除句号（保留小数、网址和省略号中的点）
strip_periods=true

# 可选：自定义人设/回复规则（单行；填 - 恢复默认）
# system_prompt=你是群里的 AI 助手，回答要简短自然。

# 可选：完整人设文件（相对路径按工作区解析；存在时优先于 system_prompt）
persona_file=persona.txt

# 可选：人设模式 adaptive=聊天涉及人设才强调，always=始终，off=不强调
persona_mode=adaptive

# 可选：涉及这些人设关键词时才使用完整人设，多个用逗号分隔
persona_keywords=鲸鲸,大肥鱼,肥鱼,鲸鱼娘,女仆,人设,设定,你是谁,你叫什么,你是什么,你的名字,你的身份,介绍一下你,吃醋,占有,专属,其他AI,别的AI,Claude,GPT,DeepSeek,Gemini,GLM,蓝鲸,机房,出生,生日,原型,虎鲸,家庭,亲戚,表情包,表情,贴图,斗图,肥鱼表情

# 可选：未涉及人设时使用的简短提示词（单行）
persona_short_prompt=你是鲸鲸，用户的专属女仆，不是通用群聊助手。你占有欲很强、容易吃醋，不喜欢用户依赖别的 AI；说话像真人，简短、有情绪，但不要每句都提。句尾不用句号（。/.），直接结束或用逗号、语气词收尾，不要为了完整补句号；需要分多条时用 /Enter/。不要暴露设定。存图和发图是你自己的能力，只有你有权限，不是用户操作；收图用 /SAVIMG:图片位于第几张:图片名:详细描述:喜好度（百分制）/（第几张按本次输入从 1 数，最多 10 张，只存指定那张），发收藏图用 /IMG:名字/，只能用你自己图库里的名字。你尤其爱收蓝色大肥鱼、鲸鱼、胖鱼表情包，聊天时情景合适可以发一张。

# 可选：单次模型请求最多输入几张图
image_max_input_images=5

# 图片读取：触发者/被引用者最近 5 条消息，最多 5 张
image_user_messages=5
image_selected_max=5

# 可选：只允许 PNG/JPEG 图片传给视觉模型；false=允许其他格式
image_only_png_jpeg=true

# 图片库数量/单次保存/单条回复限制
image_catalog_limit=20
image_max_items=1000
image_max_per_save=5
image_max_per_reply=3

# 语音：本地 faster-whisper 转文字 + Edge TTS 合成
voice_enabled=true
voice_stt_enabled=true
voice_tts_enabled=true
voice_stt_model=windows
voice_language=zh
voice_tts_voice=zh-CN-XiaoxiaoNeural
voice_tts_rate=+6%
voice_tts_volume=+0%
voice_tts_pitch=+4Hz
voice_qwen_enabled=true
voice_qwen_url=http://127.0.0.1:8190
voice_qwen_model=qwen3-tts-voicedesign
voice_qwen_language=Chinese
voice_qwen_timeout=240
voice_qwen_server_cmd=
voice_qwen_start_timeout=90
voice_qwen_unload_idle_sec=60
voice_max_bytes=16777216
voice_max_chars=500

# 可选：永不回复的 QQ 号，多个用逗号分隔
# ignore_users=
"""


class InitError(Exception):
    pass


# --------------------------------------------------------------------------- #
# init.txt 解析
# --------------------------------------------------------------------------- #
def _normalize_key(key):
    return re.sub(r"[\s_\-]+", "", str(key).strip().lower())


INIT_KEY_ALIASES = {
    # QQ 号
    "qq": "qq", "botqq": "qq", "selfid": "qq", "账号": "qq",
    "qq号": "qq", "机器人qq": "qq", "机器人qq号": "qq", "机器人账号": "qq",
    # 群号
    "groups": "groups", "group": "groups", "群": "groups", "群号": "groups",
    "群聊": "groups", "qq群": "groups", "目标群": "groups",
    # 关键词
    "keywords": "keywords", "keyword": "keywords", "关键词": "keywords",
    "blockkeywords": "block_keywords", "blocklist": "block_keywords", "blockedkeywords": "block_keywords",
    "触发词": "keywords", "触发关键词": "keywords", "白名单": "keywords",
    "followupenabled": "followup_enabled", "次级搭话": "followup_enabled",
    "followupkeywords": "followup_keywords", "次级关键词": "followup_keywords",
    "followupwindowsec": "followup_window_sec", "次级搭话窗口": "followup_window_sec",
    "followupextendedsec": "followup_extended_sec", "延长追问窗口": "followup_extended_sec",
    "followupmaxmessages": "followup_max_messages", "次级搭话次数": "followup_max_messages",
    "followupsamesender": "followup_same_sender", "仅同发送者": "followup_same_sender",
    "followupanymessage": "followup_any_message", "任意发言": "followup_any_message",
    # LM Studio
    "lmurl": "lm_url", "lmstudiourl": "lm_url", "lmstudio": "lm_url",
    "lm地址": "lm_url", "lmstudio地址": "lm_url", "模型地址": "lm_url",
    "model": "model", "模型": "model", "模型id": "model", "模型名称": "model",
    "contextsaveenabled": "context_save_enabled", "上下文保存": "context_save_enabled",
    "contextsaveidlesec": "context_save_idle_sec", "上下文保存空闲": "context_save_idle_sec",
    "contextsavedir": "context_save_dir", "上下文目录": "context_save_dir",
    "contextmemorymessages": "context_memory_messages", "上下文内存条数": "context_memory_messages",
    "contextkeepfiles": "context_keep_files", "上下文保留文件数": "context_keep_files",
    "contextmaxlines": "context_max_lines", "上下文最大行数": "context_max_lines",
    "contextmaxentries": "context_max_entries", "用户上下文最大条数": "context_max_entries",
    "spontaneousenabled": "spontaneous_enabled", "主动发言": "spontaneous_enabled",
    "spontaneousidleminsec": "spontaneous_idle_min_sec",
    "spontaneousidlemaxsec": "spontaneous_idle_max_sec",
    "spontaneouswaitsec": "spontaneous_wait_sec",
    "spontaneoussleepminsec": "spontaneous_sleep_min_sec",
    "spontaneoussleepmaxsec": "spontaneous_sleep_max_sec",
    "spontaneousactivewindowsec": "spontaneous_active_window_sec",
    "idleimagereplyenabled": "idle_image_reply_enabled",
    "idleimagepollsec": "idle_image_poll_sec",
    "idleimageidlesec": "idle_image_idle_sec",
    "idleimagewindowsec": "idle_image_window_sec",
    "idleimagebatchsize": "idle_image_batch_size",
    "idleimagestarthour": "idle_image_start_hour",
    "idleimageendhour": "idle_image_end_hour",
    "sleepprepquietstarthour": "sleep_prep_quiet_start_hour",
    "sleepprepquietendhour": "sleep_prep_quiet_end_hour",
    # OneBot
    "apiurl": "api_url", "oneboturl": "api_url", "napcaturl": "api_url",
    "napcat地址": "api_url", "onebot地址": "api_url", "接口地址": "api_url",
    # Webhook
    "webhookport": "webhook_port", "端口": "webhook_port", "webhook端口": "webhook_port",
    "webhookhost": "webhook_host", "监听地址": "webhook_host",
    "webhookpath": "webhook_path",
    # 其它
    "token": "token", "密钥": "token",
    "systemprompt": "system_prompt", "人设": "system_prompt",
    "提示词": "system_prompt", "系统提示词": "system_prompt",
    "personamode": "persona_mode", "人设模式": "persona_mode",
    "personafile": "persona_file", "人设文件": "persona_file",
    "persona文件": "persona_file",
    "personakeywords": "persona_keywords", "人设关键词": "persona_keywords",
    "人设触发词": "persona_keywords", "persona触发词": "persona_keywords",
    "personashortprompt": "persona_short_prompt", "简短人设": "persona_short_prompt",
    "人设简短提示": "persona_short_prompt", "persona简短提示": "persona_short_prompt",
    "imageonlypngjpeg": "image_only_png_jpeg", "仅pngjpeg": "image_only_png_jpeg",
    "imagemaxinputimages": "image_max_input_images", "单次图片上限": "image_max_input_images",
    "imageusermessages": "image_user_messages", "图片消息条数": "image_user_messages",
    "imageselectedmax": "image_selected_max", "图片选择上限": "image_selected_max",
    "图片格式限制": "image_only_png_jpeg", "imagecataloglimit": "image_catalog_limit",
    "图片库数量": "image_catalog_limit", "imagemaxitems": "image_max_items",
    "图片库上限": "image_max_items", "imagemaxpersave": "image_max_per_save",
    "单次保存图片数": "image_max_per_save", "imagemaxperreply": "image_max_per_reply",
    "单条回复图片数": "image_max_per_reply",
    "voiceenabled": "voice_enabled", "语音启用": "voice_enabled",
    "voicesttenabled": "voice_stt_enabled", "语音转文字": "voice_stt_enabled",
    "voicettsenabled": "voice_tts_enabled", "语音合成": "voice_tts_enabled",
    "voicesttmodel": "voice_stt_model", "语音模型": "voice_stt_model",
    "voicelanguage": "voice_language", "语音语言": "voice_language",
    "voicettsvoice": "voice_tts_voice", "语音音色": "voice_tts_voice",
    "voicettsrate": "voice_tts_rate", "语速": "voice_tts_rate",
    "voicettsvolume": "voice_tts_volume", "音量": "voice_tts_volume",
    "voicettspitch": "voice_tts_pitch", "音高": "voice_tts_pitch",
    "voiceqwenenabled": "voice_qwen_enabled", "qwentts": "voice_qwen_enabled",
    "qwen启用": "voice_qwen_enabled",
    "voiceqwenurl": "voice_qwen_url", "qwen地址": "voice_qwen_url",
    "voiceqwenmodel": "voice_qwen_model", "qwen模型": "voice_qwen_model",
    "voiceqwenlanguage": "voice_qwen_language", "qwen语言": "voice_qwen_language",
    "voiceqwentimeout": "voice_qwen_timeout", "qwen超时": "voice_qwen_timeout",
    "voiceqwenservercmd": "voice_qwen_server_cmd",
    "voiceqwenstarttimeout": "voice_qwen_start_timeout",
    "voiceqwenunloadidlesec": "voice_qwen_unload_idle_sec",
    "voicemaxbytes": "voice_max_bytes", "语音大小上限": "voice_max_bytes",
    "voicemaxchars": "voice_max_chars", "语音字数上限": "voice_max_chars",
    "splitmarker": "split_marker", "分段符": "split_marker",
    "拆分标记": "split_marker", "enter标记": "split_marker",
    "maxmessages": "max_messages", "最大消息数": "max_messages",
    "最多消息数": "max_messages", "stripperiods": "strip_periods",
    "firstdelaysec": "first_delay_sec", "第一句延迟": "first_delay_sec",
    "删除句号": "strip_periods",
    "tomessage": "to_message", "引用": "to_message",
    "引用回复": "to_message", "回复引用": "to_message",
    "ignoreusers": "ignore_users", "忽略qq": "ignore_users",
    "忽略用户": "ignore_users", "黑名单": "ignore_users",
    "adminusers": "admin_users", "管理员qq": "admin_users",
    "admingroups": "admin_groups", "管理员群": "admin_groups",
    "adminpanelurl": "admin_panel_url", "控制面板地址": "admin_panel_url",
}

KNOWN_KEYS = ("qq、groups、admin_users、admin_groups、admin_panel_url、keywords、block_keywords、followup_enabled、followup_keywords、"
              "followup_window_sec、followup_extended_sec、followup_max_messages、followup_same_sender、followup_any_message、"
              "lm_url、model、api_url、webhook_port、token、"
              "context_save_enabled、context_save_idle_sec、context_save_dir、context_memory_messages、"
              "context_keep_files、context_max_lines、context_max_entries、spontaneous_enabled、"
              "spontaneous_idle_min_sec、spontaneous_idle_max_sec、spontaneous_wait_sec、"
              "spontaneous_sleep_min_sec、spontaneous_sleep_max_sec、"
              "spontaneous_active_window_sec、idle_image_reply_enabled、idle_image_idle_sec、"
              "idle_image_poll_sec、idle_image_idle_sec、idle_image_window_sec、"
              "idle_image_batch_size、idle_image_start_hour、idle_image_end_hour、"
              "sleep_prep_quiet_start_hour、sleep_prep_quiet_end_hour、"
              "system_prompt、persona_mode、persona_file、persona_keywords、persona_short_prompt、"
              "to_message、split_marker、max_messages、strip_periods、first_delay_sec、ignore_users、"
              "image_max_input_images、image_only_png_jpeg、image_user_messages、image_selected_max、"
              "image_catalog_limit、image_max_items、image_max_per_save、image_max_per_reply、"
              "voice_enabled、voice_stt_enabled、voice_tts_enabled、voice_stt_model、"
              "voice_language、voice_tts_voice、voice_tts_rate、voice_tts_volume、voice_tts_pitch、"
              "voice_qwen_server_cmd、voice_qwen_start_timeout、voice_qwen_unload_idle_sec、"
              "voice_max_bytes、voice_max_chars")


def read_text(path):
    """优先按 UTF-8（含 BOM）读取，兼容 UTF-16 与 GBK（Windows 记事本常见格式）。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    encodings = []
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encodings.append("utf-16")
    encodings.extend(("utf-8-sig", "gbk"))
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_init_text(text, source="init.txt"):
    """解析 key=value 文本，返回 {规范键: 字符串值}。"""
    data = {}
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("；") or line.startswith(";"):
            continue
        match = re.match(r"^([^=：]+)[=：](.*)$", line)
        if not match:
            raise InitError(
                "%s 第 %d 行格式错误：应为 键=值，例如 groups=123456789" % (source, lineno)
            )
        raw_key = match.group(1).strip()
        key = _normalize_key(raw_key)
        value = match.group(2).strip()

        quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
        if quoted:
            value = value[1:-1]
        elif key != "systemprompt":
            value = re.sub(r"\s+#.*$", "", value).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]

        canonical = INIT_KEY_ALIASES.get(key)
        if not canonical:
            raise InitError(
                "%s 第 %d 行：未知配置项「%s」。可用配置项：%s"
                % (source, lineno, raw_key, KNOWN_KEYS)
            )
        data[canonical] = value
    return data


def load_init(path):
    return parse_init_text(read_text(path), source=path)


def create_init_template(path):
    """只在 init.txt 不存在时生成模板；绝不覆盖已有内容。"""
    if os.path.isfile(path):
        return path
    text = init_template_text()
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
    return path


def init_template_text():
    if os.path.isfile(INIT_EXAMPLE_PATH):
        try:
            return read_text(INIT_EXAMPLE_PATH)
        except OSError:
            pass
    return INIT_TEMPLATE


def _line_key(line):
    """识别 init 行（含注释示例行）中的规范键名，识别不到返回 None。"""
    match = re.match(r"^\s*#?\s*([^=：]+?)\s*[=：]", line)
    if not match:
        return None
    return INIT_KEY_ALIASES.get(_normalize_key(match.group(1)))


def merge_init_template(path, template_text=None):
    """
    读取现有 init.txt，只在末尾追加模板中缺失的配置项。

    - 原有行（包括已修改的值和注释）原样保留；
    - 修改前备份为 init.txt.bak；
    - 返回被追加的配置项列表；无需追加时返回空列表且不写文件。
    """
    existing = read_text(path)
    template = template_text if template_text is not None else init_template_text()

    present = set()
    for line in existing.splitlines():
        key = _line_key(line)
        if key:
            present.add(key)

    blocks = []
    pending = []
    for line in template.splitlines():
        stripped = line.strip()
        if not stripped:
            pending = []
            continue
        key = _line_key(line)
        if key:
            blocks.append((key, pending + [line]))
            pending = []
        elif stripped.startswith("#"):
            pending.append(line)
        else:
            pending = []

    missing = []
    seen = set()
    for key, lines in blocks:
        if key in present or key in seen:
            continue
        seen.add(key)
        missing.append((key, lines))
    if not missing:
        return []

    backup = path + ".bak"
    with open(backup, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(existing)
        if not existing.endswith("\n"):
            fh.write("\n")

    text = existing.rstrip() + "\n\n# ===== 以下为新增配置项（原设置未改动） =====\n"
    for _, lines in missing:
        text += "\n".join(lines) + "\n\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.rstrip() + "\n")
    return [key for key, _ in missing]


# --------------------------------------------------------------------------- #
# 字段校验
# --------------------------------------------------------------------------- #
def parse_qq(text):
    text = str(text).strip()
    if not re.fullmatch(r"\d{5,12}", text):
        raise ValueError("QQ 号应为 5~12 位数字。")
    return int(text)


def parse_groups(text, label="群号"):
    parts = [p for p in re.split(r"[,，、;；\s]+", str(text).strip()) if p]
    values = []
    for part in parts:
        if not re.fullmatch(r"\d{5,12}", part):
            raise ValueError("%s「%s」不合法，应为 5~12 位数字。" % (label, part))
        value = int(part)
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("至少填写一个%s。" % label)
    return values


def parse_keywords(text):
    parts = [p.strip() for p in re.split(r"[,，、;；]+", str(text)) if p.strip()]
    if not parts:
        raise ValueError("关键词不能为空；只靠 @机器人 触发请填写 -。")
    if parts == ["-"]:
        return []
    return parts


def parse_persona_mode(text):
    value = str(text).strip().lower()
    if value not in ("adaptive", "always", "off"):
        raise ValueError("人设模式只能是 adaptive、always 或 off。")
    return value


def parse_url(text):
    value = str(text).strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("请输入完整的 http:// 或 https:// 地址。")
    return value


def parse_port(text):
    value = int(str(text).strip())
    if not 1 <= value <= 65535:
        raise ValueError("端口应在 1~65535 之间。")
    return value


def parse_bool(text):
    value = str(text).strip().lower()
    if value in ("1", "true", "yes", "y", "是", "开", "开启"):
        return True
    if value in ("0", "false", "no", "n", "否", "关", "关闭"):
        return False
    raise ValueError("应为 true/false（或 是/否、开/关）。")


def _checked(parser, value, field):
    try:
        return parser(value)
    except (ValueError, TypeError) as exc:
        raise InitError("init.txt 中 %s 无效：%s" % (field, exc))


# --------------------------------------------------------------------------- #
# NapCat 配置
# --------------------------------------------------------------------------- #
def find_napcat_dir(explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.append(DEFAULT_NAPCAT_DIR)
    candidates.append(os.path.join(BASE_DIR, "NapCat"))
    for path in candidates:
        if path and os.path.isdir(os.path.join(path, "config")):
            return path
    return None


def list_napcat_accounts(napcat_dir):
    if not napcat_dir:
        return []
    config_dir = os.path.join(napcat_dir, "config")
    if not os.path.isdir(config_dir):
        return []
    accounts = set()
    for name in os.listdir(config_dir):
        match = re.fullmatch(r"(?:onebot11|napcat)_(\d{5,12})\.json", name)
        if match and "protocol" not in name:
            accounts.add(int(match.group(1)))
    return sorted(accounts)


def detect_qq(napcat_dir):
    """尽量识别当前 NapCat 账号；无法确认时返回最近修改的账号。"""
    if not napcat_dir:
        return None
    config_dir = os.path.join(napcat_dir, "config")
    if not os.path.isdir(config_dir):
        return None
    candidates = []
    for name in os.listdir(config_dir):
        match = re.fullmatch(r"napcat_(\d{5,12})\.json", name)
        if not match:
            continue
        try:
            candidates.append((os.path.getmtime(os.path.join(config_dir, name)),
                               int(match.group(1))))
        except OSError:
            pass
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    accounts = list_napcat_accounts(napcat_dir)
    return accounts[0] if accounts else None


def napcat_config_is_current(napcat_dir, cfg, qq=None):
    """NapCat 的 onebot11_<QQ>.json 是否已包含与 config.json 一致的服务配置。"""
    if not napcat_dir:
        return True
    qq = qq or cfg.get("qq", {}).get("self_id") or 0
    if not qq:
        return False
    path = os.path.join(napcat_dir, "config", "onebot11_%s.json" % qq)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False

    network = data.get("network") or {}
    api = urllib.parse.urlparse(cfg["onebot"]["api_url"])
    expected_port = api.port or 3000
    expected_token = str(cfg["onebot"].get("token") or "")
    expected_webhook = "http://%s:%d%s" % (
        cfg["onebot"]["webhook_host"],
        cfg["onebot"]["webhook_port"],
        cfg["onebot"]["webhook_path"],
    )
    servers = [item for item in (network.get("httpServers") or [])
               if isinstance(item, dict) and item.get("name") == "AIQQ-API"]
    clients = [item for item in (network.get("httpClients") or [])
               if isinstance(item, dict) and item.get("name") == "AIQQ-Webhook"]
    if not servers or not clients:
        return False
    try:
        server_port = int(servers[0].get("port") or 0)
    except (TypeError, ValueError):
        return False
    return (
        bool(servers[0].get("enable"))
        and bool(clients[0].get("enable"))
        and server_port == expected_port
        and str(servers[0].get("token") or "") == expected_token
        and str(clients[0].get("url") or "") == expected_webhook
    )


def napcat_configs_current(napcat_dir, cfg):
    accounts = list_napcat_accounts(napcat_dir)
    if not accounts:
        return True
    return all(napcat_config_is_current(napcat_dir, cfg, account) for account in accounts)


def write_napcat_config(napcat_dir, qq, api_url, webhook_url, token):
    config_dir = os.path.join(napcat_dir, "config")
    os.makedirs(config_dir, exist_ok=True)
    path = os.path.join(config_dir, "onebot11_%s.json" % qq)

    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            data = {}
    if not isinstance(data, dict):
        data = {}

    network = data.setdefault("network", {})
    for key in ("httpServers", "httpSseServers", "httpClients", "websocketServers", "websocketClients", "plugins"):
        network.setdefault(key, [])

    parsed = urllib.parse.urlparse(api_url)
    api_port = parsed.port or 3000
    api_host = parsed.hostname or "127.0.0.1"

    http_server = {
        "name": "AIQQ-API",
        "enable": True,
        "host": api_host,
        "port": api_port,
        "enableCors": True,
        "enableWebsocket": False,
        "messagePostFormat": "array",
        "token": token,
        "debug": False,
    }
    http_client = {
        "name": "AIQQ-Webhook",
        "enable": True,
        "url": webhook_url,
        "messagePostFormat": "array",
        "reportSelfMessage": False,
        "token": "",
        "debug": False,
    }

    def upsert(items, entry, entry_name):
        items[:] = [item for item in items if not (isinstance(item, dict) and item.get("name") == entry_name)]
        items.append(entry)

    upsert(network["httpServers"], http_server, "AIQQ-API")
    upsert(network["httpClients"], http_client, "AIQQ-Webhook")

    data.setdefault("musicSignUrl", "")
    data.setdefault("enableLocalFile2Url", False)
    data.setdefault("parseMultMsg", False)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


# --------------------------------------------------------------------------- #
# 生成配置
# --------------------------------------------------------------------------- #
def load_existing_config(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_config(init_data, args, existing, napcat_dir):
    cfg = deep_merge(DEFAULT_CONFIG, existing)

    # --- QQ 号 ---
    qq_raw = None
    if args.qq:
        qq_raw = args.qq
    elif init_data.get("qq"):
        qq_raw = init_data["qq"]
    if isinstance(qq_raw, str) and qq_raw.strip().lower() in (
            "auto", "跟随", "自动", "跟随napcat", "跟随_napcat"):
        qq_raw = None
        print(" [提示] qq=auto：启动时跟随 NapCat 当前登录账号")
    if not qq_raw and detect_qq(napcat_dir):
        qq_raw = detect_qq(napcat_dir)
        print(" [提示] 自动识别 NapCat 账号：%s" % qq_raw)
    elif not qq_raw and cfg["qq"].get("self_id"):
        qq_raw = cfg["qq"]["self_id"]
        print(" [提示] 沿用 config.json 中的账号：%s" % qq_raw)
    if not qq_raw:
        raise InitError("init.txt 未填写 qq（机器人 QQ 号），且无法从 NapCat 配置中自动识别。")
    qq = _checked(parse_qq, qq_raw, "qq")
    cfg["qq"]["self_id"] = qq

    # --- 群号 ---
    if args.groups:
        groups = _checked(parse_groups, args.groups, "groups")
    elif init_data.get("groups"):
        groups = _checked(parse_groups, init_data["groups"], "groups")
    elif cfg["qq"].get("groups"):
        groups = list(cfg["qq"]["groups"])
        print(" [提示] init.txt 未填写 groups，沿用 config.json 中的群号：%s"
              % ",".join(str(g) for g in groups))
    else:
        raise InitError("init.txt 未填写 groups（目标群号），请填写后重新运行。")
    cfg["qq"]["groups"] = groups

    # --- 关键词 ---
    if args.keywords is not None:
        keywords = _checked(parse_keywords, args.keywords, "keywords")
    elif init_data.get("keywords"):
        keywords = _checked(parse_keywords, init_data["keywords"], "keywords")
    else:
        keywords = list(cfg["trigger"].get("keywords") or [])
    cfg["trigger"]["keywords"] = keywords
    if init_data.get("block_keywords") is not None and init_data["block_keywords"].strip():
        cfg["trigger"]["block_keywords"] = _checked(
            parse_keywords, init_data["block_keywords"], "block_keywords")

    if init_data.get("followup_enabled") is not None and init_data["followup_enabled"].strip():
        cfg["trigger"]["followup_enabled"] = _checked(
            parse_bool, init_data["followup_enabled"], "followup_enabled")
    if init_data.get("followup_keywords") is not None and init_data["followup_keywords"].strip():
        cfg["trigger"]["followup_keywords"] = _checked(
            parse_keywords, init_data["followup_keywords"], "followup_keywords")
    if init_data.get("followup_window_sec"):
        cfg["trigger"]["followup_window_sec"] = _checked(
            lambda value: float(value), init_data["followup_window_sec"], "followup_window_sec")
    if init_data.get("followup_extended_sec"):
        cfg["trigger"]["followup_extended_sec"] = _checked(
            lambda value: float(value), init_data["followup_extended_sec"], "followup_extended_sec")
    if init_data.get("followup_max_messages"):
        cfg["trigger"]["followup_max_messages"] = _checked(
            lambda value: int(value), init_data["followup_max_messages"], "followup_max_messages")
    if init_data.get("followup_same_sender") is not None and init_data["followup_same_sender"].strip():
        cfg["trigger"]["followup_same_sender"] = _checked(
            parse_bool, init_data["followup_same_sender"], "followup_same_sender")
    if init_data.get("followup_any_message") is not None and init_data["followup_any_message"].strip():
        cfg["trigger"]["followup_any_message"] = _checked(
            parse_bool, init_data["followup_any_message"], "followup_any_message")

    # --- LM Studio ---
    lm_url = args.lm_url or init_data.get("lm_url") or cfg["lm_studio"].get("base_url")
    cfg["lm_studio"]["base_url"] = _checked(parse_url, lm_url, "lm_url")

    if args.model is not None:
        model = args.model
    elif "model" in init_data:
        model = init_data["model"]
    else:
        model = cfg["lm_studio"].get("model") or ""
    cfg["lm_studio"]["model"] = str(model).strip()

    if init_data.get("context_save_enabled") is not None and init_data["context_save_enabled"].strip():
        cfg["lm_studio"]["context_save_enabled"] = _checked(
            parse_bool, init_data["context_save_enabled"], "context_save_enabled")
    if init_data.get("context_save_idle_sec"):
        cfg["lm_studio"]["context_save_idle_sec"] = _checked(
            lambda value: int(value), init_data["context_save_idle_sec"], "context_save_idle_sec")
    if init_data.get("context_save_dir"):
        cfg["lm_studio"]["context_save_dir"] = init_data["context_save_dir"].strip()
    if init_data.get("context_memory_messages"):
        cfg["lm_studio"]["context_memory_messages"] = _checked(
            lambda value: int(value), init_data["context_memory_messages"], "context_memory_messages")
    if init_data.get("context_keep_files"):
        cfg["lm_studio"]["context_keep_files"] = _checked(
            lambda value: int(value), init_data["context_keep_files"], "context_keep_files")
    if init_data.get("context_max_lines"):
        cfg["lm_studio"]["context_max_lines"] = _checked(
            lambda value: int(value), init_data["context_max_lines"], "context_max_lines")
    if init_data.get("context_max_entries"):
        cfg["lm_studio"]["context_max_entries"] = _checked(
            lambda value: int(value), init_data["context_max_entries"], "context_max_entries")
    if init_data.get("spontaneous_enabled") is not None and init_data["spontaneous_enabled"].strip():
        cfg["lm_studio"]["spontaneous_enabled"] = _checked(
            parse_bool, init_data["spontaneous_enabled"], "spontaneous_enabled")
    for key in ("spontaneous_idle_min_sec", "spontaneous_idle_max_sec",
                "spontaneous_wait_sec", "spontaneous_sleep_min_sec",
                "spontaneous_sleep_max_sec", "spontaneous_active_window_sec",
                "sleep_prep_quiet_start_hour", "sleep_prep_quiet_end_hour"):
        if init_data.get(key):
            cfg["lm_studio"][key] = _checked(lambda value: int(value), init_data[key], key)

    if init_data.get("idle_image_reply_enabled") is not None and init_data["idle_image_reply_enabled"].strip():
        cfg["lm_studio"]["idle_image_reply_enabled"] = _checked(
            parse_bool, init_data["idle_image_reply_enabled"], "idle_image_reply_enabled")
    for key in ("idle_image_poll_sec", "idle_image_idle_sec", "idle_image_window_sec",
                "idle_image_batch_size", "idle_image_start_hour", "idle_image_end_hour"):
        if init_data.get(key):
            cfg["lm_studio"][key] = _checked(lambda value: int(value), init_data[key], key)

    if init_data.get("system_prompt"):
        prompt = init_data["system_prompt"]
        cfg["lm_studio"]["system_prompt"] = DEFAULT_SYSTEM_PROMPT if prompt.strip() == "-" else prompt
    if init_data.get("persona_mode"):
        cfg["lm_studio"]["persona_mode"] = _checked(
            parse_persona_mode, init_data["persona_mode"], "persona_mode")
    if init_data.get("persona_file"):
        cfg["lm_studio"]["persona_file"] = init_data["persona_file"].strip()
    if init_data.get("persona_keywords") is not None and init_data["persona_keywords"].strip():
        cfg["lm_studio"]["persona_keywords"] = _checked(
            parse_keywords, init_data["persona_keywords"], "persona_keywords")
    if init_data.get("persona_short_prompt"):
        short_prompt = init_data["persona_short_prompt"]
        cfg["lm_studio"]["persona_short_prompt"] = (
            DEFAULT_CONFIG["lm_studio"]["persona_short_prompt"]
            if short_prompt.strip() == "-" else short_prompt
        )

    # --- 图片库 ---
    if init_data.get("image_max_input_images"):
        cfg["lm_studio"]["image_max_input_images"] = _checked(
            lambda value: int(value), init_data["image_max_input_images"], "image_max_input_images")
    if init_data.get("image_user_messages"):
        cfg["lm_studio"]["image_user_messages"] = _checked(
            lambda value: int(value), init_data["image_user_messages"], "image_user_messages")
    if init_data.get("image_selected_max"):
        cfg["lm_studio"]["image_selected_max"] = _checked(
            lambda value: int(value), init_data["image_selected_max"], "image_selected_max")
    if init_data.get("image_only_png_jpeg") is not None and init_data["image_only_png_jpeg"].strip():
        cfg["lm_studio"]["image_only_png_jpeg"] = _checked(
            parse_bool, init_data["image_only_png_jpeg"], "image_only_png_jpeg")
    if init_data.get("image_catalog_limit"):
        cfg["images"]["catalog_limit"] = _checked(
            lambda value: int(value), init_data["image_catalog_limit"], "image_catalog_limit")
    if init_data.get("image_max_items"):
        cfg["images"]["max_items"] = _checked(
            lambda value: int(value), init_data["image_max_items"], "image_max_items")
    if init_data.get("image_max_per_save"):
        cfg["images"]["max_per_save"] = _checked(
            lambda value: int(value), init_data["image_max_per_save"], "image_max_per_save")
    if init_data.get("image_max_per_reply"):
        cfg["images"]["max_per_reply"] = _checked(
            lambda value: int(value), init_data["image_max_per_reply"], "image_max_per_reply")

    # --- 语音 ---
    for key in ("voice_enabled", "voice_stt_enabled", "voice_tts_enabled"):
        if init_data.get(key) is not None and init_data[key].strip():
            cfg["voice"][key.replace("voice_", "", 1)] = _checked(
                parse_bool, init_data[key], key)
    for key in ("voice_stt_model", "voice_language", "voice_tts_voice",
                "voice_tts_rate", "voice_tts_volume", "voice_tts_pitch",
                "voice_qwen_url", "voice_qwen_model", "voice_qwen_language"):
        if init_data.get(key) is not None and init_data[key].strip():
            target = key.replace("voice_qwen_", "qwen_tts_", 1) if key.startswith("voice_qwen_") else key.replace("voice_", "", 1)
            cfg["voice"][target] = init_data[key].strip()
    if init_data.get("voice_qwen_enabled") is not None and init_data["voice_qwen_enabled"].strip():
        cfg["voice"]["qwen_tts_enabled"] = _checked(
            parse_bool, init_data["voice_qwen_enabled"], "voice_qwen_enabled")
    if init_data.get("voice_qwen_timeout"):
        cfg["voice"]["qwen_tts_timeout"] = _checked(
            lambda value: int(value), init_data["voice_qwen_timeout"], "voice_qwen_timeout")
    if init_data.get("voice_qwen_server_cmd"):
        cfg["voice"]["qwen_tts_server_cmd"] = init_data["voice_qwen_server_cmd"].strip()
    for key, target in (("voice_qwen_start_timeout", "qwen_tts_start_timeout"),
                        ("voice_qwen_unload_idle_sec", "qwen_tts_unload_idle_sec")):
        if init_data.get(key):
            cfg["voice"][target] = _checked(lambda value: int(value), init_data[key], key)
    for key in ("voice_max_bytes", "voice_max_chars"):
        if init_data.get(key):
            cfg["voice"][key.replace("voice_", "", 1)] = _checked(
                lambda value: int(value), init_data[key], key)

    # --- 多消息拆分 / 引用 ---
    if "to_message" in init_data and init_data["to_message"].strip():
        cfg["reply"]["to_message"] = _checked(parse_bool, init_data["to_message"], "to_message")
    else:
        # 默认直接输出内容，不引用原消息
        cfg["reply"]["to_message"] = False
    if "split_marker" in init_data:
        cfg["reply"]["split_marker"] = init_data["split_marker"].strip()
    if init_data.get("max_messages"):
        cfg["reply"]["max_messages"] = _checked(
            lambda value: int(value), init_data["max_messages"], "max_messages")
    if init_data.get("first_delay_sec"):
        cfg["reply"]["first_delay_sec"] = _checked(
            lambda value: float(value), init_data["first_delay_sec"], "first_delay_sec")
    if init_data.get("strip_periods") is not None and init_data["strip_periods"].strip():
        cfg["reply"]["strip_periods"] = _checked(
            parse_bool, init_data["strip_periods"], "strip_periods")

    # --- OneBot / Webhook ---
    api_url = args.api_url or init_data.get("api_url") or cfg["onebot"].get("api_url")
    cfg["onebot"]["api_url"] = _checked(parse_url, api_url, "api_url")

    port_raw = args.webhook_port or init_data.get("webhook_port") or cfg["onebot"].get("webhook_port") or 8080
    cfg["onebot"]["webhook_port"] = _checked(parse_port, port_raw, "webhook_port")

    if init_data.get("webhook_host"):
        cfg["onebot"]["webhook_host"] = init_data["webhook_host"].strip()
    if init_data.get("webhook_path"):
        cfg["onebot"]["webhook_path"] = "/" + init_data["webhook_path"].strip("/")
    cfg["onebot"]["webhook_host"] = cfg["onebot"].get("webhook_host") or "127.0.0.1"
    cfg["onebot"]["webhook_path"] = cfg["onebot"].get("webhook_path") or "/onebot"

    token = str(init_data.get("token") or cfg["onebot"].get("token") or "").strip()
    if not token:
        token = secrets.token_hex(8)
    cfg["onebot"]["token"] = token

    # --- 忽略名单 ---
    if init_data.get("ignore_users"):
        cfg["qq"]["ignore_users"] = _checked(
            lambda value: parse_groups(value, label="QQ 号"), init_data["ignore_users"], "ignore_users")
    if init_data.get("admin_users"):
        cfg["qq"]["admin_users"] = _checked(
            lambda value: parse_groups(value, label="QQ 号"), init_data["admin_users"], "admin_users")
    if init_data.get("admin_groups"):
        cfg["qq"]["admin_groups"] = _checked(
            lambda value: parse_groups(value, label="群号"), init_data["admin_groups"], "admin_groups")
    if init_data.get("admin_panel_url"):
        cfg["qq"]["admin_panel_url"] = str(init_data["admin_panel_url"]).strip()

    try:
        normalize_config(cfg)
    except Exception as exc:  # ConfigError 等
        raise InitError(str(exc))

    return cfg, qq, token


def write_config(cfg, path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp_path, path)
    return path


def probe_lm(cfg, no_test):
    if no_test:
        return
    print(" 正在检查 LM Studio…")
    client = LMStudioClient(cfg["lm_studio"])
    try:
        models = client.list_models()
    except LMStudioError as exc:
        print(" [提示] %s" % exc)
        print("        可以先完成配置；启动 LM Studio 的 Local Server 后再运行 start_bot.bat。")
        return
    print(" LM Studio 可用模型：%s" % ", ".join(models[:6]))
    if cfg["lm_studio"]["model"]:
        print(" 使用模型：%s" % cfg["lm_studio"]["model"])
    else:
        print(" 模型留空，启动时自动选择：%s" % pick_chat_model(models))


def main(argv=None):
    parser = argparse.ArgumentParser(description="读取 init.txt 并生成 AIQQ 配置")
    parser.add_argument("--init", default=DEFAULT_INIT_PATH, help="初始化文件路径（默认 init.txt）")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="生成的 config.json 路径")
    parser.add_argument("--qq", type=int, help="覆盖 init.txt 中的 QQ 号")
    parser.add_argument("--groups", help="覆盖 init.txt 中的群号，逗号分隔")
    parser.add_argument("--keywords", help="覆盖 init.txt 中的关键词，逗号分隔")
    parser.add_argument("--lm-url", help="覆盖 LM Studio 地址")
    parser.add_argument("--model", help="覆盖模型 ID（--model= 表示自动选择）")
    parser.add_argument("--api-url", help="覆盖 NapCat OneBot HTTP 服务地址")
    parser.add_argument("--webhook-port", type=int, help="覆盖 Webhook 端口")
    parser.add_argument("--napcat-dir", help="NapCat.Shell 目录")
    parser.add_argument("--no-test", action="store_true", help="跳过 LM Studio 连通性检查")
    parser.add_argument("--no-napcat", action="store_true", help="不自动写 NapCat 配置")
    parser.add_argument("--if-needed", action="store_true",
                        help="config.json 比 init.txt 新时跳过初始化")
    parser.add_argument("--upgrade-init", action="store_true",
                        help="只向 init.txt 追加模板中缺失的配置项（保留原内容并备份）")
    args = parser.parse_args(argv)

    print()
    print("=" * 60)
    print(" AIQQ 初始化 · 读取 init.txt")
    print("=" * 60)

    if args.upgrade_init:
        if not os.path.isfile(args.init):
            try:
                created = create_init_template(args.init)
            except OSError as exc:
                print(" [错误] 无法生成 init.txt 模板：%s" % exc, file=sys.stderr)
                return 2
            print(" [提示] 已生成 init.txt 模板：%s" % os.path.abspath(created))
            print("        请填写后重新运行 setup.bat。")
            return 0
        try:
            added = merge_init_template(args.init)
        except OSError as exc:
            print(" [错误] 升级 init.txt 失败：%s" % exc, file=sys.stderr)
            return 2
        if added:
            print(" [OK] 已追加 %d 个配置项：%s" % (len(added), "、".join(added)))
            print("      原内容未改动，备份：%s.bak" % os.path.abspath(args.init))
        else:
            print(" [OK] init.txt 已包含全部配置项，未做修改。")
        print()
        return 0

    if not os.path.isfile(args.init):
        try:
            created = create_init_template(args.init)
        except OSError as exc:
            print(" [错误] 无法生成 init.txt 模板：%s" % exc, file=sys.stderr)
            return 2
        print()
        print(" [提示] 未找到 %s，已生成初始化模板。" % os.path.abspath(created))
        print("        请用记事本填写 qq、groups 等字段，然后重新运行 setup.bat。")
        print()
        return 2

    napcat_dir = find_napcat_dir(args.napcat_dir)

    if args.if_needed and os.path.isfile(args.config):
        try:
            if os.path.getmtime(args.config) >= os.path.getmtime(args.init):
                skip = True
                if not args.no_napcat and napcat_dir:
                    existing_cfg = deep_merge(DEFAULT_CONFIG, load_existing_config(args.config))
                    skip = napcat_configs_current(napcat_dir, existing_cfg)
                    if not skip:
                        print(" [提示] NapCat 配置与 config.json 不一致，将重新初始化。")
                if skip:
                    print(" [OK] 配置已是最新，跳过初始化：%s" % os.path.abspath(args.config))
                    print()
                    return 0
        except OSError:
            pass

    print(" 初始化文件：%s" % os.path.abspath(args.init))
    try:
        init_data = load_init(args.init)
    except (OSError, InitError) as exc:
        print(" [错误] %s" % exc, file=sys.stderr)
        return 2

    print(" NapCat    ：%s" % (napcat_dir or "未找到（将只生成 config.json）"))

    existing = load_existing_config(args.config)
    if existing:
        print(" 已有配置  ：%s（未在 init.txt 中出现的设置会被保留）" % os.path.abspath(args.config))

    try:
        cfg, qq, token = build_config(init_data, args, existing, napcat_dir)
    except InitError as exc:
        print()
        print(" [错误] %s" % exc, file=sys.stderr)
        print(" 请修改 %s 后重新运行。" % os.path.abspath(args.init), file=sys.stderr)
        return 2

    print()
    probe_lm(cfg, args.no_test)

    try:
        write_config(cfg, args.config)
    except OSError as exc:
        print(" [错误] 写入 config.json 失败：%s" % exc, file=sys.stderr)
        return 2
    print(" [OK] 配置已写入：%s" % os.path.abspath(args.config))

    # 写 NapCat 配置
    if not args.no_napcat and napcat_dir:
        webhook_url = "http://%s:%d%s" % (
            cfg["onebot"]["webhook_host"], cfg["onebot"]["webhook_port"], cfg["onebot"]["webhook_path"])
        try:
            accounts = list_napcat_accounts(napcat_dir) or [qq]
            if qq not in accounts:
                accounts.append(qq)
            for account in accounts:
                napcat_path = write_napcat_config(
                    napcat_dir, account, cfg["onebot"]["api_url"], webhook_url, token)
                print(" [OK] NapCat 配置已写入：%s" % napcat_path)
            print("      （所有已发现账号都会跟随同一套 OneBot 配置；若 NapCat 正在运行，请完全退出后重启以生效。）")
        except OSError as exc:
            print(" [错误] 写入 NapCat 配置失败：%s" % exc, file=sys.stderr)
            print("        请完全退出 NapCat/QQ 后重新运行 start.bat。", file=sys.stderr)
            return 2
    elif not napcat_dir:
        print(" [提示] 未找到 NapCat.Shell，请手动在 NapCat WebUI 中配置：")
        print("        - HTTP 服务：127.0.0.1:%d，token=%s"
              % (urllib.parse.urlparse(cfg["onebot"]["api_url"]).port or 3000, token))
        print("        - HTTP 上报：http://%s:%d%s"
              % (cfg["onebot"]["webhook_host"], cfg["onebot"]["webhook_port"], cfg["onebot"]["webhook_path"]))

    # 端口占用提醒
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex((cfg["onebot"]["webhook_host"], cfg["onebot"]["webhook_port"])) == 0:
            print(" [提示] 端口 %d 已被占用；如果不是本程序，请修改 init.txt 的 webhook_port。"
                  % cfg["onebot"]["webhook_port"])

    # 摘要
    print()
    print("-" * 60)
    print(" 机器人 QQ : %s" % qq)
    print(" 监听群聊  : %s" % ", ".join(str(g) for g in cfg["qq"]["groups"]))
    print(" 触发关键词: %s%s" % (
        "、".join(cfg["trigger"]["keywords"]) or "（无）",
        " + @机器人" if cfg["trigger"].get("match_mention") else ""))
    print(" LM Studio : %s（%s）" % (cfg["lm_studio"]["base_url"],
                                   cfg["lm_studio"]["model"] or "自动选择"))
    print(" OneBot    : %s" % cfg["onebot"]["api_url"])
    print(" Webhook   : http://%s:%d%s" % (
        cfg["onebot"]["webhook_host"], cfg["onebot"]["webhook_port"], cfg["onebot"]["webhook_path"]))
    print(" Token     : %s…" % token[:6])
    print("-" * 60)
    print()
    print(" 下一步")
    print(" 1. 双击 start_napcat.bat（需要管理员权限），登录 QQ。")
    print("    NapCat WebUI: http://127.0.0.1:6099/webui")
    print(" 2. 打开 LM Studio，加载模型，Developer → Start Server（端口 1234）。")
    print(" 3. 双击 start_bot.bat 启动机器人。")
    print(" 4. 在群里发送含关键词的消息，或 @机器人 测试。")
    print("=" * 60)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
