#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ voice：语音识别、TTS 生命周期与语音标记解析。"""
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



from .config import BASE_DIR, VOICE_DEPS, _as_int, log

class VoiceError(Exception):
    pass


class VoiceEngine(object):
    """Windows 中文语音识别 + Edge TTS 语音合成。"""

    def __init__(self, cfg, root):
        self.enabled = bool(cfg.get("enabled", True))
        self.stt_enabled = bool(cfg.get("stt_enabled", True))
        self.tts_enabled = bool(cfg.get("tts_enabled", True))
        self.language = str(cfg.get("language") or "zh")
        self.voice = str(cfg.get("tts_voice") or "Microsoft Huihui Desktop")
        self.rate = str(cfg.get("tts_rate") or "+0%")
        self.volume = str(cfg.get("tts_volume") or "+0%")
        self.pitch = str(cfg.get("tts_pitch") or "+4Hz")
        self.qwen_enabled = bool(cfg.get("qwen_tts_enabled", False))
        self.qwen_url = str(cfg.get("qwen_tts_url") or "http://127.0.0.1:8190").rstrip("/")
        self.qwen_model = str(cfg.get("qwen_tts_model") or "qwen3-tts-voicedesign")
        self.qwen_language = str(cfg.get("qwen_tts_language") or "Chinese")
        self.qwen_timeout = max(30, _as_int(cfg.get("qwen_tts_timeout"), 240))
        self.max_bytes = max(1024 * 1024, _as_int(cfg.get("max_bytes"), 16 * 1024 * 1024))
        self.max_chars = max(50, min(2000, _as_int(cfg.get("max_chars"), 500)))
        self.qwen_start_timeout = max(10, min(600, _as_int(
            cfg.get("qwen_tts_start_timeout"), 90)))
        self.qwen_unload_idle = max(30, _as_int(cfg.get("qwen_tts_unload_idle_sec"), 60))
        self.qwen_server_cmd = str(cfg.get("qwen_tts_server_cmd") or "").strip()
        if not self.qwen_server_cmd:
            self.qwen_server_cmd = os.path.join(root, "tools", "start_qwen_tts.cmd")
        self._qwen_process = None
        self._qwen_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active = 0
        self.root = os.path.join(root, "voice")
        self.cache_dir = os.path.join(root, "voice_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    def _powershell(self, script, env_extra, timeout=90):
        env = os.environ.copy()
        env.update(env_extra)
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise VoiceError("语音处理超时")
        except OSError as exc:
            raise VoiceError("无法调用 PowerShell：%s" % exc)
        return (result.stdout or b"").decode("utf-8", errors="replace").strip(), \
               (result.stderr or b"").decode("utf-8", errors="replace").strip()

    def _ffmpeg(self):
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
        for base in (VOICE_DEPS, BASE_DIR):
            for root, _dirs, files in os.walk(base):
                for name in files:
                    if name.lower().startswith("ffmpeg") and name.lower().endswith(".exe"):
                        return os.path.join(root, name)
        return ""

    def _percent(self, value):
        match = re.search(r"([+-]?\d+)", str(value or ""))
        return int(match.group(1)) if match else 0

    def _to_wav(self, source, target):
        if source.lower().endswith(".silk"):
            try:
                import pilk
                decoded = source + ".wav"
                pilk.silk_to_wav(source, decoded, rate=24000)
                source = decoded
            except Exception as exc:  # noqa: BLE001
                raise VoiceError("silk 解码失败：%s" % exc)
        ffmpeg = self._ffmpeg()
        if not ffmpeg:
            raise VoiceError("缺少 ffmpeg，无法转换语音格式")
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-i", source, "-ar", "16000", "-ac", "1",
                 "-c:a", "pcm_s16le", target],
                capture_output=True, timeout=90)
        except subprocess.TimeoutExpired:
            raise VoiceError("音频转换超时")
        if result.returncode != 0 or not os.path.isfile(target):
            err = (result.stderr or b"").decode("utf-8", errors="replace")[-300:]
            raise VoiceError("音频转换失败：%s" % err)

    def transcribe(self, info):
        if not self.enabled or not self.stt_enabled:
            return ""
        data = read_image_bytes(info, self.max_bytes)
        filename = str(info.get("file") or info.get("url") or "voice")
        suffix = os.path.splitext(filename.split("?", 1)[0])[1].lower()
        if suffix not in (".mp3", ".wav", ".amr", ".silk", ".ogg", ".m4a", ".flac", ".aac"):
            suffix = ".amr"
        stamp = time.time_ns()
        source = os.path.join(self.cache_dir, "stt_%s%s" % (stamp, suffix))
        wav_path = os.path.join(self.cache_dir, "stt_%s.wav" % stamp)
        try:
            with open(source, "wb") as fh:
                fh.write(data)
            self._to_wav(source, wav_path)
            script = (
                "Add-Type -AssemblyName System.Speech;"
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                "$r=New-Object System.Speech.Recognition.SpeechRecognitionEngine;"
                "$r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar));"
                "$r.SetInputToWaveFile($env:AIQQ_STT_WAV);"
                "$texts=@();"
                "while($true){try{$res=$r.Recognize()}catch{break};if($res -eq $null){break};"
                "if($res.Text){$texts += $res.Text}};"
                "$r.Dispose();$texts -join ' '"
            )
            text, err = self._powershell(script, {"AIQQ_STT_WAV": wav_path})
            if err and not text:
                raise VoiceError("语音识别失败：%s" % err[-300:])
            return re.sub(r"\s+", " ", text).strip()[:self.max_chars]
        finally:
            for path in (source, wav_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    @staticmethod
    def _voice_number(value, default=0):
        match = re.search(r"[+-]?\d+", str(value or ""))
        return int(match.group()) if match else default

    def _instruct_profile(self, instruct):
        """Edge 兜底：把自然语言描述粗略映射为语速/音高/音量偏移。"""
        text = str(instruct or "")
        rate = pitch = volume = 0
        if any(word in text for word in ("焦急", "着急", "催促", "生气", "愤怒", "激动", "兴奋")):
            rate += 22
            pitch += 6
            volume += 10
        if any(word in text for word in ("撒娇", "稚嫩", "萝莉", "卖萌", "偏高", "黏人")):
            rate += 8
            pitch += 8
            volume += 4
        if any(word in text for word in ("懒散", "困倦", "敷衍", "慢悠悠", "低沉", "疲惫", "刚睡醒")):
            rate -= 20
            pitch -= 6
            volume -= 8
        if any(word in text for word in ("温柔", "轻声", "小声", "耳语")):
            volume -= 8
        return rate, pitch, volume

    def is_busy(self):
        with self._active_lock:
            return self._active > 0

    def qwen_health(self):
        if not self.qwen_enabled:
            return False
        try:
            with urllib.request.urlopen(self.qwen_url + "/health", timeout=2) as response:
                return response.status == 200
        except Exception:
            return False

    def ensure_qwen_server(self):
        if not self.qwen_enabled:
            return False
        if self.qwen_health():
            return True
        with self._qwen_lock:
            if self.qwen_health():
                return True
            cmd = self.qwen_server_cmd
            if not cmd or not os.path.isfile(cmd):
                log.warning("[语音] 找不到 Qwen TTS 启动脚本：%s", cmd or "(空)")
                return False
            log.info("[语音] Qwen TTS 模型已卸载，正在重新启动服务：%s", cmd)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._qwen_process = subprocess.Popen(
                    ["cmd.exe", "/c", cmd],
                    cwd=os.path.dirname(cmd) or BASE_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("[语音] 启动 Qwen TTS 服务失败：%s", exc)
                return False
            deadline = time.time() + self.qwen_start_timeout
            while time.time() < deadline:
                if self.qwen_health():
                    log.info("[语音] Qwen TTS 服务已就绪")
                    return True
                time.sleep(1.0)
            log.warning("[语音] Qwen TTS 服务启动超时（%d 秒）", self.qwen_start_timeout)
            return False

    def unload_qwen_server(self):
        """停止 TTS 服务进程，释放语音模型显存/内存。"""
        if not self.qwen_enabled:
            return False
        with self._qwen_lock:
            if self.is_busy():
                return False
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", "tts-server.exe"],
                    capture_output=True, timeout=15, creationflags=flags)
            except Exception as exc:  # noqa: BLE001
                log.warning("[语音] 卸载 Qwen TTS 失败：%s", exc)
                return False
            self._qwen_process = None
            if result.returncode == 0:
                log.info("[语音] 语音生成已空闲，已卸载 Qwen TTS 模型")
                return True
            return False

    def _qwen_synthesize(self, text, instruct):
        """调用独立 qwentts.cpp Vulkan 服务，返回 (wav_path, duration)。"""
        payload = {
            "model": self.qwen_model,
            "input": text,
            "voice": "",
            "instructions": instruct or "保持同一角色声线：稚嫩偏高的少女声，像小孩子撒娇但带一点温柔，语速稍慢",
            "language": self.qwen_language,
            "response_format": "wav",
        }
        request = urllib.request.Request(
            self.qwen_url + "/v1/audio/speech",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        path = os.path.join(self.cache_dir, "qwen_%s.wav" % time.time_ns())
        try:
            with urllib.request.urlopen(request, timeout=self.qwen_timeout) as response:
                raw = response.read()
        except Exception as exc:  # noqa: BLE001
            raise VoiceError("Qwen TTS 请求失败：%s" % exc)
        if len(raw) < 1024 or not raw.startswith(b"RIFF"):
            raise VoiceError("Qwen TTS 未返回有效 WAV")
        with open(path, "wb") as fh:
            fh.write(raw)
        try:
            with wave.open(path, "rb") as wav:
                duration = wav.getnframes() / float(wav.getframerate() or 1)
        except Exception as exc:  # noqa: BLE001
            self.cleanup(path)
            raise VoiceError("Qwen TTS WAV 读取失败：%s" % exc)
        # NapCat/QQ 对 WAV 语音兼容性差，转成 Edge 同样的 MP3 再发。
        ffmpeg = self._ffmpeg()
        if ffmpeg:
            mp3 = os.path.join(self.cache_dir, "qwen_%s.mp3" % time.time_ns())
            try:
                result = subprocess.run(
                    [ffmpeg, "-y", "-i", path, "-codec:a", "libmp3lame",
                     "-b:a", "64k", mp3],
                    capture_output=True, timeout=60)
                if result.returncode == 0 and os.path.isfile(mp3):
                    self.cleanup(path)
                    return mp3, duration
            except Exception:  # noqa: BLE001
                pass
            self.cleanup(mp3)
        return path, duration

    def synthesize(self, text, rate=None, pitch=None, volume=None, instruct=None):
        with self._active_lock:
            self._active += 1
        try:
            return self._synthesize_inner(text, rate=rate, pitch=pitch, volume=volume, instruct=instruct)
        finally:
            with self._active_lock:
                self._active -= 1

    def _synthesize_inner(self, text, rate=None, pitch=None, volume=None, instruct=None):
        if not self.enabled or not self.tts_enabled:
            raise VoiceError("语音合成未启用")
        text = str(text or "").strip()[:self.max_chars]
        if not text:
            raise VoiceError("语音内容为空")
        if self.qwen_enabled:
            if self.ensure_qwen_server():
                try:
                    return self._qwen_synthesize(text, instruct)
                except Exception as exc:  # noqa: BLE001
                    log.warning("[语音] Qwen 服务不可用，回退 Edge：%s", exc)
            else:
                log.warning("[语音] Qwen TTS 服务未就绪，回退 Edge")
        delta_rate, delta_pitch, delta_volume = self._instruct_profile(instruct)
        if rate is None:
            rate = "%+d%%" % max(-50, min(100, self._voice_number(self.rate) + delta_rate))
        else:
            rate = "%+d%%" % int(rate)
        if pitch is None:
            pitch = "%+dHz" % max(-50, min(50, self._voice_number(self.pitch) + delta_pitch))
        else:
            pitch = "%+dHz" % int(pitch)
        if volume is None:
            volume = "%+d%%" % max(-50, min(100, self._voice_number(self.volume) + delta_volume))
        else:
            volume = "%+d%%" % int(volume)
        try:
            import edge_tts
        except Exception as exc:  # noqa: BLE001
            raise VoiceError("edge-tts 未安装：%s" % exc)
        path = os.path.join(self.cache_dir, "tts_%s.mp3" % time.time_ns())

        async def save():
            communicate = edge_tts.Communicate(
                text, self.voice, rate=rate, volume=volume,
                pitch=pitch)
            await communicate.save(path)

        try:
            asyncio.run(save())
        except Exception as exc:  # noqa: BLE001
            raise VoiceError("语音合成失败：%s" % exc)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise VoiceError("语音合成没有生成文件")
        duration = 0.0
        try:
            from mutagen.mp3 import MP3
            duration = float(MP3(path).info.length)
        except Exception:  # noqa: BLE001
            duration = max(1.0, len(text) / 4.5)
        return path, duration

    def cleanup(self, path):
        try:
            os.remove(path)
        except OSError:
            pass


VOICE_START_RE = r"/\s*VOICE"
VOICE_MARKER_RE = re.compile(
    VOICE_START_RE +
    r"\s*(?:[：:]\s*([^/\r\n]*?))?"
    r"\s*(?:/|(?=\r?\n|$))",
    re.IGNORECASE,
)
_LOOSE_VOICE_MARKER_RE = re.compile(
    r"(?:[\[【(（]\s*)?VOICE\s*(?:[：:]\s*[^\]】)）／/\r\n]*?)?\s*(?:[\]】)）])",
    re.IGNORECASE,
)
_VOICE_OPEN_LINE_RE = re.compile(r"/\s*VOICE[^\r\n]*(?:\r?\n|$)", re.IGNORECASE)


VOICE_TAIL_START_RE = re.compile(VOICE_START_RE, re.IGNORECASE)


def _strip_tail_in_segment(segment):
    """删除单段尾部没有实际正文的语音提示，且不继承为下一段语气。"""
    raw = str(segment or "")
    matches = list(VOICE_TAIL_START_RE.finditer(raw))
    if not matches:
        return raw
    # 从最后一个 VOICE 开始检查；只有它延伸到段尾且后面没有正文时才算尾部提示。
    for match in reversed(matches):
        before = raw[:match.start()]
        candidate = raw[match.start():].lstrip("\x00 \t\r\n")
        complete = VOICE_MARKER_RE.match(candidate)
        if complete is not None:
            rest = candidate[complete.end():].strip()
            if before.strip():
                if rest:
                    # 段中标记后仍有正文，保持原样。
                    return raw
                return before.rstrip()
            if rest:
                # 段首标记 + 正文，正常语音标记。
                return raw
            return ""
        # 没有闭合的语音提示：只丢弃第一行提示，保留后续正文。
        first_line, sep, rest = candidate.partition("\n")
        if sep and rest.strip():
            prefix = before.rstrip()
            return (prefix + "\n" + rest.lstrip("\r")) if prefix else rest
        if before.strip():
            return before.rstrip()
        return ""
    return raw


def _strip_tail_voice_prompts(text, split_marker="/Enter/"):
    """按段清理尾部 VOICE 提示；段间标记保留，供下一段继承语气。"""
    raw = str(text or "")
    marker = str(split_marker or "")
    if not marker:
        return _strip_tail_in_segment(raw)
    parts = raw.split(marker)
    cleaned = []
    for index, part in enumerate(parts):
        # 只保留“段首且只含标记”的段间 /VOICE，供下一段继承语气；
        # 正文末尾的 /VOICE 仍然视为无效尾部提示。
        stripped = part.lstrip("\x00 \t\r\n")
        marker_only = VOICE_MARKER_RE.match(stripped)
        if (index < len(parts) - 1 and marker_only
                and not stripped[marker_only.end():].strip()):
            cleaned.append(part)
        else:
            cleaned.append(_strip_tail_in_segment(part))
    return marker.join(cleaned)


def parse_voice_marker(text, split_marker="/Enter/"):
    """解析 /VOICE:语气描述/，返回 (是否请求, 清理文本, 参数)。"""
    raw = _strip_tail_voice_prompts(str(text or ""), split_marker)
    match = VOICE_MARKER_RE.search(raw)
    requested = bool(match)
    params = {}
    if match:
        description = (match.group(1) or "").strip().rstrip("]】")
        # 兼容旧接口 /VOICE:语速:音高:音量/。
        legacy = re.fullmatch(
            r"([+-]?\d+)\s*[：:]\s*([+-]?\d+)\s*[：:]\s*([+-]?\d+)",
            description)
        if legacy:
            rate = max(-50, min(100, int(legacy.group(1))))
            pitch = max(-50, min(50, int(legacy.group(2))))
            volume = max(-50, min(100, int(legacy.group(3))))
            params = {"rate": rate, "pitch": pitch, "volume": volume, "instruct": ""}
        elif description:
            params = {"instruct": description}
    cleaned = VOICE_MARKER_RE.sub(" ", raw)
    cleaned = _LOOSE_VOICE_MARKER_RE.sub(" ", cleaned)
    # 未闭合的 /VOICE:描述 只删除标记行，保留后面的正文。
    cleaned = _VOICE_OPEN_LINE_RE.sub("\n", cleaned)
    return requested, re.sub(r"[ \t]{2,}", " ", cleaned).strip(), params


def has_voice_marker(text, split_marker="/Enter/"):
    raw = _strip_tail_voice_prompts(str(text or ""), split_marker)
    return bool(VOICE_MARKER_RE.search(raw))


def _consume_voice_marker(text):
    """只消费文本开头的 /VOICE.../，返回 (描述, 剩余文本)；无标记返回 (None, 原文)。"""
    raw = str(text or "")
    stripped = raw.lstrip("\x00 \t\r\n")
    match = VOICE_MARKER_RE.match(stripped)
    if not match:
        return None, raw
    description = (match.group(1) or "").strip().rstrip("]】")
    return description, stripped[match.end():].lstrip()


def _normalize_voice_instruct(description):
    """旧数字接口转换成 Qwen 可理解的自然语言语气。"""
    text = str(description or "").strip()
    legacy = re.fullmatch(
        r"([+-]?\d+)\s*[：:]\s*([+-]?\d+)\s*[：:]\s*([+-]?\d+)", text)
    if not legacy:
        return text
    rate = max(-50, min(100, int(legacy.group(1))))
    pitch = max(-50, min(50, int(legacy.group(2))))
    volume = max(-50, min(100, int(legacy.group(3))))
    parts = []
    if rate >= 15:
        parts.append("语速偏快")
    elif rate <= -15:
        parts.append("语速偏慢")
    if pitch >= 8:
        parts.append("音调偏高")
    elif pitch <= -8:
        parts.append("音调偏低")
    if volume >= 10:
        parts.append("音量偏大")
    elif volume <= -10:
        parts.append("音量偏小")
    return "，".join(parts) or "自然平稳的年轻女声"


def split_voice_units(text, split_marker, slash_fallback=True):
    from .reply import split_by_marker

    """
    按 /Enter/ 和句首 /VOICE:描述/ 拆分语音单元。

    返回 [(文本, 语气描述), ...]。没有新标记的句子继承上一句描述；
    当前回复结束后状态丢弃，不写入上下文或跨消息缓存。
    """
    text = _strip_tail_voice_prompts(text, split_marker)
    units = []
    current = ""
    for part in split_by_marker(
            text, split_marker,
            bool(slash_fallback) and not has_voice_marker(text, split_marker)):
        position = 0
        for match in VOICE_MARKER_RE.finditer(part):
            body = part[position:match.start()].strip()
            if body:
                units.append((body, current))
            description = (match.group(1) or "").strip().rstrip("]】")
            current = _normalize_voice_instruct(description) if description else ""
            position = match.end()
        tail = part[position:].strip()
        if tail:
            units.append((tail, current))
    return units


def strip_voice_marker(text, split_marker="/Enter/"):
    """兼容旧调用：删除 /VOICE.../ 标记，返回 (是否请求, 清理文本)。"""
    requested, cleaned, _params = parse_voice_marker(text, split_marker)
    return requested, cleaned

__all__ = [name for name in globals() if not name.startswith("__")]
