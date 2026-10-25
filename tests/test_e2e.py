#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端测试：
  启动假的 LM Studio + 假的 NapCat OneBot 服务 + 真实的 bot.py 子进程，
  通过 Webhook 推送群消息事件，验证触发、忽略、限流、上下文、分条与故障处理。
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mock_lm_studio import MockLMStudio  # noqa: E402
from mock_napcat import MockOneBot, make_group_event, post_event  # noqa: E402


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def get_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class E2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="aiqq-e2e-")
        cls.lm = MockLMStudio().start()
        cls.onebot = MockOneBot().start()
        now = int(time.time())
        cls.onebot.history = [
            {
                "message_id": 4001,
                "user_id": 30001,
                "time": now - 120,
                "sender": {"nickname": "老王", "card": ""},
                "message": [{"type": "text", "data": {"text": "今天群里好热闹"}}],
            },
            {
                "message_id": 4002,
                "user_id": 10001,
                "time": now - 60,
                "sender": {"nickname": "测试机器人", "card": ""},
                "message": [{"type": "text", "data": {"text": "大家好呀"}}],
            },
        ]

        cls.webhook_port = free_port()
        cls.config_path = os.path.join(cls.tmpdir, "config.json")
        cls.config = {
            "qq": {"self_id": 10001, "groups": [888888], "ignore_users": [20009]},
            "onebot": {
                "api_url": "http://127.0.0.1:%d" % cls.onebot.port,
                "token": "",
                "webhook_host": "127.0.0.1",
                "webhook_port": cls.webhook_port,
                "webhook_path": "/onebot",
                "webhook_secret": "",
            },
            "lm_studio": {
                "base_url": "http://127.0.0.1:%d/v1" % cls.lm.port,
                "model": "",
                "temperature": 0.5,
                "max_tokens": 256,
                "timeout_sec": 30,
                "context_messages": 6,
                "context_max_chars": 2000,
                "context_max_age_min": 30,
                "context_save_dir": os.path.join(cls.tmpdir, "context"),
                "sleep_state_file": os.path.join(cls.tmpdir, "sleep_state.json"),
            },
            "trigger": {"keywords": ["助手"], "regex": "", "match_mention": True},
            "reply": {
                "to_message": False,
                "max_chars": 300,
                "max_chunks": 3,
                "max_messages": 6,
                "split_marker": "/Enter/",
                "chunk_delay_sec": 0.05,
                "chunk_delay_max_sec": 0.05,
                "chunk_chars_per_sec_min": 1000.0,
                "chunk_chars_per_sec_max": 1000.0,
                "skip_markers": ["[SKIP]"],
                "error_reply": "",
            },
            "rate_limit": {
                "min_interval_sec": 0.3,
                "per_user_cooldown_sec": 6,
                "max_replies_per_minute": 20,
            },
            "logging": {"level": "INFO", "dir": os.path.join(cls.tmpdir, "logs")},
        }
        with open(cls.config_path, "w", encoding="utf-8") as fh:
            json.dump(cls.config, fh, ensure_ascii=False, indent=2)

        cls.bot_log_path = os.path.join(cls.tmpdir, "bot-output.log")
        cls.bot_log = open(cls.bot_log_path, "w", encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "bot.py"), "--config", cls.config_path],
            cwd=ROOT,
            stdout=cls.bot_log,
            stderr=subprocess.STDOUT,
            env=env,
        )

        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                get_json("http://127.0.0.1:%d/healthz" % cls.webhook_port)
                return
            except Exception:
                if cls.proc.poll() is not None:
                    break
                time.sleep(0.1)
        cls.tearDownClass()
        raise AssertionError("bot.py 未能启动，日志：\n" + cls.read_log())

    @classmethod
    def tearDownClass(cls):
        proc = getattr(cls, "proc", None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        log_file = getattr(cls, "bot_log", None)
        if log_file is not None:
            try:
                log_file.flush()
                log_file.close()
            except Exception:
                pass
        for name in ("lm", "onebot"):
            mock = getattr(cls, name, None)
            if mock is not None:
                mock.stop()
        print("\n--- bot.py 日志（末尾 40 行）---")
        print(cls.read_log(40))
        print("--- 日志结束 ---\n")

    @classmethod
    def read_log(cls, lines=60):
        try:
            with open(cls.bot_log_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read().splitlines()
            return "\n".join(content[-lines:])
        except OSError:
            return "(无日志)"

    # ------------------------------------------------------------------ #
    def webhook_url(self):
        return "http://127.0.0.1:%d/onebot" % self.webhook_port

    def post(self, event):
        status = post_event(self.webhook_url(), event)
        self.assertEqual(status, 200)

    def health(self):
        return get_json("http://127.0.0.1:%d/healthz" % self.webhook_port)

    def wait_for(self, predicate, timeout=6.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_end_to_end_flow(self):
        sends = self.onebot.snapshot()

        # 1. 非目标群：不处理
        self.post(make_group_event(5001, 20001, [{"type": "text", "data": {"text": "助手你好"}}],
                                   group_id=999999))
        time.sleep(0.8)
        self.assertEqual(len(self.onebot.snapshot()), len(sends))

        # 2. 目标群但内容不涉及机器人：不回复
        self.post(make_group_event(5002, 20001, [{"type": "text", "data": {"text": "今天天气不错"}}]))
        time.sleep(0.8)
        self.assertEqual(len(self.onebot.snapshot()), len(sends))

        # 3. 关键词触发：回复 + 引用原消息 + 带上下文
        self.post(make_group_event(5003, 20001, [{"type": "text", "data": {"text": "助手你好"}}],
                                   nickname="小明"))
        self.assertTrue(self.wait_for(lambda: len(self.onebot.snapshot()) >= len(sends) + 1),
                        "关键词触发后没有收到回复")
        first = self.onebot.snapshot()[len(sends)]
        self.assertEqual(int(first["group_id"]), 888888)
        segments = first["message"]
        self.assertEqual([segment["type"] for segment in segments], ["text"])
        first_text = segments[0]["data"]["text"]
        self.assertIn("收到", first_text)

        request = self.lm.last_request
        self.assertIsNotNone(request)
        messages = request["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("测试群", messages[0]["content"])
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn("助手你好", messages[-1]["content"])
        history_text = "\n".join(m["content"] for m in messages[1:-1])
        self.assertEqual(history_text, "")

        # 等待本轮任务释放
        self.assertTrue(self.wait_for(lambda: self.health().get("replies", 0) >= 1))
        time.sleep(0.4)

        # 4. 同一用户冷却中：不再回复
        count_before = len(self.onebot.snapshot())
        self.post(make_group_event(5004, 20001, [{"type": "text", "data": {"text": "助手再说一句"}}]))
        time.sleep(1.2)
        self.assertEqual(len(self.onebot.snapshot()), count_before, "用户冷却未生效")

        # 5. 另一用户 @机器人：纯 @ 也能触发
        self.post(make_group_event(5005, 20002, [
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": " 在吗"}},
        ], nickname="小红"))
        self.assertTrue(self.wait_for(lambda: len(self.onebot.snapshot()) >= count_before + 1),
                        "@机器人后没有收到回复")
        count_before = len(self.onebot.snapshot())

        # 6. 忽略名单中的用户：不回复
        self.post(make_group_event(5006, 20009, [{"type": "text", "data": {"text": "助手在不在"}}]))
        time.sleep(0.8)
        self.assertEqual(len(self.onebot.snapshot()), count_before)

        # 7. 长回复：按 max_chars 分条，最多 max_chunks 条
        self.post(make_group_event(5007, 20003, [{"type": "text", "data": {"text": "助手来个长回复"}}],
                                   nickname="小刚"))
        self.assertTrue(self.wait_for(lambda: len(self.onebot.snapshot()) >= count_before + 3, timeout=8),
                        "长回复没有按预期分条")
        time.sleep(0.5)
        chunks = self.onebot.snapshot()[count_before:count_before + 3]
        self.assertEqual(len(chunks), 3)
        for item in chunks:
            text = item["message"][-1]["data"]["text"]
            self.assertLessEqual(len(text), 300)
        self.assertTrue(chunks[-1]["message"][-1]["data"]["text"].endswith("…"))

        count_before = len(self.onebot.snapshot())

        # 8. /Enter/ 显式分段：每一段作为一条独立消息发送
        self.post(make_group_event(5009, 20005, [{"type": "text", "data": {"text": "助手分条测试"}}],
                                   nickname="小丽"))
        self.assertTrue(self.wait_for(lambda: len(self.onebot.snapshot()) >= count_before + 3, timeout=8),
                        "/Enter/ 分段没有按预期发送")
        time.sleep(0.4)
        split_msgs = self.onebot.snapshot()[count_before:count_before + 3]
        split_texts = [item["message"][-1]["data"]["text"] for item in split_msgs]
        self.assertEqual(split_texts, ["第一条消息", "第二条消息", "第三条消息"])
        for item in split_msgs:
            self.assertEqual([segment["type"] for segment in item["message"]], ["text"])
        for text in split_texts:
            self.assertNotIn("Enter", text)

        count_before = len(self.onebot.snapshot())

        # 9. LM Studio 故障：不回消息、不崩溃、错误计数增加
        self.post(make_group_event(5010, 20004, [{"type": "text", "data": {"text": "助手 ERROR"}}]))
        time.sleep(3.0)
        self.assertEqual(len(self.onebot.snapshot()), count_before, "LM 故障时不应发送回复")
        stats = self.health()
        self.assertGreaterEqual(stats.get("errors", 0), 1)
        self.assertIsNone(self.proc.poll(), "bot.py 在 LM 故障后应继续运行")

        # 10. 健康检查与统计
        self.assertGreaterEqual(stats["replies"], 8)
        self.assertGreaterEqual(stats["triggers"], 5)
        self.assertEqual(stats["model"], "mock-model")
        self.assertEqual(stats["self_id"], 10001)


if __name__ == "__main__":
    unittest.main(verbosity=2)
