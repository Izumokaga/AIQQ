#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""消息解析、触发、限流、清洗与分条的单元测试。"""

import base64
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from datetime import datetime
from io import BytesIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot as aiqq  # noqa: E402


class MessageParseTests(unittest.TestCase):
    def test_array_segments(self):
        message = [
            {"type": "text", "data": {"text": "你好，"}},
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": " 小助手在吗"}},
            {"type": "image", "data": {"file": "a.png"}},
        ]
        text, mentioned = aiqq.message_to_text(message, 10001)
        self.assertTrue(mentioned)
        self.assertIn("@你", text)
        self.assertIn("小助手在吗", text)
        self.assertIn("[图片]", text)

    def test_other_at(self):
        message = [{"type": "at", "data": {"qq": "20002", "name": "小明"}},
                   {"type": "text", "data": {"text": "早"}}]
        text, mentioned = aiqq.message_to_text(message, 10001)
        self.assertFalse(mentioned)
        self.assertIn("@小明", text)

    def test_cq_string(self):
        text, mentioned = aiqq.cq_string_to_text(
            "[CQ:at,qq=10001] 小助手在吗 [CQ:face,id=1]", "10001")
        self.assertTrue(mentioned)
        self.assertIn("小助手在吗", text)
        self.assertIn("[表情]", text)

    def test_cq_escaped_text(self):
        text, mentioned = aiqq.cq_string_to_text("你好&#91;世界&#93;", "1")
        self.assertFalse(mentioned)
        self.assertEqual(text, "你好[世界]")

    def test_cq_reply_is_ignored(self):
        text, _ = aiqq.cq_string_to_text("[CQ:reply,id=123]收到", "1")
        self.assertEqual(text, "收到")

    def test_extract_reply_id_array(self):
        message = [
            {"type": "reply", "data": {"id": "456"}},
            {"type": "text", "data": {"text": "看图"}},
        ]
        self.assertEqual(aiqq.extract_reply_id(message), "456")

    def test_extract_reply_id_cq(self):
        self.assertEqual(aiqq.extract_reply_id("[CQ:reply,id=789]看图"), "789")
        self.assertEqual(aiqq.extract_reply_id("没有引用"), "")

    def test_extract_forward_segments_array(self):
        message = [{"type": "forward", "data": {"id": "forward-1"}}]
        forwards = aiqq.extract_forward_segments(message)
        self.assertEqual(len(forwards), 1)
        self.assertEqual(forwards[0]["id"], "forward-1")

    def test_extract_forward_segments_cq(self):
        forwards = aiqq.extract_forward_segments("[CQ:forward,id=forward-2]")
        self.assertEqual(len(forwards), 1)
        self.assertEqual(forwards[0]["id"], "forward-2")


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.matcher = aiqq.TriggerMatcher({
            "keywords": ["小助手", "Bot"],
            "match_mention": True,
            "regex": "",
        })

    def test_keyword(self):
        self.assertEqual(self.matcher.match("小助手你好", False), "关键词「小助手」")

    def test_keyword_casefold(self):
        self.assertEqual(self.matcher.match("hey BOT", False), "关键词「Bot」")

    def test_ascii_keyword_ignores_all_case_combinations(self):
        matcher = aiqq.TriggerMatcher({
            "keywords": ["ChatGPT", "GPT-4o"], "match_mention": False, "regex": "",
        })
        for text in ("chatgpt", "CHATGPT", "ChatGpt", "gpt-4o", "GPT-4O", "Gpt-4o"):
            self.assertIsNotNone(matcher.match(text, False), text)

    def test_regex_ignores_ascii_case(self):
        matcher = aiqq.TriggerMatcher({
            "keywords": [], "match_mention": False, "regex": r"gpt-4o",
        })
        self.assertIsNotNone(matcher.match("GPT-4O", False))
        self.assertIsNotNone(matcher.match("Gpt-4o", False))

    def test_mention(self):
        self.assertEqual(self.matcher.match("随便聊聊", True), "@了你")

    def test_no_match(self):
        self.assertIsNone(self.matcher.match("今天天气不错", False))

    def test_regex(self):
        matcher = aiqq.TriggerMatcher({"keywords": [], "match_mention": False, "regex": r"^/ai\s"})
        self.assertEqual(matcher.match("/ai 你好", False), "正则匹配")
        self.assertIsNone(matcher.match("你好", False))

    def test_blacklist_blocks_every_trigger_level(self):
        matcher = aiqq.TriggerMatcher({
            "keywords": ["小助手"], "match_mention": True,
            "regex": r"^/ai\s", "block_keywords": ["广告", "Spam"],
        })
        self.assertIsNone(matcher.match("小助手 广告", False))
        self.assertIsNone(matcher.match("广告 随便聊聊", True))
        self.assertIsNone(matcher.match("/ai 广告", False))
        self.assertIsNone(matcher.match("SPAM 小助手", False))
        self.assertEqual(matcher.match("小助手 正常", False), "关键词「小助手」")

    def test_followup_blacklist(self):
        now = [1000.0]
        tracker = aiqq.FollowupTracker({
            "followup_enabled": True,
            "followup_keywords": ["你"],
            "followup_window_sec": 30,
            "followup_extended_sec": 30,
            "followup_max_messages": 0,
            "followup_same_sender": True,
            "followup_any_message": True,
            "block_keywords": ["广告"],
        }, clock=lambda: now[0])
        tracker.open(1, 10, "关键词")
        self.assertIsNone(tracker.match(1, 10, "广告 你呢"))
        self.assertEqual(tracker.match(1, 10, "你呢"), "追问（关键词）")


class RateLimitTests(unittest.TestCase):
    def test_user_cooldown(self):
        limiter = aiqq.RateLimiter({
            "min_interval_sec": 0,
            "per_user_cooldown_sec": 30,
            "max_replies_per_minute": 10,
        })
        ok, _ = limiter.try_acquire(1, 2)
        self.assertTrue(ok)
        ok, why = limiter.try_acquire(1, 2)
        self.assertFalse(ok)
        self.assertIn("冷却", why)

    def test_min_interval_between_groups(self):
        limiter = aiqq.RateLimiter({
            "min_interval_sec": 30,
            "per_user_cooldown_sec": 0,
            "max_replies_per_minute": 10,
        })
        self.assertTrue(limiter.try_acquire(1, 2)[0])
        self.assertFalse(limiter.try_acquire(1, 3)[0])


class ReplyTextTests(unittest.TestCase):
    def test_clean(self):
        raw = "```python\nprint(1)\n```\n**你好** [CQ:image,file=x]"
        cleaned = aiqq.clean_reply(raw, ["[SKIP]"], 100)
        self.assertNotIn("```", cleaned)
        self.assertNotIn("[CQ:", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertIn("print(1)", cleaned)

    def test_skip_marker(self):
        self.assertEqual(aiqq.clean_reply("不想回答 [SKIP]", ["[SKIP]"], 100), "")

    def test_strip_leading_breaks(self):
        self.assertEqual(aiqq.strip_leading_breaks("/Enter/你好"), "你好")
        self.assertEqual(aiqq.strip_leading_breaks("\n/Enter/ <br> \\n[换行]你好"), "你好")
        self.assertEqual(aiqq.strip_leading_breaks("你好 /Enter/ 再见"), "你好 /Enter/ 再见")
        self.assertEqual(aiqq.strip_leading_breaks("／ENTER／你好"), "你好")

    def test_strip_trailing_breaks(self):
        self.assertEqual(aiqq.strip_trailing_breaks("你好 /Enter/"), "你好")
        self.assertEqual(aiqq.strip_trailing_breaks("你好 /Enter/ /Enter/ \n"), "你好")
        self.assertEqual(aiqq.strip_trailing_breaks("你好 /Enter/ 再见"), "你好 /Enter/ 再见")

    def test_strip_periods(self):
        self.assertEqual(aiqq.strip_periods("你好。今天。"), "你好今天")
        self.assertEqual(aiqq.strip_periods("你好.今天"), "你好今天")
        self.assertEqual(aiqq.strip_periods("Hello. 版本 3.14 example.com..."), "Hello 版本 3.14 example.com...")
        self.assertEqual(aiqq.strip_periods("省略号……保留"), "省略号……保留")
        self.assertEqual(aiqq.strip_periods("数字3."), "数字3")

    def test_strip_media_placeholders(self):
        self.assertEqual(aiqq.strip_media_placeholders("你好[图片]"), "你好")
        self.assertEqual(aiqq.strip_media_placeholders("这是[发送了图片]啊"), "这是啊")
        self.assertEqual(aiqq.strip_media_placeholders("[图片 肥鱼得意] 好看"), "好看")
        self.assertEqual(aiqq.strip_media_placeholders("[聊天记录]"), "")
        self.assertEqual(aiqq.strip_media_placeholders("【发送了图片】"), "")
        self.assertEqual(aiqq.strip_media_placeholders("前 /图片/ 后"), "前 后")
        self.assertEqual(aiqq.strip_media_placeholders("[IMG:]"), "")
        self.assertEqual(aiqq.strip_media_placeholders("[正常文本]"), "[正常文本]")
        self.assertEqual(aiqq.strip_media_placeholders("/Enter/"), "/Enter/")
        self.assertEqual(aiqq.strip_media_placeholders("[IMG:肥鱼得意]"), "")
        self.assertEqual(aiqq.strip_media_placeholders("[SAVIMG:名称:描述:90]"), "")

    def test_truncate(self):
        cleaned = aiqq.clean_reply("字" * 200, [], 50)
        self.assertLessEqual(len(cleaned), 50)
        self.assertTrue(cleaned.endswith("…"))

    def test_split_sentences(self):
        text = "这是一个句子。" * 40
        chunks = aiqq.split_text(text, 80, 3)
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 80)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(chunks[-1].endswith("…"))

    def test_short_text(self):
        self.assertEqual(aiqq.split_text("你好", 80, 3), ["你好"])

    def test_split_hard_cut(self):
        chunks = aiqq.split_text("a" * 250, 100, 3)
        self.assertEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 100)

    def test_split_by_marker(self):
        self.assertEqual(
            aiqq.split_by_marker("你好/Enter/今天怎么样", "/Enter/"),
            ["你好", "今天怎么样"],
        )

    def test_split_by_marker_variants(self):
        self.assertEqual(
            aiqq.split_by_marker("甲／Enter／乙／ENTER／丙", "/Enter/"),
            ["甲", "乙", "丙"],
        )
        self.assertEqual(
            aiqq.split_by_marker("甲 / Enter / 乙", "/Enter/"),
            ["甲", "乙"],
        )

    def test_split_by_marker_ignores_empty_parts(self):
        self.assertEqual(
            aiqq.split_by_marker("/Enter/甲/Enter//Enter/乙/Enter/", "/Enter/"),
            ["甲", "乙"],
        )

    def test_split_by_marker_disabled_or_absent(self):
        self.assertEqual(aiqq.split_by_marker("没有标记", ""), ["没有标记"])
        self.assertEqual(aiqq.split_by_marker("没有标记", "/Enter/"), ["没有标记"])
        self.assertEqual(aiqq.split_by_marker("", "/Enter/"), [])

    def test_cap_messages(self):
        messages = ["一", "二", "三", "四"]
        capped = aiqq.cap_messages(messages, 100, 3)
        self.assertEqual(len(capped), 3)
        self.assertEqual(capped[0], "一")
        self.assertIn("三", capped[-1])
        self.assertIn("四", capped[-1])
        self.assertEqual(aiqq.cap_messages(messages, 100, 6), messages)

    def test_split_slash_fallback_spaced(self):
        text = "那是 / 我这儿也没啥花钱的 / 主要是帮你解决问题 / 省得你多掏钱"
        self.assertEqual(
            aiqq.split_by_marker(text, "/Enter/", True),
            ["那是", "我这儿也没啥花钱的", "主要是帮你解决问题", "省得你多掏钱"],
        )

    def test_split_slash_fallback_no_spaces(self):
        text = "在的哈/我是小助手/没啥好介绍的"
        self.assertEqual(
            aiqq.split_by_marker(text, "/Enter/", True),
            ["在的哈", "我是小助手", "没啥好介绍的"],
        )

    def test_split_slash_fallback_is_safe(self):
        for text in ("https://example.com/a/b", "1 / 2", "他/她", "C:/Users/test"):
            self.assertEqual(aiqq.split_by_marker(text, "/Enter/", True), [text])

    def test_split_slash_fallback_can_be_disabled(self):
        text = "那是 / 我这儿 / 省得你多掏钱"
        self.assertEqual(aiqq.split_by_marker(text, "/Enter/", False), [text])

    def test_remove_linebreaks_keeps_enter_marker(self):
        text = "第一行\n第二行\r\n/Enter/\n第三行"
        cleaned = aiqq.remove_linebreaks(text)
        self.assertEqual(cleaned, "第一行 第二行 /Enter/ 第三行")
        self.assertEqual(aiqq.split_by_marker(cleaned, "/Enter/", True),
                         ["第一行 第二行", "第三行"])
        self.assertEqual(aiqq.remove_linebreaks("\n\r\n"), "")

    def test_ignore_comma_breaks(self):
        cases = [
            ("第一句，\n/Enter/第二句", "第一句，第二句"),
            ("第一句，/Enter/第二句", "第一句，第二句"),
            ("第一句，\n第二句", "第一句，第二句"),
            ("第一句， /Enter/ 第二句", "第一句，第二句"),
            ("第一句，\n/Enter/\n第二句", "第一句，第二句"),
            ("第一句, \n /Enter/ second", "第一句,second"),
        ]
        for source, expected in cases:
            self.assertEqual(aiqq.ignore_comma_breaks(source), expected)
        self.assertEqual(aiqq.ignore_comma_breaks("第一句/Enter/第二句"),
                         "第一句/Enter/第二句")

    def test_ignore_comma_break_keeps_later_marker(self):
        text = aiqq.ignore_comma_breaks("第一句，\n/Enter/第二句/Enter/第三句")
        self.assertEqual(text, "第一句，第二句/Enter/第三句")
        self.assertEqual(aiqq.split_by_marker(text, "/Enter/", True),
                         ["第一句，第二句", "第三句"])

    def test_segment_delay(self):
        class FixedRate:
            def uniform(self, low, high):
                return 4.0

        self.assertEqual(aiqq.segment_delay("", 1.0, 20.0, 3.0, 5.0, FixedRate()), 1.0)
        self.assertEqual(aiqq.segment_delay("短", 1.0, 20.0, 3.0, 5.0, FixedRate()), 1.0)
        self.assertEqual(aiqq.segment_delay("字" * 12, 1.0, 20.0, 3.0, 5.0, FixedRate()), 3.0)
        self.assertEqual(aiqq.segment_delay("字" * 12, 1.0, 20.0, 3.0, 5.0, FixedRate(), base_sec=2.0), 5.0)
        self.assertEqual(aiqq.segment_delay("字" * 200, 1.0, 20.0, 3.0, 5.0, FixedRate()), 20.0)

    def test_read_chunked_body(self):
        raw = b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
        self.assertEqual(aiqq._read_chunked(BytesIO(raw)), b"hello world")

    def test_read_chunked_with_extension(self):
        raw = b"5;foo=bar\r\nhello\r\n0\r\n\r\n"
        self.assertEqual(aiqq._read_chunked(BytesIO(raw)), b"hello")


class LMRetryTests(unittest.TestCase):
    def test_retry_when_reasoning_consumes_tokens(self):
        client = aiqq.LMStudioClient({
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "test-model",
            "temperature": 0.7,
            "top_p": 0.95,
            "max_tokens": 100,
            "timeout_sec": 5,
        })
        calls = []

        def fake_request(method, path, payload=None):
            calls.append(payload["max_tokens"])
            if len(calls) == 1:
                return {
                    "choices": [{
                        "message": {"content": "", "reasoning_content": "thinking"},
                        "finish_reason": "length",
                    }]
                }
            return {
                "choices": [{
                    "message": {"content": "最终回复"},
                    "finish_reason": "stop",
                }]
            }

        client._request = fake_request
        result = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result, "最终回复")
        self.assertEqual(calls, [100, 2048])
        self.assertEqual(client.max_tokens, 2048)

    def test_reasoning_effort_payload(self):
        client = aiqq.LMStudioClient({
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "test-model",
            "max_tokens": 100,
            "timeout_sec": 5,
            "reasoning_effort": "none",
        })
        seen = []

        def fake_request(method, path, payload=None):
            seen.append(payload)
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

        client._request = fake_request
        client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(seen[-1]["reasoning_effort"], "none")

        client.reasoning_effort = "auto"
        client.chat([{"role": "user", "content": "hi"}])
        self.assertNotIn("reasoning_effort", seen[-1])


class ImageInputTests(unittest.TestCase):
    def test_extract_array_image(self):
        message = [
            {"type": "text", "data": {"text": "看这个"}},
            {"type": "image", "data": {"url": "https://example.com/a.jpg", "file": "a.jpg"}},
        ]
        images = aiqq.extract_image_segments(message)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["url"], "https://example.com/a.jpg")
        self.assertEqual(images[0]["file"], "a.jpg")

    def test_extract_cq_image(self):
        message = "[CQ:image,file=a.jpg,url=https://example.com/a.jpg]"
        images = aiqq.extract_image_segments(message)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["url"], "https://example.com/a.jpg")
        self.assertEqual(images[0]["file"], "a.jpg")

    def test_image_bytes_to_data_url(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
        data_url = aiqq.image_bytes_to_data_url(png)
        self.assertTrue(data_url.startswith("data:image/png;base64,"))

    def test_read_local_image_and_limit(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            path = fh.name
        try:
            info = {"url": "", "file": path}
            self.assertTrue(aiqq.read_image_bytes(info, 1024).startswith(b"\x89PNG"))
            with self.assertRaises(aiqq.ImageInputError):
                aiqq.read_image_bytes(info, 8)
        finally:
            os.unlink(path)


class SenderFilterTests(unittest.TestCase):
    def test_keeps_sender_and_replied_user(self):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "lm_studio": {"context_sender_only": True, "context_include_bot": False},
        })
        b = aiqq.Bot(cfg)
        b.self_id = 999
        entries = [
            {"message_id": "1", "user_id": 1, "reply_to": "", "is_self": False,
             "content": "A1", "images": [], "forwards": []},
            {"message_id": "2", "user_id": 2, "reply_to": "", "is_self": False,
             "content": "B1", "images": [], "forwards": []},
            {"message_id": "3", "user_id": 1, "reply_to": "2", "is_self": False,
             "content": "A2", "images": [], "forwards": []},
            {"message_id": "4", "user_id": 3, "reply_to": "", "is_self": False,
             "content": "C1", "images": [], "forwards": []},
            {"message_id": "5", "user_id": 999, "reply_to": "", "is_self": True,
             "content": "bot", "images": [], "forwards": []},
        ]
        entry = {"mentioned": True, "user_id": 1, "reply_to": ""}
        filtered = b._filter_context_by_sender(entry, entries)
        self.assertEqual([item["message_id"] for item in filtered], ["1", "2", "3"])


class PersonaTests(unittest.TestCase):
    def setUp(self):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "lm_studio": {
                "system_prompt": "完整人设",
                "persona_file": "",
                "persona_short_prompt": "简短提示",
                "persona_mode": "adaptive",
                "persona_keywords": ["大肥鱼", "你是谁"],
            },
        })
        self.bot = aiqq.Bot(cfg)
        self.bot.self_nick = "测试"

    def test_adaptive_uses_short_prompt_for_unrelated_chat(self):
        prompt = self.bot._system_prompt(1, "今天天气不错")
        self.assertTrue(prompt.startswith("简短提示"))
        self.assertNotIn("保持人设中的身份", prompt)

    def test_adaptive_uses_full_persona_for_related_chat(self):
        prompt = self.bot._system_prompt(1, "你是谁")
        self.assertTrue(prompt.startswith("简短提示"))
        self.assertIn("[完整人设]", prompt)
        self.assertIn("完整人设", prompt)
        self.assertIn("保持人设中的身份", prompt)

    def test_reply_target_instruction_limits_to_trigger(self):
        note = self.bot._reply_target_instruction(
            {"nickname": "小明", "user_id": 12345})
        self.assertIn("本轮只回复触发者 小明（QQ12345）", note)
        self.assertIn("其他群成员的消息只作为参考", note)
        self.assertIn("不要回复、点名或替其他成员发言", note)

    def test_group_persona_always_and_global_full_after_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {
                    "context_save_dir": os.path.join(tmp, "context"),
                    "system_prompt": "全局完整人设",
                    "persona_file": "",
                    "persona_short_prompt": "简短提示",
                    "persona_mode": "adaptive",
                    "persona_keywords": ["你是谁"],
                },
            })
            bot = aiqq.Bot(cfg)
            bot.group_persona.set_active_preset("测试预设")
            bot.group_persona.write(1, "群聊内人设")
            short = bot._system_prompt(1, "普通聊天")
            self.assertTrue(short.startswith("简短提示"))
            self.assertIn("[群聊人设]\n群聊内人设", short)
            self.assertNotIn("[完整人设]", short)
            full = bot._system_prompt(1, "你是谁")
            self.assertTrue(full.startswith("简短提示"))
            self.assertIn("[群聊人设]\n群聊内人设", full)
            self.assertIn("[完整人设]\n全局完整人设", full)
            self.assertLess(full.index("[完整人设]"), full.index("[群聊人设]"))

    def test_persona_modes(self):
        self.bot.cfg["lm_studio"]["persona_mode"] = "always"
        always_prompt = self.bot._system_prompt(2, "普通聊天")
        self.assertTrue(always_prompt.startswith("简短提示"))
        self.assertIn("[完整人设]", always_prompt)
        self.bot.cfg["lm_studio"]["persona_mode"] = "off"
        self.assertTrue(self.bot._system_prompt(2, "你是谁").startswith("简短提示"))


class ImageLibraryTests(unittest.TestCase):
    def test_sanitize_name(self):
        self.assertEqual(aiqq.sanitize_image_name("  红蓝/方块.png "), "红蓝方块")

    def test_parse_image_markers(self):
        text, names = aiqq.parse_image_markers("看图/IMG:红蓝方块/ 好看")
        self.assertNotIn("IMG:", text)
        self.assertEqual(names, ["红蓝方块"])
        text, names = aiqq.parse_save_markers("收下/SAVIMG:红蓝方块/")
        self.assertNotIn("SAVIMG", text)
        self.assertEqual(names, [{"position": None, "name": "红蓝方块", "description": "", "favorite": 50}])
        text, markers = aiqq.parse_save_markers("收下/SAVIMG:红蓝方块:红底蓝方块:88/")
        self.assertEqual(markers, [{"position": None, "name": "红蓝方块", "description": "红底蓝方块", "favorite": 88}])
        text, markers = aiqq.parse_save_markers(
            "/SAVIMG: 肥鱼得意 ： 蓝色大肥鱼叉腰 ： 150/")
        self.assertEqual(markers[0]["favorite"], 100)
        self.assertEqual(markers[0]["name"], "肥鱼得意")
        text, markers = aiqq.parse_save_markers("/SAVIMG:2:肥鱼干饭:蓝色大肥鱼在吃饭:95/")
        self.assertEqual(markers[0]["position"], 2)
        self.assertEqual(markers[0]["name"], "肥鱼干饭")
        self.assertEqual(markers[0]["favorite"], 95)
        text, markers = aiqq.parse_save_markers(
            "/SAVIMG:图片位于第1张:红眼激光金发震惊脸:模糊金发人物双眼发出红色激光，嘴巴微张一脸震惊，"
            "氛围感强烈，适合表达“被吓到”或“危险预警”的情绪场景:90/")
        self.assertNotIn("SAVIMG", text)
        self.assertEqual(markers[0]["position"], 1)
        self.assertEqual(markers[0]["name"], "红眼激光金发震惊脸")
        self.assertIn("危险预警", markers[0]["description"])
        self.assertEqual(markers[0]["favorite"], 90)
        text, markers = aiqq.parse_save_markers(
            "/SAVIMG:图片位于第2张:橙发女孩生气举棍.gif:橙发Q版少女满头大汗、愤怒握拳、"
            "举着长木板/木棍，表情夸张崩溃，配文“🇨🇳 ip敢靠近一棒子打死”，"
            "很有气势的吐槽表情包:95\n/Enter/")
        self.assertNotIn("SAVIMG", text)
        self.assertIn("/Enter/", text)
        self.assertFalse(aiqq.has_voice_marker(text))
        self.assertEqual(markers[0]["position"], 2)
        self.assertEqual(markers[0]["name"], "橙发女孩生气举棍")
        self.assertIn("长木板/木棍", markers[0]["description"])
        self.assertEqual(markers[0]["favorite"], 95)

    def test_save_find_and_dedupe(self):
        def make_png(width, height, payload=b""):
            ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
            return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13)
                    + b"IHDR" + ihdr + payload)

        png = make_png(10, 10, b"0")
        with tempfile.TemporaryDirectory() as tmp:
            library = aiqq.ImageLibrary(os.path.join(tmp, "img"))
            entry = library.save_bytes(png, "红蓝方块", "红底蓝方块", favorite=77)
            self.assertFalse(entry["duplicate"])
            self.assertEqual(entry["favorite"], 77)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "img", entry["file"])))
            self.assertTrue(library.path_for("红蓝方块"))
            self.assertIn("红蓝方块", library.catalog_text())
            with open(os.path.join(tmp, "image_index.txt"), encoding="utf-8") as fh:
                self.assertIn("红蓝方块：红底蓝方块（喜爱 77）", fh.read())

            duplicate = library.save_bytes(png, "另一个名字", "重复图片")
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["name"], "红蓝方块")

            different = library.save_bytes(make_png(20, 20, b"x"), "红蓝方块", "第二张")
            self.assertFalse(different["duplicate"])
            self.assertNotEqual(different["file"], entry["file"])
            self.assertEqual(different["file"], "红蓝方块_2.png")

            same_size = library.save_bytes(make_png(10, 10, b"new"), "红蓝方块", "同尺寸不同内容")
            self.assertFalse(same_size["duplicate"])
            self.assertNotEqual(same_size["file"], entry["file"])
            self.assertEqual(same_size["file"], "红蓝方块_3.png")
            with open(os.path.join(tmp, "img", entry["file"]), "rb") as fh:
                self.assertTrue(fh.read().endswith(b"0"))
            with open(os.path.join(tmp, "img", same_size["file"]), "rb") as fh:
                self.assertTrue(fh.read().endswith(b"new"))
            self.assertEqual(len(library.entries), 3)


class ImagePruneTests(unittest.TestCase):
    def test_prune_lowest_favorite(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        with tempfile.TemporaryDirectory() as tmp:
            library = aiqq.ImageLibrary(os.path.join(tmp, "img"), max_items=2)
            library.save_bytes(png + b"a", "低分", "喜欢低", favorite=20)
            library.save_bytes(png + b"b", "高分", "喜欢高", favorite=95)
            library.save_bytes(png + b"c", "中分", "喜欢中", favorite=60)
            self.assertEqual(len(library.entries), 2)
            self.assertFalse(library.path_for("低分"))
            self.assertTrue(library.path_for("高分"))
            self.assertTrue(library.path_for("中分"))


class PersonaFileTests(unittest.TestCase):
    def test_persona_file_overrides_system_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "persona.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("文件里的完整人设")
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "lm_studio": {
                    "system_prompt": "备用人设",
                    "persona_file": path,
                    "persona_short_prompt": "简短人设",
                    "persona_mode": "always",
                },
            })
            b = aiqq.Bot(cfg)
            b.self_nick = "测试"
            prompt = b._system_prompt(1, "普通聊天")
            self.assertIn("文件里的完整人设", prompt)
            self.assertNotIn("备用人设", prompt)


class FollowupTests(unittest.TestCase):
    def make_tracker(self, same_sender=True, max_messages=2, window=10, extended=None):
        now = [1000.0]
        tracker = aiqq.FollowupTracker({
            "followup_enabled": True,
            "followup_keywords": ["你", "肥鱼"],
            "followup_window_sec": window,
            "followup_extended_sec": window if extended is None else extended,
            "followup_max_messages": max_messages,
            "followup_same_sender": same_sender,
        }, clock=lambda: now[0])
        return tracker, now

    def test_followup_requires_primary_open_and_same_sender(self):
        tracker, now = self.make_tracker()
        self.assertIsNone(tracker.match(1, 10, "你"))
        tracker.open(1, 10, "关键词")
        self.assertEqual(tracker.match(1, 10, "你呢"), "追问（关键词）")
        self.assertIsNone(tracker.match(1, 11, "你呢"))
        now[0] += 11
        self.assertIsNone(tracker.match(1, 10, "你"))

    def test_any_message_without_keyword(self):
        tracker, _ = self.make_tracker()
        tracker.open(1, 10, "关键词")
        self.assertEqual(tracker.match(1, 10, "随便说一句"), "追问（关键词）")
        self.assertEqual(tracker.match(1, 10, "", has_content=True), "追问（关键词）")

    def test_zero_max_is_unlimited(self):
        tracker, now = self.make_tracker(max_messages=0, window=30)
        tracker.open(1, 10, "关键词")
        for _ in range(5):
            self.assertIsNotNone(tracker.match(1, 10, "继续"))
            tracker.consume(1)
            now[0] += 1
        self.assertIsNotNone(tracker.match(1, 10, "还在"))

    def test_other_primary_trigger_interrupts(self):
        tracker, _ = self.make_tracker()
        tracker.open(1, 10, "关键词")
        self.assertIsNotNone(tracker.match(1, 10, "第一句"))
        tracker.open(1, 11, "别人关键词")
        self.assertIsNone(tracker.match(1, 10, "我还在"))
        self.assertIsNotNone(tracker.match(1, 11, "换我"))

    def test_followup_consumes_and_expires(self):
        tracker, now = self.make_tracker(max_messages=1)
        tracker.open(1, 10, "关键词")
        self.assertIsNotNone(tracker.match(1, 10, "肥鱼呢"))
        tracker.consume(1)
        self.assertIsNone(tracker.match(1, 10, "你"))
        now[0] += 11
        self.assertIsNone(tracker.match(1, 10, "你"))

    def test_followup_window_starts_after_reply_finishes(self):
        tracker, now = self.make_tracker(window=10)
        tracker.open(1, 10, "关键词")
        now[0] += 9
        tracker.refresh_after_reply(1, 10)
        now[0] += 9
        self.assertIsNotNone(tracker.match(1, 10, "继续"))
        now[0] += 2
        self.assertIsNone(tracker.match(1, 10, "继续"))

    def test_followup_30_any_then_90_quote_or_keyword(self):
        tracker, now = self.make_tracker(window=30, extended=90)
        tracker.open(1, 10, "关键词")
        tracker.refresh_after_reply(1, 10)
        now[0] += 29
        self.assertEqual(tracker.match(1, 10, "随便"), "追问（关键词）")
        now[0] += 2
        self.assertIsNone(tracker.match(1, 10, "随便"))
        self.assertIsNotNone(tracker.match(1, 10, "你呢"))
        self.assertIsNotNone(tracker.match(1, 10, "", quoted=True))
        now[0] += 60
        self.assertIsNone(tracker.match(1, 10, "你呢"))
        self.assertIsNone(tracker.match(1, 10, "", quoted=True))

    def test_followup_other_sender_when_disabled(self):
        tracker, _ = self.make_tracker(same_sender=False)
        tracker.open(1, 10, "关键词")
        self.assertIsNotNone(tracker.match(1, 11, "你"))


class GroupContextStoreTests(unittest.TestCase):
    def test_one_context_file_per_group_and_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.GroupContextStore(tmp, max_entries=500, max_chars=10)
            self.assertEqual(store.append_exchange(123, "你好", "你好呀"), 5)
            self.assertEqual(os.path.basename(store.context_path(123)), "context.json")
            self.assertFalse(store.needs_consolidation(123))
            store.append_exchange(123, "字" * 600, "好的")
            self.assertTrue(store.needs_consolidation(123))
            self.assertEqual(len(os.listdir(os.path.join(tmp, "123"))), 1)

    def test_group_persona_ensure_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.GroupPersonaStore(tmp)
            self.assertEqual(store.set_active_preset("温柔"), "温柔")
            self.assertTrue(store.ensure(1, "温柔", "完整人设"))
            self.assertFalse(store.ensure(1, "温柔", "新内容"))
            self.assertEqual(store.read(1), "完整人设")
            self.assertEqual(store.active_preset(), "温柔")

class GeneralThinkingTests(unittest.TestCase):
    def test_general_thinking_switch_maps_to_reasoning_effort(self):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "lm_studio": {"general_deep_thinking": True},
        })
        bot = aiqq.Bot(cfg)
        self.assertEqual(bot._general_reasoning_effort(), "high")
        bot.cfg["lm_studio"]["general_deep_thinking"] = False
        self.assertEqual(bot._general_reasoning_effort(), "none")

class ContextMaintenanceTests(unittest.TestCase):
    def make_bot(self, tmp):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "qq": {"groups": [1], "admin_users": [99]},
            "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
        })
        bot = aiqq.Bot(cfg)
        bot.group_persona.set_active_preset("测试预设")
        bot.group_persona.write(1, "旧人设")
        bot._context_notify_done = lambda *args, **kwargs: None
        bot._context_send_failure = lambda *args, **kwargs: None
        bot._context_pause_queues = lambda: None
        return bot

    def test_context_consolidation_overwrites_persona_and_deletes_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.group_context.append_exchange(1, "旧上下文", "旧回复")
            bot.lm.chat = lambda *args, **kwargs: "整理后的新人设"
            bot._context_maintenance.set()
            bot._run_context_consolidation(1)
            self.assertEqual(bot.group_persona.read(1), "整理后的新人设")
            self.assertFalse(os.path.isfile(bot.group_context.context_path(1)))
            self.assertFalse(bot._context_maintenance.is_set())

    def test_context_prompt_requests_member_traits_without_injected_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.group_context.append_exchange(
                1, "小王说的话", "回复", user_name="小王")
            bot.group_context.append_exchange(
                1, "小李说的话", "回复", user_name="小李")
            calls = []
            bot.images.name_catalog_text = lambda *args, **kwargs: "[测试图:77]"
            bot.lm.chat = lambda messages, **kwargs: calls.append(messages) or "新人设"
            bot._context_maintenance.set()
            bot._run_context_consolidation(1)
            prompt = calls[0][0]["content"]
            self.assertIn("图库内容：\n[测试图:77]", prompt)
            self.assertIn("在人设最后附上对每个活跃群成员", prompt)
            self.assertIn("以个人为单位的用户特征总结", prompt)
            self.assertNotIn("活跃群成员：\n- 小王", prompt)
            self.assertNotIn("条消息", prompt)
            self.assertIn("小王说的话", prompt)
            self.assertIn("小李说的话", prompt)

    def test_context_output_over_2000_is_saved_completely(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.group_context.append_exchange(1, "旧上下文", "旧回复")
            bot.lm.chat = lambda *args, **kwargs: "长" * 2500
            bot._context_maintenance.set()
            bot._run_context_consolidation(1)
            saved = bot.group_persona.read(1)
            self.assertEqual(len(saved), 2500)

    def test_context_failure_blocks_group_after_five_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.group_context.append_exchange(1, "旧上下文", "旧回复")
            calls = []
            def fail(*args, **kwargs):
                calls.append(1)
                raise RuntimeError("模型崩溃")
            bot.lm.chat = fail
            bot._context_maintenance.set()
            bot._run_context_consolidation(1)
            self.assertEqual(len(calls), 5)
            self.assertIn(1, bot._context_disabled_groups)
            self.assertTrue(os.path.isfile(bot.group_context.context_path(1)))

class ManualContextMaintenanceTests(unittest.TestCase):
    def test_manual_consolidation_requires_context_and_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            bot = aiqq.Bot(cfg)
            self.assertFalse(bot.request_manual_context_consolidation(1)["ok"])
            bot.group_context.append_exchange(1, "上下文", "回复")
            started = []
            bot._start_context_consolidation = lambda group_id: started.append(group_id) or True
            result = bot.request_manual_context_consolidation(1)
            self.assertTrue(result["ok"])
            self.assertEqual(started, [1])

class ContextStoreTests(unittest.TestCase):
    def test_save_load_gzip_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"), maxlen=10, keep_files=5)
            memory = aiqq.GroupMemory(maxlen=10)
            memory.add(123, {"message_id": "m1", "text": "你好", "user_id": 1,
                             "nickname": "A", "time": 1000})
            path = store.save(123, memory.snapshot(123))
            self.assertTrue(os.path.basename(path).startswith("123_"))
            self.assertTrue(path.endswith(".json.gz"))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(2), b"\x1f\x8b")
            loaded = store.load_into(memory)
            self.assertEqual(loaded, {123: 1})
            self.assertTrue(memory.recent(123, 1)[0].get("restored"))

    def test_keeps_only_newest_files_and_loads_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"), maxlen=10, keep_files=2)
            for index in range(3):
                store.save(123, [{"message_id": "m%d" % index, "text": "版本%d" % index,
                                  "user_id": 1, "nickname": "A", "time": 1000 + index}])
            files = store._files_for_group(123)
            self.assertEqual(len(files), 2)
            loaded = store.load(123)
            self.assertEqual([item["text"] for item in loaded], ["版本0", "版本1", "版本2"])
            memory = aiqq.GroupMemory(maxlen=10)
            store.load_into(memory)
            self.assertEqual(memory.recent(123, 5)[-1]["text"], "版本2")

    def test_merges_older_context_and_ignores_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"), keep_files=5)
            def item(mid, text):
                return {"message_id": mid, "text": text, "user_id": 1,
                        "nickname": "A", "time": int(mid[1:])}
            store.save(123, [item("m1", "一"), item("m2", "二")])
            store.save(123, [item("m2", "二"), item("m3", "三")])
            store.save(123, [item("m3", "三"), item("m3", "三"), item("m4", "四")])
            self.assertEqual([entry["text"] for entry in store.load(123)],
                             ["一", "二", "三", "四"])

    def test_self_image_placeholder_is_not_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"))
            store.save(123, [
                {"message_id": "self1", "text": "[发送了图片]", "is_self": True,
                 "user_id": 999, "time": 1000},
                {"message_id": "u1", "text": "用户消息", "is_self": False,
                 "user_id": 1, "time": 1001},
            ])
            loaded = store.load(123)
            self.assertEqual([item["text"] for item in loaded], ["用户消息"])

    def test_identical_snapshot_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"), keep_files=5)
            entries = [{"message_id": "m1", "text": "你好", "user_id": 1,
                        "nickname": "A", "time": 1000}]
            first = store.save(123, entries)
            second = store.save(123, entries)
            self.assertEqual(first, second)
            self.assertEqual(len(store._files_for_group(123)), 1)

    def test_empty_context_removes_all_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"), keep_files=3)
            store.save(123, [{"message_id": "m1", "text": "你好", "time": 1}])
            store.save(123, [{"message_id": "m2", "text": "再见", "time": 2}])
            self.assertTrue(store.latest_path(123))
            store.save(123, [])
            self.assertFalse(store._files_for_group(123))

    def test_context_limit_compresses_adjacent_same_sender(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"), max_lines=3)
            entries = [{"message_id": "m%d" % i, "text": "第%d句" % i,
                        "user_id": 1, "nickname": "A", "time": 1000 + i}
                       for i in range(4)]
            fitted = store.fit_entries(entries)
            self.assertEqual(store.last_fit_action, "compressed")
            self.assertEqual(len(fitted), 1)
            self.assertLessEqual(store._line_count(fitted), 3)

    def test_context_limit_removes_oldest_when_cannot_compress(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"), max_lines=3)
            entries = [{"message_id": "m%d" % i, "text": "第%d句" % i,
                        "user_id": 1 + (i % 2), "nickname": "A", "time": 1000 + i}
                       for i in range(5)]
            fitted = store.fit_entries(entries)
            self.assertEqual(store.last_fit_action, "trimmed")
            self.assertEqual([item["message_id"] for item in fitted], ["m2", "m3", "m4"])
            self.assertLessEqual(store._line_count(fitted), 3)

    def test_saved_context_respects_line_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.ContextStore(os.path.join(tmp, "context"), max_lines=3)
            entries = [{"message_id": "m%d" % i, "text": "第%d句" % i,
                        "user_id": 1 + (i % 2), "nickname": "A", "time": 1000 + i}
                       for i in range(5)]
            store.save(123, entries)
            self.assertLessEqual(store._line_count(store.load(123)), 3)


class ImageSelectionTests(unittest.TestCase):
    def make_bot(self, tmp):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "qq": {"groups": [1]},
            "lm_studio": {"context_save_dir": os.path.join(tmp, "context"),
                          "image_user_messages": 5, "image_selected_max": 5},
        })
        return aiqq.Bot(cfg)

    @staticmethod
    def image(name):
        return {"url": "http://img/" + name, "file": name}

    def add_message(self, bot, user_id, message_id, images):
        bot.memory.add(1, {
            "message_id": message_id, "user_id": user_id,
            "text": "", "images": images, "forwards": [], "time": 0,
        })

    def test_no_quote_uses_last_five_trigger_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            for index in range(6):
                self.add_message(bot, 10, "m%d" % index, [self.image(str(index))])
            entry = {"user_id": 10, "reply_to": "", "images": [], "text": "看图"}
            selected = bot._select_input_images(entry, 1)
            self.assertEqual([item["file"] for item in selected],
                             ["1", "2", "3", "4", "5"])

    def test_quote_quoted_images_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            self.add_message(bot, 10, "t1", [self.image("t1")])
            self.add_message(bot, 10, "t2", [self.image("t2")])
            self.add_message(bot, 20, "q1", [self.image("q1")])
            self.add_message(bot, 20, "q2", [self.image("q2")])
            self.add_message(bot, 20, "q3", [self.image("q3")])
            entry = {"user_id": 10, "reply_to": "q3", "images": [], "text": "引用"}
            selected = bot._select_input_images(entry, 1)
            files = [item["file"] for item in selected]
            self.assertEqual(files[:3], ["q1", "q2", "q3"])
            self.assertEqual(files[3:], ["t1", "t2"])
            self.assertEqual(len(files), 5)

    def test_quote_more_than_five_keeps_last_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            for index in range(6):
                self.add_message(bot, 20, "q%d" % index, [self.image("q%d" % index)])
            entry = {"user_id": 10, "reply_to": "q5", "images": [], "text": "引用"}
            selected = bot._select_input_images(entry, 1)
            self.assertEqual([item["file"] for item in selected],
                             ["q1", "q2", "q3", "q4", "q5"])


class UserContextTests(unittest.TestCase):
    def test_user_context_shared_across_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.UserContextStore(tmp, keep_files=5, max_entries=500)
            store.append_exchange(1, 100, "你好", "你好呀")
            store.append_exchange(1, 200, "另一个群", "也记得")
            entries = store.messages(1)
            self.assertEqual([item["content"] for item in entries],
                             ["你好", "你好呀", "另一个群", "也记得"])
            self.assertTrue(os.path.isdir(os.path.join(tmp, "100", "1")))
            self.assertTrue(os.path.isdir(os.path.join(tmp, "200", "1")))

    def test_user_context_keeps_five_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.UserContextStore(tmp, keep_files=5, max_entries=500)
            for index in range(7):
                store.append_exchange(1, 100, "问%d" % index, "答%d" % index)
            files = os.listdir(os.path.join(tmp, "100", "1"))
            self.assertLessEqual(len([name for name in files if name.endswith(".json.gz")]), 5)

    def test_user_context_clear_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.UserContextStore(tmp, keep_files=5, max_entries=500)
            store.append_exchange(1, 100, "问", "答")
            self.assertTrue(store.messages(1))
            removed = store.clear_all()
            self.assertGreaterEqual(removed, 1)
            self.assertEqual(store.messages(1), [])
            self.assertEqual(store.count(), 0)

    def test_user_context_clear_user_and_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.UserContextStore(tmp, keep_files=5, max_entries=500)
            store.append_exchange(1, 100, "a", "b")
            store.append_exchange(1, 200, "c", "d")
            store.append_exchange(2, 100, "e", "f")
            self.assertGreaterEqual(store.clear_user(1), 1)
            self.assertEqual(store.messages(1), [])
            self.assertTrue(store.messages(2))
            self.assertGreaterEqual(store.clear_group(100), 1)
            self.assertEqual(store.messages(2), [])

    def test_user_context_overflow_drops_assistant_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = aiqq.UserContextStore(tmp, keep_files=5, max_entries=4)
            store.append_exchange(1, 100, "问一", "答一")
            store.append_exchange(1, 100, "问二", "答二")
            store.append_exchange(1, 100, "问三", "答三")
            entries = store.messages(1)
            self.assertLessEqual(len(entries), 4)
            self.assertFalse(any(item.get("content") == "答一" for item in entries))

    def test_model_context_labels_assistant_without_user_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            bot = aiqq.Bot(cfg)
            bot.user_context.append_exchange(7, 1, "触发文本", "模型回复")
            entries = bot._user_context_entries(7)
            self.assertEqual(entries[0]["content"], "[user] 触发文本")
            self.assertEqual(entries[1]["content"], "[迪普西克] 模型回复")

    def test_model_context_saves_names_and_mentions(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            bot = aiqq.Bot(cfg)
            bot.user_context.append_exchange(
                7, 1, "触发文本", "模型回复",
                user_name="小明", mentions=[{"user_id": "8", "name": "小红"}])
            entries = bot._user_context_entries(7)
            self.assertIn("小明：触发文本", entries[0]["content"])
            self.assertIn("[被@] 小红(QQ8)", entries[0]["content"])
            self.assertEqual(entries[1]["content"], "[迪普西克] 模型回复")

    def test_system_prompt_has_natural_current_time_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            bot = aiqq.Bot(cfg)
            prompt = bot._system_prompt(1, "测试")
            self.assertIn("[当前时间]", prompt)
            self.assertIn("不要在回复里反复报时", prompt)
            self.assertIn("更像真人的时间感", prompt)

    def test_extract_mentions(self):
        message = [
            {"type": "at", "data": {"qq": "10001", "name": "小明"}},
            {"type": "at", "data": {"qq": "10002"}},
            {"type": "at", "data": {"qq": "all"}},
        ]
        mentions = aiqq.extract_mentions(message, "99999")
        self.assertEqual([item["user_id"] for item in mentions], ["10001", "10002"])

    def test_voice_marker_splits_enter_into_multiple_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            wav = os.path.join(tmp, "voice.wav")
            with open(wav, "wb") as fh:
                fh.write(b"wav")
            texts = []

            def fake_synthesize(text, **kwargs):
                path = os.path.join(tmp, "voice_%d.wav" % len(texts))
                with open(path, "wb") as fh:
                    fh.write(b"wav")
                texts.append(text)
                return path, 0.1

            bot.voice.synthesize = fake_synthesize
            sent = []
            bot.onebot.send_group_msg = lambda group_id, segments: (
                sent.append(segments) or {"message_id": "m"})
            old_sleep = aiqq.time.sleep
            aiqq.time.sleep = lambda seconds: None
            try:
                bot._send_reply(
                    {"message_id": "m1", "user_id": 1, "text": "你好"},
                    1, "/VOICE/ 你好 /ENTER/ 世界")
            finally:
                aiqq.time.sleep = old_sleep
            self.assertEqual(texts, ["你好", "世界"])
            self.assertEqual([item[0]["type"] for item in sent], ["record", "record"])

    def test_voice_tts_failure_keeps_one_text_per_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            bot._send_voice_reply = lambda entry, group_id, text, quote_used=False, voice_params=None: False
            sent = []
            bot.onebot.send_group_msg = lambda group_id, segments: (
                sent.append(segments) or {"message_id": "m"})
            bot._send_reply(
                {"message_id": "m1", "user_id": 1, "text": "你好"},
                1, "/VOICE/ 你好 /ENTER/ 世界")
            self.assertEqual(len(sent), 2)
            self.assertEqual(
                [item[0]["data"]["text"] for item in sent], ["你好", "世界"])

    def test_same_message_triggers_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            keyword = bot.cfg["trigger"]["keywords"][0]
            bot.dedup.is_duplicate = lambda key: False
            bot.limiter.try_acquire = lambda *args, **kwargs: (True, "")
            enqueued = []
            bot._enqueue_model_request = (
                lambda entry, group_id, priority: enqueued.append(
                    (entry.get("message_id"), group_id)) or True)
            event = {
                "group_id": 1,
                "user_id": 2,
                "message_id": "same-message",
                "sender": {"nickname": "测试"},
                "message": [{"type": "text", "data": {"text": keyword + "你好"}}],
            }
            bot.handle_group_message(dict(event))
            bot.handle_group_message(dict(event, self_id=999))
            self.assertEqual(enqueued, [("same-message", 1)])

    def test_send_reply_uses_tone_until_next_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            calls = []
            bot._send_voice_reply = (
                lambda entry, group_id, text, quote_used=False, voice_params=None: (
                    calls.append((text, voice_params.get("instruct"))) or True))
            bot._send_reply(
                {"message_id": "m1", "user_id": 1, "text": "你好"},
                1, "/VOICE:温柔轻声/ 第一句。\n/VOICE:生气/ 第二句。")
            self.assertEqual(calls, [("第一句。", "温柔轻声"),
                                     ("第二句。", "生气")])

    def test_marker_only_at_reply_start_inherits_to_next_segments(self):
        raw = (
            "/VOICE:保持【迪普西克】人设：音色偏【萝莉少女、母性萝莉】、"
            "年龄感【十二到十四岁】、性格【温柔慵懒】、语速【很慢】，"
            "带一点困意和撒娇的鼻音/Enter/宝贝乖啦……闭上眼睛……今天辛苦了哦\n"
            "/Enter/妈妈在这里陪着你呢……不要害怕……我会一直守着你的……晚安"
        )
        self.assertTrue(aiqq.has_voice_marker(raw))
        units = aiqq.split_voice_units(raw, "/Enter/")
        self.assertEqual(len(units), 2)
        self.assertTrue(units[0][0].startswith("宝贝乖啦"))
        self.assertTrue(units[1][0].startswith("妈妈在这里"))
        self.assertTrue(units[0][1])
        self.assertEqual(units[0][1], units[1][1])
        self.assertNotIn("/VOICE", " ".join(text for text, _ in units))

    def test_voice_description_with_brackets_is_not_spoken(self):
        units = aiqq.split_voice_units(
            "/VOICE:保持【角色】人设：音色偏【萝莉】、年龄感【十二岁】、语速【稍慢】/"
            "肝的我肝疼大人，我滴在乖乖干活呀", "/Enter/")
        self.assertEqual(len(units), 1)
        body, instruct = units[0]
        self.assertTrue(body.startswith("肝的我肝疼大人"))
        self.assertNotIn("/VOICE", body)
        self.assertIn("萝莉", instruct)

    def test_inline_voice_marker_is_not_spoken(self):
        units = aiqq.split_voice_units(
            "你好呀 /VOICE:温柔软糯，语速缓慢/ 世界真安静", "/Enter/")
        self.assertEqual([text for text, _ in units], ["你好呀", "世界真安静"])
        self.assertNotIn("/VOICE", " ".join(text for text, _ in units))

    def test_voice_units_inherit_and_change_at_sentence_start(self):
        units = aiqq.split_voice_units(
            "/VOICE:温柔轻声/ 第一句。第二句。/Enter/ 第三句。\n/VOICE:生气/ 第四句。",
            "/Enter/")
        self.assertEqual([text for text, _ in units],
                         ["第一句。第二句。", "第三句。", "第四句。"])
        self.assertEqual([instruct for _, instruct in units],
                         ["温柔轻声", "温柔轻声", "生气"])
        again = aiqq.split_voice_units("没有标记", "/Enter/")
        self.assertEqual(again, [("没有标记", "")])

    def test_voice_with_image_still_sends_gallery_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            images = []
            voices = []
            bot._send_gallery_images = (
                lambda entry, group_id, names, quote_used=False: (
                    images.append(list(names)) or 1, True))
            bot._send_voice_reply = (
                lambda entry, group_id, text, quote_used=False, voice_params=None: (
                    voices.append(text) or True))
            bot._send_reply(
                {"message_id": "m1", "user_id": 1, "text": "你好"},
                1, "/VOICE:20:5:10/ 你好 /IMG:测试图/")
            self.assertEqual(images, [["测试图"]])
            self.assertEqual(voices, ["你好"])

    def test_voice_reply_uses_duration_plus_random_delay(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            wav = os.path.join(tmp, "voice.wav")
            with open(wav, "wb") as fh:
                fh.write(b"wav")
            bot.voice.synthesize = lambda text, **kwargs: (wav, 2.0)
            sent = []
            sleeps = []
            bot.onebot.send_group_msg = lambda group_id, segments: (
                sent.append((group_id, segments)) or {"message_id": "m"})
            old_sleep = aiqq.time.sleep
            aiqq.time.sleep = lambda seconds: sleeps.append(seconds)
            try:
                ok = bot._send_voice_reply(
                    {"message_id": "m1", "user_id": 1}, 1, "你好")
            finally:
                aiqq.time.sleep = old_sleep
            self.assertTrue(ok)
            self.assertEqual(sent[0][1][0]["type"], "record")
            self.assertGreaterEqual(sleeps[0], 3.0)
            self.assertLessEqual(sleeps[0], 5.0)

    def test_strip_speaker_labels(self):
        text = "[迪普西克] 你好\n[肥鱼] 在的"
        self.assertEqual(aiqq.strip_speaker_labels(text, ("迪普西克",)), "你好\n在的")

    def test_tail_voice_prompt_is_ignored(self):
        text = ("怎么都比我早醒啊 /VOICE:保持同一角色声线：稚嫩偏高的少女声，"
                "带着刚睡醒的慵懒和一点小迷糊，语速慢吞吞的，声音有点哑")
        self.assertFalse(aiqq.has_voice_marker(text))
        requested, cleaned, params = aiqq.parse_voice_marker(text)
        self.assertFalse(requested)
        self.assertEqual(cleaned, "怎么都比我早醒啊")
        self.assertEqual(params, {})
        self.assertEqual(aiqq.split_voice_units(text, "/Enter/"),
                         [("怎么都比我早醒啊", "")])

        complete = "怎么都比我早醒啊 /VOICE:懒散/"
        self.assertFalse(aiqq.has_voice_marker(complete))
        self.assertEqual(aiqq.split_voice_units(complete, "/Enter/"),
                         [("怎么都比我早醒啊", "")])

        before_enter = "第一段 /VOICE:懒散/ /Enter/ 第二段"
        self.assertFalse(aiqq.has_voice_marker(before_enter))
        self.assertEqual(aiqq.split_voice_units(before_enter, "/Enter/"),
                         [("第一段", ""), ("第二段", "")])

        with_start = "/VOICE:温柔轻声/ 第一句。 /VOICE:尾"
        self.assertTrue(aiqq.has_voice_marker(with_start))
        self.assertEqual(aiqq.split_voice_units(with_start, "/Enter/"),
                         [("第一句。", "温柔轻声")])

    def test_ignored_voice_prompt_is_not_sent_as_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            sent = []
            bot.cfg["reply"]["first_delay_sec"] = 0
            bot.cfg["reply"]["chunk_delay_sec"] = 0
            bot.onebot.send_group_msg = lambda group_id, segments: (
                sent.append(segments) or {"message_id": "m"})
            bot._send_gallery_images = (
                lambda entry, group_id, names, quote_used=False: (False, False))
            bot._send_reply(
                {"message_id": "m1", "user_id": 1, "text": "你好"},
                1, "/VOICE:保持萝莉妈妈的人设：稚嫩偏高的少女声，像小孩子撒娇但带一点照顾人的温柔，语速稍慢，句尾软软的/")
            self.assertEqual(sent, [])
            bot._send_reply(
                {"message_id": "m2", "user_id": 1, "text": "你好"},
                1, "怎么都比我早醒啊 /VOICE:保持同一角色声线：稚嫩偏高的少女声，语速慢吞吞的，声音有点哑")
            self.assertEqual(
                [item[0]["data"]["text"] for item in sent], ["怎么都比我早醒啊"])

    def test_strip_voice_marker(self):
        requested, text = aiqq.strip_voice_marker("/VOICE/ 整段话 /Enter/ 第二段")
        self.assertTrue(requested)
        self.assertNotIn("/VOICE/", text)
        self.assertIn("整段话", text)

    def test_parse_voice_marker_parameters(self):
        requested, text, params = aiqq.parse_voice_marker(
            "/VOICE:120:-80:200/ 你好 /Enter/ 世界")
        self.assertTrue(requested)
        self.assertEqual(params, {"rate": 100, "pitch": -50,
                                  "volume": 100, "instruct": ""})
        self.assertEqual(text, "你好 /Enter/ 世界")

    def test_unclosed_voice_marker_keeps_body(self):
        requested, text, params = aiqq.parse_voice_marker(
            "/VOICE:保持人设：温柔软糯，语速缓慢\n小星星，亮晶晶。\n我也在问你呀。")
        self.assertTrue(requested)
        self.assertEqual(params.get("instruct"), "保持人设：温柔软糯，语速缓慢")
        self.assertIn("小星星，亮晶晶。", text)
        self.assertIn("我也在问你呀。", text)
        self.assertNotIn("/VOICE", text)

    def test_parse_voice_description_marker(self):
        requested, text, params = aiqq.parse_voice_marker(
            "/VOICE:体现撒娇稚嫩的少女声，音调偏高且起伏明显/ 你好")
        self.assertTrue(requested)
        self.assertEqual(
            params["instruct"], "体现撒娇稚嫩的少女声，音调偏高且起伏明显")
        self.assertEqual(text, "你好")
        requested, text, params = aiqq.parse_voice_marker(
            "/VOICE：懒散低沉，语速缓慢/ 世界")
        self.assertTrue(requested)
        self.assertEqual(params["instruct"], "懒散低沉，语速缓慢")
        self.assertEqual(text, "世界")

    def test_parse_bracket_voice_marker(self):
        requested, text, params = aiqq.parse_voice_marker(
            "[VOICE:35:12:20] 你好\n【VOICE】 世界")
        self.assertFalse(requested)
        self.assertNotIn("VOICE", text)
        self.assertIn("你好", text)
        self.assertIn("世界", text)

    def test_extract_voice_segments(self):
        message = [
            {"type": "record", "data": {"file": "a.amr", "url": "http://voice/a"}},
            {"type": "text", "data": {"text": "hi"}},
        ]
        voices = aiqq.extract_voice_segments(message)
        self.assertEqual(voices[0]["file"], "a.amr")
        self.assertEqual(voices[0]["url"], "http://voice/a")

    def test_system_prompt_voice_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            bot = aiqq.Bot(cfg)
            prompt = bot._system_prompt(1, "测试")
            self.assertIn("/VOICE:语气描述/", prompt)
            self.assertIn("人设", prompt)
            self.assertNotIn("稚嫩偏高", prompt)
            self.assertNotIn("/SAVIMG:1:肥鱼得意", prompt)
            self.assertIn("不能变成另一个角色", prompt)


class ModelQueueTests(unittest.TestCase):
    def test_priority_clears_lower_same_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1, 2]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            bot._enqueue_model_request({"text": "低", "followup": True}, 1, 1)
            bot._enqueue_model_request({"text": "高"}, 1, 0)
            bot._enqueue_model_request({"text": "其他群"}, 2, 0)
            queued = [item[2]["text"] for item in bot._model_queue]
            self.assertIn("高", queued)
            self.assertIn("低", queued)
            self.assertIn("其他群", queued)
            self.assertLess(queued.index("低"), queued.index("高"))

    def test_queue_max_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            accepted = [
                bot._enqueue_model_request({"text": str(i)}, 1 + i, 0)
                for i in range(11)
            ]
            self.assertEqual(accepted.count(True), 10)
            self.assertEqual(bot._model_queue_size(), 10)


class ContextActivityTests(unittest.TestCase):
    def test_normal_messages_do_not_reset_trigger_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [123]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            b = aiqq.Bot(cfg)
            b._context_last_activity[123] = 0
            b._mark_context_dirty(123)
            self.assertEqual(b._context_last_activity[123], 0)
            b._note_context_activity(123)
            self.assertGreater(b._context_last_activity[123], 0)

    def test_self_reply_is_remembered(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [123]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            b = aiqq.Bot(cfg)
            b.self_id = 999
            b.self_nick = "鲸鲸"
            b._remember_self_reply(123, "自己的回复")
            entry = b.memory.recent(123, 1)[0]
            self.assertTrue(entry["is_self"])
            self.assertEqual(entry["text"], "自己的回复")
            self.assertEqual(entry["user_id"], 999)

    def test_manual_context_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [123]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            b = aiqq.Bot(cfg)
            b.user_context.append_exchange(1, 123, "手动保存", "模型回复")
            result = b.save_context_now()
            self.assertEqual(result["saved"], 1)
            self.assertTrue(os.path.isdir(os.path.join(
                tmp, "context", "123", "1")))


class LMReloadTests(unittest.TestCase):
    def test_reload_model_uses_native_v1_api(self):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG["lm_studio"], {"model": "demo-model"})
        client = aiqq.LMStudioClient(cfg)
        calls = []

        def fake(method, path, payload=None, timeout=None):
            calls.append((method, path, payload))
            if path == "/api/v1/models":
                return {"models": [{
                    "key": "demo-model",
                    "loaded_instances": [{
                        "id": "demo-model",
                        "config": {"context_length": 4096, "flash_attention": True},
                    }],
                }]}
            if path == "/api/v1/models/unload":
                return {"instance_id": payload["instance_id"]}
            if path == "/api/v1/models/load":
                return {"instance_id": "demo-model", "load_time_seconds": 1.2}
            return {}

        client._native_request = fake
        info = client.reload_model()
        self.assertEqual(info["model"], "demo-model")
        self.assertIn(("POST", "/api/v1/models/unload", {"instance_id": "demo-model"}), calls)
        load_call = [item for item in calls if item[1] == "/api/v1/models/load"][0]
        self.assertEqual(load_call[2]["model"], "demo-model")
        self.assertEqual(load_call[2]["context_length"], 4096)
        self.assertTrue(load_call[2]["flash_attention"])


class IdleImageReplyTests(unittest.TestCase):
    def make_bot(self, tmp):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "qq": {"groups": [1]},
            "lm_studio": {
                "context_save_dir": os.path.join(tmp, "context"),
                "idle_image_start_hour": 0,
                "idle_image_end_hour": 24,
                "idle_image_idle_sec": 30,
            },
        })
        return aiqq.Bot(cfg)

    def test_normal_trigger_does_not_overtake_older_followup(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            older = {"message_id": "1", "text": "旧图", "images": [], "followup": True}
            newer = {"message_id": "2", "text": "新图", "images": []}
            self.assertTrue(bot._enqueue_model_request(older, 1, 1))
            self.assertTrue(bot._enqueue_model_request(newer, 1, 0))
            self.assertEqual(len(bot._model_queue), 2)
            self.assertIs(bot._model_queue[0][2], older)

    def test_blacklist_purges_user_queues_and_reloads_active_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            calls = []
            bot.request_model_reload = lambda force=False: calls.append(force) or True
            bot._queue.put({"user_id": 7, "message_id": "e1"})
            bot._queue.put({"user_id": 8, "message_id": "e2"})
            first = {"message_id": "1", "user_id": 7, "images": []}
            second = {"message_id": "2", "user_id": 8, "images": []}
            active = {"message_id": "3", "user_id": 7, "images": []}
            bot._enqueue_model_request(first, 1, 0)
            bot._enqueue_model_request(second, 1, 0)
            bot._pending_followups[1] = {"entry": {"user_id": 7}}
            bot._model_active_group = 1
            bot._model_active_user = 7
            bot._model_active_entry = active
            result = bot._purge_user_queues(1, 7, reason="测试")
            self.assertEqual(result["events"], 1)
            self.assertEqual(result["models"], 1)
            self.assertEqual(result["pending"], 1)
            self.assertTrue(result["reload"])
            self.assertTrue(active.get("blocked_by_blacklist"))
            self.assertEqual(calls, [True])
            remaining = [item[2].get("user_id") for item in bot._model_queue]
            self.assertEqual(remaining, [8])
            self.assertEqual(bot._queue.qsize(), 1)
            self.assertEqual(bot._queue.get_nowait().get("user_id"), 8)

    def test_consecutive_followups_are_coalesced(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            first = {"message_id": "1", "text": "第一句", "images": [], "followup": True}
            second = {"message_id": "2", "text": "第二句", "images": [], "followup": True}
            self.assertTrue(bot._enqueue_model_request(first, 1, 1))
            self.assertTrue(bot._enqueue_model_request(second, 1, 1))
            self.assertEqual(len(bot._model_queue), 1)
            self.assertIs(bot._model_queue[0][2], second)

    def test_recent_similar_reply_is_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            first = ("哈哈……这图看着好有精神啊，白头发眨一只眼配满屏的“哈哈哈”，"
                     "像是在疯狂憋笑或者被逗乐了")
            similar = ("天呐……这图看着好有精神啊，白头发眨一只眼配满屏的“哈哈哈”，"
                       "像是在疯狂憋笑或者被逗乐了")
            bot._remember_sent_reply(1, first)
            self.assertTrue(bot._is_duplicate_reply(1, similar))
            self.assertFalse(bot._is_duplicate_reply(1, "完全不同的话题，今天天气不错"))

    def test_replied_images_are_marked_for_idle_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            info = {"url": "http://example/image.png", "file": "image.png"}
            entry = {"message_id": "m1", "images": [info]}
            bot._mark_images_handled(entry, 1)
            key = bot._idle_image_key(1, entry, 0, info)
            self.assertIn(key, bot._idle_image_seen)
            self.assertGreater(bot._last_idle_image_at, 0)

    def test_hour_window_and_pick_skips_triggered(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            self.assertTrue(bot._idle_image_hour_allowed())
            info = {"url": "http://example/image.png", "file": "image.png"}
            bot.memory.add(1, {
                "message_id": "m1", "user_id": 2, "nickname": "群友",
                "text": "看图", "images": [info], "is_self": False,
                "time": int(time.time()),
            })
            picked = bot._pick_idle_image(time.time())
            self.assertIsNotNone(picked)
            self.assertEqual(picked[1]["message_id"], "m1")
            self.assertIsNone(bot._pick_idle_image(time.time()))

            bot.memory.add(1, {
                "message_id": "old", "user_id": 2, "nickname": "群友",
                "text": "旧图", "images": [info], "is_self": False,
                "time": int(time.time()) - 400,
            })
            self.assertIsNone(bot._pick_idle_image(time.time()))

            bot.memory.add(1, {
                "message_id": "m2", "user_id": 2, "nickname": "群友",
                "text": "看图", "images": [info], "is_self": False,
                "triggered": True, "time": int(time.time()),
            })
            self.assertIsNone(bot._pick_idle_image(time.time()))

    def test_no_recent_image_keeps_polling(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._last_trigger_at = 0
            bot._last_idle_image_at = 0
            bot._last_idle_image_poll_at = 0
            bot._connectivity["onebot_connected"] = True
            before = time.time()
            bot._idle_image_tick()
            self.assertGreaterEqual(bot._last_idle_image_poll_at, before)
            self.assertEqual(bot._last_idle_image_at, 0)

    def test_new_trigger_cancels_idle_image_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            info = {"url": "http://example/image.jpg", "file": "image.jpg"}
            entry = {"message_id": "m1", "user_id": 2, "nickname": "群友",
                     "text": "看这个", "images": [info], "is_self": False,
                     "time": int(time.time())}
            bot.lm.supports_vision = lambda: True
            bot._read_image_bytes = lambda item: b"\xff\xd8\xff" + b"0" * 32

            def chat(messages):
                bot._cancel_idle_image_tasks()
                return "空闲识图回复"
            bot.lm.chat = chat
            sent = []
            bot._send_reply = lambda e, g, text, thinking_sec=0.0: sent.append(text)
            ok = bot._idle_image_reply(1, entry, info, time.time())
            self.assertFalse(ok)
            self.assertEqual(sent, [])
            self.assertGreater(bot._idle_image_generation, 0)

    def test_pick_batch_oldest_and_max_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            now = time.time()
            for index in range(6):
                info = {"url": "http://example/%d.png" % index, "file": "%d.png" % index}
                bot.memory.add(1, {
                    "message_id": "m%d" % index, "user_id": 2, "nickname": "群友",
                    "text": "图%d" % index, "images": [info], "is_self": False,
                    "time": int(now - 50 + index),
                })
            picked = bot._pick_idle_images(now)
            self.assertEqual(len(picked), 5)
            self.assertEqual([item[1]["message_id"] for item in picked],
                             ["m0", "m1", "m2", "m3", "m4"])

    def test_failed_batch_image_shifts_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            good = {"url": "http://example/good.jpg", "file": "good.jpg"}
            bad = {"url": "http://example/bad.jpg", "file": "bad.jpg"}
            e1 = {"message_id": "m1", "user_id": 2, "nickname": "群友",
                  "text": "图1", "images": [bad], "is_self": False,
                  "time": int(time.time())}
            e2 = {"message_id": "m2", "user_id": 2, "nickname": "群友",
                  "text": "图2", "images": [good], "is_self": False,
                  "time": int(time.time())}
            bot.lm.supports_vision = lambda: True
            bot._read_image_bytes = lambda item: (
                (_ for _ in ()).throw(ValueError("bad")) if item is bad
                else b"\xff\xd8\xff" + b"0" * 32)
            calls = []
            bot.lm.chat = lambda messages: calls.append(messages) or "收到"
            bot._send_reply = lambda *args, **kwargs: None
            bot.user_context.append_exchange = lambda *args, **kwargs: None
            ok = bot._idle_image_reply_batch(1, [(e1, bad), (e2, good)], time.time())
            self.assertTrue(ok)
            image_parts = [part for msg in calls[0] if isinstance(msg.get("content"), list)
                           for part in msg["content"] if part.get("type") == "image_url"]
            self.assertEqual(len(image_parts), 1)
            self.assertEqual(bot._image_inputs_cache[(1, "m2")], [good])

    def test_failed_idle_recognition_does_not_start_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            info = {"url": "http://example/image.jpg", "file": "image.jpg"}
            entry = {"message_id": "m1", "user_id": 2, "nickname": "群友",
                     "text": "看这个", "images": [info], "is_self": False,
                     "time": int(time.time())}
            bot.lm.supports_vision = lambda: True
            bot._read_image_bytes = lambda item: b"\xff\xd8\xff" + b"0" * 32
            bot.lm.chat = lambda messages: ""
            self.assertFalse(bot._idle_image_reply(1, entry, info, time.time()))
            self.assertEqual(bot._last_idle_image_at, 0)

    def test_no_image_after_startup_keeps_retrying(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._connectivity["onebot_connected"] = True
            bot._last_trigger_at = time.time()
            bot._last_idle_image_at = 0
            bot._last_idle_image_poll_at = 0
            bot._idle_image_tick()
            first = bot._last_idle_image_poll_at
            self.assertGreater(first, 0)
            bot._idle_image_tick()
            self.assertGreaterEqual(bot._last_idle_image_poll_at, first)

    def test_idle_reply_uses_one_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            info = {"url": "http://example/image.jpg", "file": "image.jpg"}
            entry = {"message_id": "m1", "user_id": 2, "nickname": "群友",
                     "text": "看这个", "images": [info], "is_self": False,
                     "time": int(time.time())}
            bot.memory.add(1, entry)
            bot.lm.supports_vision = lambda: True
            calls = []
            bot._read_image_bytes = lambda item: b"\xff\xd8\xff" + b"0" * 32
            bot.lm.chat = lambda messages: calls.append(messages) or "好看"
            sent = []
            context = []
            raw = []
            bot._remember_raw_reply = lambda g, text: raw.append((g, text))
            bot._send_reply = lambda e, g, text, thinking_sec=0.0: sent.append(text)
            bot.group_context.append_exchange = lambda *args, **kwargs: context.append((args, kwargs)) or 0
            ok = bot._idle_image_reply(1, entry, info, time.time())
            self.assertTrue(ok)
            self.assertEqual(sent, ["好看"])
            self.assertEqual(raw, [(1, "好看")])
            self.assertEqual(len(context), 1)
            self.assertEqual(context[0][0][1], "看这个")
            self.assertEqual(context[0][0][2], "好看")
            image_parts = [part for msg in calls[0] if isinstance(msg.get("content"), list)
                           for part in msg["content"] if part.get("type") == "image_url"]
            self.assertEqual(len(image_parts), 1)
            self.assertIn((1, "m1"), bot._image_inputs_cache)


class ImageFailureNumberingTests(unittest.TestCase):
    def test_failed_multimodal_inputs_ignore_save_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            bot = aiqq.Bot(cfg)
            bot._image_inputs_cache[(1, "m1")] = False
            called = []
            bot._collect_save_images = lambda entry, group_id: called.append(True) or []
            saved = bot._save_marked_images(
                {"message_id": "m1", "user_id": 1},
                1,
                [{"position": 1, "name": "测试", "description": "测试", "favorite": 50}],
            )
            self.assertEqual(saved, [])
            self.assertEqual(called, [])


class SharedMarkerSlashTests(unittest.TestCase):
    def test_save_marker_can_share_slash_with_enter(self):
        raw = (
            "/SAVIMG:1:蕾姆哭脸_蓝发女仆:蓝色短发女仆装少女，紫色眼睛含泪，"
            "表情震惊悲伤，头戴三角形发饰，适合表达感动、委屈或剧情高潮场景:85"
            "/Enter/哇……蕾姆姐姐在哭吗？看着好心疼啊"
        )
        cleaned, markers = aiqq.parse_save_markers(raw)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["name"], "蕾姆哭脸_蓝发女仆")
        self.assertTrue(cleaned.strip().startswith("/Enter/"))
        self.assertEqual(aiqq.split_by_marker(cleaned, "/Enter/"),
                         ["哇……蕾姆姐姐在哭吗？看着好心疼啊"])

    def test_gif_is_converted_to_static_png_for_vision(self):
        if not aiqq._find_ffmpeg():
            self.skipTest("未安装 ffmpeg，跳过 GIF 转换测试")
        raw = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
        data_url = aiqq.image_bytes_to_data_url(raw)
        self.assertTrue(data_url.startswith("data:image/png;base64,"))

    def test_bracketed_favorite_suffix_is_removed(self):
        raw = "[SAVIMG:1:蕾姆:描述（喜爱度50）:50]"
        cleaned, _markers = aiqq.parse_save_markers(raw)
        self.assertNotIn(":50]", cleaned)
        self.assertEqual(cleaned.strip(), "")
        cleaned, _markers = aiqq.parse_save_markers(
            "[SAVIMG:1:蕾姆:描述（喜爱度50）:50]/Enter/正文")
        self.assertNotIn(":50]", cleaned)
        self.assertEqual(aiqq.split_by_marker(cleaned, "/Enter/"), ["正文"])

    def test_image_and_voice_markers_share_slash(self):
        cleaned, names = aiqq.parse_image_markers("/IMG:名字/Enter/正文")
        self.assertEqual(names, ["名字"])
        self.assertEqual(cleaned.strip(), "/Enter/正文")
        requested, cleaned, _params = aiqq.parse_voice_marker("/VOICE:温柔/Enter/正文")
        self.assertTrue(requested)
        self.assertEqual(cleaned, "/Enter/正文")


class SaveMarkerSlashTests(unittest.TestCase):
    def test_slash_prefixed_save_marker_is_parsed(self):
        cleaned, markers = aiqq.parse_save_markers(
            "梁文峰是爹诶 /SAVIMG:1:拒绝救你梗图:聊天截图里反复问掉水里救谁:85/")
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["position"], 1)
        self.assertEqual(markers[0]["name"], "拒绝救你梗图")
        self.assertEqual(markers[0]["favorite"], 85)
        self.assertNotIn("SAVIMG", cleaned)


class FlexibleMarkerWrapperTests(unittest.TestCase):
    def test_save_markers_accept_mixed_and_bare_wrappers(self):
        cases = [
            "[SAVIMG:1:图一:描述一:80]",
            "【SAVIMG:1:图二:描述二:81】",
            "(SAVIMG:1:图四:描述四:83)",
            "SAVIMG:1:图五:描述五:84",
        ]
        for text in cases:
            cleaned, markers = aiqq.parse_save_markers(text)
            self.assertEqual(markers, [], text)
            self.assertNotIn("SAVIMG", cleaned)
        cleaned, markers = aiqq.parse_save_markers("/SAVIMG:1:图三:描述三:82]")
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["name"], "图三")

    def test_image_markers_accept_mixed_and_bare_wrappers(self):
        for text in ("[IMG:肥鱼]", "【IMG:肥鱼】", "(IMG:肥鱼)", "IMG:肥鱼"):
            cleaned, names = aiqq.parse_image_markers(text)
            self.assertEqual(names, [], text)
            self.assertNotIn("IMG", cleaned)
        cleaned, names = aiqq.parse_image_markers("/IMG:肥鱼]")
        self.assertEqual(names, ["肥鱼"])


class FlexibleVoiceWrapperTests(unittest.TestCase):
    def test_voice_marker_accepts_mixed_and_bare_wrappers(self):
        requested, cleaned, params = aiqq.parse_voice_marker("/VOICE:温柔/你好")
        self.assertTrue(requested)
        self.assertIn("你好", cleaned)
        self.assertEqual(params.get("instruct"), "温柔")
        for text in ("[VOICE:温柔]你好", "【VOICE:温柔】你好", "（VOICE:温柔）你好"):
            requested, cleaned, params = aiqq.parse_voice_marker(text)
            self.assertFalse(requested, text)
            self.assertIn("你好", cleaned)
            self.assertNotIn("VOICE", cleaned)
        requested, cleaned, params = aiqq.parse_voice_marker("/VOICE:温柔\n你好")
        self.assertTrue(requested)
        self.assertEqual(params.get("instruct"), "温柔")


class VoiceIdleUnloadTests(unittest.TestCase):
    def make_bot(self, tmp):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "qq": {"groups": [1]},
            "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
        })
        return aiqq.Bot(cfg)

    def test_voice_model_unloads_after_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.voice.qwen_enabled = True
            bot.voice.is_busy = lambda: False
            calls = []
            bot.voice.unload_qwen_server = lambda: calls.append(True) or True
            now = time.time()
            bot._last_voice_done_at = now - 61
            self.assertTrue(bot._maybe_unload_voice_model(now))
            self.assertEqual(calls, [True])
            self.assertEqual(bot._last_voice_done_at, 0)

    def test_voice_model_does_not_unload_before_idle_or_while_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.voice.qwen_enabled = True
            calls = []
            bot.voice.unload_qwen_server = lambda: calls.append(True) or True
            now = time.time()
            bot._last_voice_done_at = now - 10
            self.assertFalse(bot._maybe_unload_voice_model(now))
            bot._last_voice_done_at = now - 61
            bot.voice.is_busy = lambda: True
            self.assertFalse(bot._maybe_unload_voice_model(now))
            self.assertEqual(calls, [])


class SaveBeforeImageMarkerTests(unittest.TestCase):
    def test_savimg_is_not_parsed_as_img(self):
        cleaned, markers = aiqq.parse_save_markers(
            "/SAVIMG:1:拒绝救你梗图:聊天截图里反复问掉水里救谁:85/")
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["name"], "拒绝救你梗图")
        cleaned, names = aiqq.parse_image_markers(cleaned)
        self.assertEqual(names, [])
        self.assertNotIn("SAVIMG", cleaned)


class StrictMarkerTests(unittest.TestCase):
    def test_only_slash_markers_are_effective(self):
        self.assertEqual(aiqq.parse_save_markers("[SAVIMG:1:名:描述:80]")[1], [])
        self.assertEqual(aiqq.parse_image_markers("[IMG:名]")[1], [])
        self.assertFalse(aiqq.parse_voice_marker("[VOICE:温柔] 你好")[0])
        self.assertEqual(aiqq.strip_incomplete_enter_markers("甲[Enter]乙"), "甲 乙")
        self.assertEqual(aiqq.parse_save_markers("/SAVIMG:1:名:描述:80/")[1][0]["name"], "名")


class ProfessionalModeTests(unittest.TestCase):
    def test_parse_professional_command(self):
        self.assertEqual(aiqq.extract_professional_command("/专业模式：解释一下相对论"), "解释一下相对论")
        self.assertEqual(aiqq.extract_professional_command("/专业模式: 写代码"), "写代码")
        self.assertIsNone(aiqq.extract_professional_command("普通聊天"))

    def test_professional_context_is_separate_and_deletable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            bot.professional_context.append_exchange(
                2, 1, "上一轮专业问题", "上一轮专业回答", user_name="群友")
            self.assertEqual(len(bot.professional_context.messages(2)), 2)
            self.assertEqual(len(bot.user_context.messages(2)), 0)
            calls = []
            bot.lm.chat = lambda messages, reasoning_effort=None: (
                calls.append(messages) or "新回答")
            bot._remember_raw_reply = lambda *args: None
            bot._generate_professional_reply(
                {"text": "新问题", "professional": True, "user_id": 2}, 1)
            history_contents = [m["content"] for m in calls[0]]
            self.assertIn("上一轮专业问题", history_contents)
            self.assertIn("上一轮专业回答", history_contents)
            result = bot.delete_professional_context(2)
            self.assertTrue(result["ok"])
            self.assertEqual(bot.professional_context.messages(2), [])

    def test_professional_reply_skips_persona_and_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            calls = []
            raw = []
            bot.lm.chat = lambda messages, reasoning_effort=None: (
                calls.append((messages, reasoning_effort)) or "专业回答")
            bot._remember_raw_reply = lambda group_id, text: raw.append((group_id, text))
            reply = bot._generate_professional_reply(
                {"text": "解释一下相对论", "professional": True}, 1)
            self.assertEqual(reply, "专业回答")
            self.assertEqual(calls[0][1], "high")
            messages = calls[0][0]
            self.assertEqual(len(messages), 2)
            self.assertNotIn("你是鲸鲸", messages[0]["content"])
            self.assertNotIn("女仆", messages[0]["content"])
            self.assertEqual(messages[1]["content"], "解释一下相对论")
            self.assertEqual(raw, [(1, "专业回答")])


class ProfessionalPauseTests(unittest.TestCase):
    def test_professional_reply_clears_global_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            }))
            bot._generate_reply = lambda entry, group_id: "完成"
            bot._send_reply = lambda *args, **kwargs: None
            bot._professional_active.set()
            bot._professional_message_id = "m1"
            bot._reply_job(
                {"message_id": "m1", "user_id": 2, "text": "内容",
                 "professional": True}, 1)
            self.assertFalse(bot._professional_active.is_set())
            self.assertEqual(bot._professional_message_id, "")


class ProfessionalSendingTests(unittest.TestCase):
    def test_professional_reply_splits_only_newlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = aiqq.Bot(aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
                "reply": {"first_delay_sec": 0, "chunk_delay_sec": 0,
                          "chunk_delay_max_sec": 0},
            }))
            sent = []
            bot._send_single_text = lambda entry, group_id, text, quote_used=False: sent.append(text)
            saved = []
            bot._save_marked_images = lambda *args, **kwargs: saved.append(True)
            bot._send_reply(
                {"message_id": "m1", "user_id": 1, "professional": True},
                1,
                "第一行，保留标点！\n/SAVIMG:1:名称:描述:90/\n第三行……",
            )
            self.assertEqual(sent, ["第一行，保留标点！", "/SAVIMG:1:名称:描述:90/", "第三行……"])
            self.assertEqual(saved, [])


class ImageFormatSwitchTests(unittest.TestCase):
    def test_keep_last_images_max_ten(self):
        messages = []
        for index in range(12):
            messages.append({"role": "user", "content": [
                {"type": "text", "text": "第%d张" % (index + 1)},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,%02d" % index}},
            ]})
        messages, count = aiqq.keep_last_images(messages, 10)
        self.assertEqual(count, 10)
        images = [part for message in messages if isinstance(message["content"], list)
                  for part in message["content"] if part.get("type") == "image_url"]
        self.assertEqual(len(images), 10)
        self.assertIn("02", images[0]["image_url"]["url"])
        self.assertIn("11", images[-1]["image_url"]["url"])

    def test_gif_is_blocked_by_default_and_allowed_when_disabled(self):
        gif = bytes.fromhex(
            "47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.gif")
            with open(path, "wb") as fh:
                fh.write(gif)
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            b = aiqq.Bot(cfg)
            self.assertEqual(b._load_image_parts([{"file": path}], 1, "test"), [])
            b.lm.image_only_png_jpeg = False
            self.assertEqual(len(b._load_image_parts([{"file": path}], 1, "test")), 1)


class ReplyToSelfTests(unittest.TestCase):
    def test_history_ignores_self_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            b = aiqq.Bot(cfg)
            b.self_id = 999
            b.onebot.get_group_msg_history = lambda group_id, count=20: [
                {"message_id": "1", "user_id": 999, "time": 1000,
                 "sender": {"user_id": 999, "nickname": "肥鱼"},
                 "message": [{"type": "image", "data": {"url": "http://a", "file": "a.jpg"}}]},
                {"message_id": "2", "user_id": 1000, "time": 1001,
                 "sender": {"user_id": 1000, "nickname": "群友"},
                 "message": [{"type": "image", "data": {"url": "http://b", "file": "b.jpg"}}]},
            ]
            entries = b._history_entries(1, None, 10, 0)
            by_id = {entry["message_id"]: entry for entry in entries}
            self.assertEqual(by_id["1"]["images"], [])
            self.assertEqual(by_id["1"]["forwards"], [])
            self.assertEqual(len(by_id["2"]["images"]), 1)

    def test_raw_reply_is_stored_unmodified(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            b = aiqq.Bot(cfg)
            raw = "[SAVIMG:1:名称:描述:90]\n[图片] 原始输出"
            b._remember_raw_reply(1, raw)
            b._remember_raw_reply(1, "第二条")
            b._remember_raw_reply(2, "另一个群")
            snapshot = b.stats_snapshot()
            self.assertEqual(snapshot["last_raw_reply"], "另一个群")
            self.assertEqual(snapshot["last_raw_reply_group"], 2)
            history = b.raw_history_snapshot()
            by_group = {item["group_id"]: item for item in history["groups"]}
            self.assertEqual(by_group[1]["entries"][0]["text"], raw)
            self.assertEqual(by_group[1]["entries"][1]["text"], "第二条")
            self.assertEqual(by_group[2]["entries"][0]["text"], "另一个群")

    def test_reply_to_bot_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            b = aiqq.Bot(cfg)
            b.self_id = 999
            b.onebot.get_msg = lambda message_id: {"sender": {"user_id": 999}}
            self.assertTrue(b._is_reply_to_self("123"))
            b.onebot.get_msg = lambda message_id: {"sender": {"user_id": 1000}}
            self.assertFalse(b._is_reply_to_self("124"))


class LMUnloadTests(unittest.TestCase):
    def test_unload_current_model(self):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG["lm_studio"], {"model": "demo-model"})
        client = aiqq.LMStudioClient(cfg)
        calls = []

        def fake(method, path, payload=None, timeout=None):
            calls.append((method, path, payload))
            if path == "/api/v1/models":
                return {"models": [{
                    "key": "demo-model",
                    "loaded_instances": [{"id": "demo-model", "config": {}}],
                }]}
            return {"instance_id": "demo-model"}

        client._native_request = fake
        info = client.unload_model()
        self.assertEqual(info["status"], "unloaded")
        self.assertIn(("POST", "/api/v1/models/unload", {"instance_id": "demo-model"}), calls)


class AutonomousTests(unittest.TestCase):
    def make_bot(self, tmp):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "qq": {"groups": [1]},
            "lm_studio": {
                "context_save_dir": os.path.join(tmp, "context"),
                "spontaneous_enabled": True,
                "sleep_state_file": os.path.join(tmp, "sleep_state.json"),
                "sleep_prep_quiet_start_hour": 0,
                "sleep_prep_quiet_end_hour": 0,
            },
        })
        bot = aiqq.Bot(cfg)
        bot._sleep_state_path = os.path.join(tmp, "sleep_state.json")
        return bot

    def test_spontaneous_then_activity_cancels_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            called = []
            bot._send_spontaneous = lambda group_id: called.append(group_id) or True
            bot._last_active_group = 1
            bot._group_last_trigger = {1: time.time()}
            bot._next_spontaneous_at = time.time() - 1
            bot._autonomous_tick()
            self.assertEqual(called, [1])
            self.assertTrue(bot._spontaneous_waiting)
            bot._note_spontaneous_activity(1)
            self.assertFalse(bot._spontaneous_waiting)
            self.assertGreater(bot._next_spontaneous_at, time.time())

    def test_only_recent_trigger_groups_are_targeted(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            called = []
            bot._send_spontaneous = lambda group_id: called.append(group_id) or True
            bot._group_last_trigger = {1: time.time(), 2: time.time() - 4000}
            bot.groups = [1, 2]
            bot._next_spontaneous_at = time.time() - 1
            bot._autonomous_tick()
            self.assertEqual(called, [1])

    def test_wait_timeout_goes_to_sleep(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            called = []
            bot._go_sleep = lambda: called.append("sleep")
            bot._spontaneous_waiting = True
            bot._spontaneous_at = time.time() - 301
            bot._spontaneous_group = 1
            bot._autonomous_tick()
            self.assertEqual(called, ["sleep"])

    def test_sleep_attempts_exceed_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            for expected in range(1, 6):
                self.assertEqual(bot._record_sleep_attempt(1), expected)
            self.assertEqual(bot._record_sleep_attempt(1), 6)
            self.assertEqual(bot._record_sleep_attempt(2), 1)

    def test_sleep_prep_quiet_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.cfg["lm_studio"]["sleep_prep_quiet_start_hour"] = 7
            bot.cfg["lm_studio"]["sleep_prep_quiet_end_hour"] = 22
            self.assertTrue(bot._in_sleep_prep_quiet(datetime(2026, 1, 1, 7, 0)))
            self.assertTrue(bot._in_sleep_prep_quiet(datetime(2026, 1, 1, 21, 59)))
            self.assertFalse(bot._in_sleep_prep_quiet(datetime(2026, 1, 1, 22, 0)))
            self.assertFalse(bot._in_sleep_prep_quiet(datetime(2026, 1, 1, 6, 59)))

    def test_quiet_hours_block_auto_spontaneous(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._group_last_trigger = {1: time.time()}
            bot._next_spontaneous_at = time.time() - 1
            bot._in_sleep_prep_quiet = lambda now_dt=None: True
            called = []
            bot._send_spontaneous = lambda group_id, prompt=None, purpose=None: called.append(group_id) or True
            bot._autonomous_tick()
            self.assertEqual(called, [])
            self.assertGreater(bot._next_spontaneous_at, time.time())

    def test_sleep_disables_auto_speech_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._sleeping_until = time.time() + 1000
            bot._schedule_spontaneous()
            self.assertEqual(bot._next_spontaneous_at, 0)
            bot._wake()
            self.assertGreater(bot._next_spontaneous_at, time.time())

    def test_force_sleep_uses_last_group_and_bypasses_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.groups = [1, 2]
            bot._group_last_trigger = {1: time.time() - 100, 2: time.time()}
            captured = []
            bot._go_sleep = lambda targets=None, force=False: captured.append((targets, force))
            bot._force_sleep_job([2])
            self.assertEqual(captured, [([2], True)])
            self.assertFalse(bot._force_sleep_running)

    def test_force_sleep_does_nothing_while_sleeping(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._sleeping_until = time.time() + 1000
            result = bot.start_force_sleep()
            self.assertEqual(result["status"], "sleeping")
            self.assertIsNone(bot._force_sleep_thread)

    def test_sleep_prep_only_uses_last_triggered_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.groups = [1, 2]
            bot._group_last_trigger = {1: time.time() - 100, 2: time.time()}
            self.assertEqual(bot._sleep_prep_targets(), [2])
            bot._group_last_trigger = {}
            self.assertEqual(bot.start_sleep_prep()["status"], "no_group")
            self.assertIsNone(bot._sleep_prep_thread)

    def test_sleep_prep_falls_back_to_command_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.groups = [1, 2]
            bot._group_last_trigger = {}
            self.assertEqual(bot._sleep_prep_targets(2), [2])
            bot._group_last_trigger = {1: time.time()}
            self.assertEqual(bot._sleep_prep_targets(2), [1])

    def test_sleep_prep_does_nothing_while_sleeping(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._sleeping_until = time.time() + 1000
            result = bot.start_sleep_prep()
            self.assertEqual(result["status"], "sleeping")
            self.assertFalse(bot._sleep_prep_running)
            self.assertIsNone(bot._sleep_prep_thread)

    def test_sleep_prep_generates_prompt_and_starts_countdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._group_last_trigger = {1: time.time()}
            bot._spontaneous_waiting = False
            bot._spontaneous_at = 0.0
            bot._spontaneous_groups = set()
            captured = []
            bot._send_spontaneous = lambda group_id, prompt=None, purpose=None: (
                captured.append((group_id, prompt, purpose)) or True)
            bot._sleep_prep_job()
            self.assertEqual(captured[0][0], 1)
            self.assertIn("准备去睡", captured[0][1])
            self.assertEqual(captured[0][2], "sleep_prep")
            self.assertTrue(bot._spontaneous_waiting)
            self.assertGreater(bot._spontaneous_at, 0.0)
            self.assertEqual(bot._spontaneous_groups, {1})

    def test_handle_sleep_wakes_after_six_trigger_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._sleeping_until = time.time() + 1000
            keyword = bot.cfg["trigger"]["keywords"][0]
            forced = []
            bot._force_wake_for_noise = lambda group_id: forced.append(group_id)
            for index in range(6):
                bot.handle_group_message({
                    "group_id": 1,
                    "user_id": 2,
                    "message_id": "sleep-%d" % index,
                    "time": int(time.time()),
                    "sender": {"user_id": 2, "nickname": "群友"},
                    "message": [{"type": "text", "data": {"text": keyword}}],
                })
            self.assertEqual(forced, [1])

    def test_force_wake_for_noise_greets_only_trigger_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot._sleep_state_path = os.path.join(tmp, "sleep_state.json")
            bot._sleeping_until = time.time() + 1000
            captured = {}
            bot._announce_wake_group = lambda group_id, prompt: captured.update(
                {"group_id": group_id, "prompt": prompt})
            bot._force_wake_for_noise(1)
            bot._wake_greeting_thread.join(1)
            self.assertEqual(bot._sleeping_until, 0)
            self.assertEqual(captured["group_id"], 1)
            self.assertIn("这个群", captured["prompt"])

    def test_natural_wake_greets_all_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            captured = {}
            bot._announce_wake_all = lambda prompt: captured.update({"prompt": prompt})
            bot._start_wake_greeting(0, "natural")
            bot._wake_greeting_thread.join(1)
            self.assertIn("刚睡醒", captured["prompt"])

    def test_panel_wake_greets_only_target_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.groups = [1, 2]
            bot._sleep_state_path = os.path.join(tmp, "sleep_state.json")
            bot._sleeping_until = time.time() + 1000
            captured = {}
            bot._announce_wake_group = lambda group_id, prompt: captured.update(
                {"group_id": group_id, "prompt": prompt})
            self.assertTrue(bot.force_wake(2))
            bot._wake_greeting_thread.join(1)
            self.assertEqual(captured["group_id"], 2)
            self.assertIn("叫醒", captured["prompt"])

    def test_wake_greeting_sends_to_all_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            bot.groups = [1, 2, 3]
            sent = []
            bot._send_spontaneous = lambda group_id, prompt=None, purpose=None: (
                sent.append((group_id, purpose)) or True)
            bot._announce_wake_all("hi")
            self.assertEqual(sent, [(1, "wakeup"), (2, "wakeup"), (3, "wakeup")])

    def test_farewell_uses_model_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            captured = []
            bot._generate_reply = lambda entry, group_id: captured.append(entry["text"]) or "晚安"
            bot._send_reply = lambda entry, group_id, text: captured.append(text)
            self.assertTrue(bot._send_farewell(1, 7200))
            self.assertIn("小睡", captured[0])
            self.assertIn("不要解释", captured[0])
            self.assertEqual(captured[1], "晚安")
            captured.clear()
            self.assertTrue(bot._send_farewell(1, 21600))
            self.assertIn("大睡", captured[0])

    def test_go_sleep_generates_farewell_for_each_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            sent = []
            bot._spontaneous_groups = {1, 2}
            bot._send_farewell = lambda group_id, sleep_sec, force=False: sent.append(group_id) or True
            bot.save_context_now = lambda: []
            bot.lm.unload_model = lambda: {"status": "unloaded"}
            bot._go_sleep()
            self.assertEqual(sent, [1, 2])
            self.assertTrue(bot._model_unloaded)
            self.assertGreater(bot._sleeping_until, time.time() + 7000)
            self.assertLess(bot._sleeping_until, time.time() + 21700)
            self.assertTrue(os.path.isfile(bot._sleep_state_path))


class FatalRestartTests(unittest.TestCase):
    def test_duplicate_sentences(self):
        self.assertTrue(aiqq.has_duplicate_sentences("第一句。第二句。第二句。"))
        self.assertTrue(aiqq.has_duplicate_sentences("你好。\n你好。"))
        self.assertFalse(aiqq.has_duplicate_sentences("第一句。第二句。"))

    def test_fatal_restart_and_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
                "qq": {"groups": [1]},
                "lm_studio": {"context_save_dir": os.path.join(tmp, "context")},
            })
            bot = aiqq.Bot(cfg)
            bot._restart_notice_path = os.path.join(tmp, "restart_notice.json")
            sent = []
            reloaded = []
            restarted = []
            bot._send_plain_text = lambda group_id, text: sent.append((group_id, text))
            bot.save_context_now = lambda: []
            bot.lm.reload_model = lambda: reloaded.append(True)
            bot.request_restart = lambda: restarted.append(True)
            bot._fatal_restart(1)
            self.assertEqual(sent[0], (1, "致命错误，正在重启"))
            self.assertEqual(reloaded, [True])
            self.assertEqual(restarted, [True])
            self.assertTrue(os.path.isfile(bot._restart_notice_path))
            notice_sent = []
            bot._send_plain_text = lambda group_id, text: notice_sent.append((group_id, text))
            bot._restart_notice_loop()
            self.assertEqual(notice_sent[0], (1, "重启完成"))
            self.assertFalse(os.path.isfile(bot._restart_notice_path))


class AdminCommandTests(unittest.TestCase):
    def make_bot(self, tmp, users=None, groups=None):
        cfg = aiqq.deep_merge(aiqq.DEFAULT_CONFIG, {
            "qq": {"groups": [1], "admin_users": users or [],
                   "admin_groups": groups or [],
                   "admin_panel_url": "http://127.0.0.1:8090"},
            "lm_studio": {"context_save_dir": os.path.join(tmp, "context"),
                          "sleep_state_file": os.path.join(tmp, "sleep_state.json")},
        })
        return aiqq.Bot(cfg)

    def test_admin_defaults_to_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            self.assertTrue(bot._is_admin(123, 456))
            self.assertTrue(bot._is_admin(123, 0))

    def test_standard_command_forwarded_for_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp, users=[999], groups=[888])
            captured = {}

            class Response(object):
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return b'{"ok": true}'

            def fake_urlopen(request, timeout=None):
                captured["url"] = request.full_url
                return Response()

            old = aiqq.urllib.request.urlopen
            aiqq.urllib.request.urlopen = fake_urlopen
            try:
                handled = bot._handle_admin_command({
                    "message_type": "group", "group_id": 1,
                    "user_id": 123, "message_id": "m2",
                    "message": [{"type": "text", "data": {"text": "/控制面板.图库"}}],
                })
            finally:
                aiqq.urllib.request.urlopen = old
            self.assertTrue(handled)
            self.assertIn("/api/admin-command", captured["url"])

    def test_admin_user_and_group_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp, users=[100], groups=[200])
            self.assertTrue(bot._is_admin(100, 1))
            self.assertTrue(bot._is_admin(999, 200))
            self.assertFalse(bot._is_admin(999, 1))

    def test_admin_command_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_bot(tmp)
            captured = {}

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return b'{"ok": true}'

            def fake_urlopen(request, timeout=None):
                captured["url"] = request.full_url
                captured["data"] = json.loads(request.data.decode("utf-8"))
                captured["token"] = request.headers.get("X-aiqq-admin-token")
                return Response()

            old = aiqq.urllib.request.urlopen
            aiqq.urllib.request.urlopen = fake_urlopen
            try:
                handled = bot._handle_admin_command({
                    "message_type": "group", "group_id": 1,
                    "user_id": 123, "message_id": "m1",
                    "message": [{"type": "text", "data": {"text": "/控制面板.图库"}}],
                })
            finally:
                aiqq.urllib.request.urlopen = old
            self.assertTrue(handled)
            self.assertEqual(captured["data"]["command"], "图库")
            self.assertEqual(captured["data"]["message_type"], "group")
            self.assertTrue(captured["url"].endswith("/api/admin-command"))


class ConfigTests(unittest.TestCase):
    def test_lm_base_normalization(self):
        self.assertEqual(aiqq.normalize_lm_base("http://127.0.0.1:1234"), "http://127.0.0.1:1234/v1")
        self.assertEqual(aiqq.normalize_lm_base("http://127.0.0.1:1234/v1/"), "http://127.0.0.1:1234/v1")

    def test_deep_merge(self):
        merged = aiqq.deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}})
        self.assertEqual(merged["a"]["b"], 1)
        self.assertEqual(merged["a"]["c"], 3)

    def test_pick_chat_model(self):
        models = ["text-embedding-nomic", "qwen3-9b", "bge-rerank"]
        self.assertEqual(aiqq.pick_chat_model(models), "qwen3-9b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
