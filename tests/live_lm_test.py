#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真实 LM Studio 联调测试（可选）：
  使用本机 127.0.0.1:1234 的 LM Studio 生成回复，
  NapCat 侧用假 OneBot 服务代替（避免真实 QQ 登录）。

前置条件：LM Studio 已加载模型并启动 Local Server。
运行：python tests\live_lm_test.py
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from mock_napcat import MockOneBot, make_group_event, post_event  # noqa: E402

LM_BASE = os.environ.get("AIQQ_LIVE_LM", "http://127.0.0.1:1234/v1")


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def get_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    onebot = MockOneBot().start()
    tmpdir = tempfile.mkdtemp(prefix="aiqq-live-")
    webhook_port = free_port()
    config_path = os.path.join(tmpdir, "config.json")
    config = {
        "qq": {"self_id": 0, "groups": [888888], "ignore_users": []},
        "onebot": {
            "api_url": "http://127.0.0.1:%d" % onebot.port,
            "token": "",
            "webhook_host": "127.0.0.1",
            "webhook_port": webhook_port,
            "webhook_path": "/onebot",
            "webhook_secret": "",
        },
        "lm_studio": {
            "base_url": LM_BASE,
            "model": "",
            "temperature": 0.7,
            "max_tokens": 4096,
            "timeout_sec": 240,
            "context_messages": 6,
        },
        "trigger": {"keywords": ["助手"], "regex": "", "match_mention": True},
        "reply": {"to_message": True, "max_chars": 400, "max_chunks": 2, "chunk_delay_sec": 0.2},
        "rate_limit": {"min_interval_sec": 0, "per_user_cooldown_sec": 0, "max_replies_per_minute": 10},
        "logging": {"level": "INFO", "dir": os.path.join(tmpdir, "logs")},
    }
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)

    log_path = os.path.join(tmpdir, "bot.log")
    log_file = open(log_path, "w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "bot.py"), "--config", config_path],
        cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT, env=env,
    )

    try:
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                health = get_json("http://127.0.0.1:%d/healthz" % webhook_port)
                break
            except Exception:
                if proc.poll() is not None:
                    raise SystemExit("bot.py 提前退出，日志：\n" + read_log(log_path))
                time.sleep(0.2)
        else:
            raise SystemExit("bot.py 未能启动，日志：\n" + read_log(log_path))

        print("等待真实模型生成回复（首次可能需要加载模型，最长 240 秒）…")
        post_event(
            "http://127.0.0.1:%d/onebot" % webhook_port,
            make_group_event(7001, 33333, [{"type": "text", "data": {
                "text": "助手你好，请用一句话介绍你自己"}}], nickname="联调用户"),
        )

        deadline = time.time() + 240
        while time.time() < deadline and not onebot.sends:
            time.sleep(0.3)
        if not onebot.sends:
            raise SystemExit("超时未收到回复，日志：\n" + read_log(log_path))

        reply = onebot.sends[0]["message"][-1]["data"]["text"]
        health = get_json("http://127.0.0.1:%d/healthz" % webhook_port)
        print("-" * 60)
        print("模型：%s" % health.get("model"))
        print("回复：%s" % reply)
        print("统计：%s" % json.dumps({k: health.get(k) for k in ("triggers", "replies", "errors")},
                                     ensure_ascii=False))
        print("-" * 60)
        if not reply.strip():
            raise SystemExit("模型返回了空回复。")
        if "[CQ:" in reply:
            raise SystemExit("回复中不应包含 CQ 码。")
        print("LIVE TEST OK")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            log_file.flush()
            log_file.close()
        except Exception:
            pass
        onebot.stop()


def read_log(path, lines=40):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "\n".join(fh.read().splitlines()[-lines:])
    except OSError:
        return "(无日志)"


if __name__ == "__main__":
    sys.exit(main())
