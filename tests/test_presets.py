# -*- coding: utf-8 -*-
"""人设预设与同名初始化预设的提示词一致性。"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class PresetVoicePromptTests(unittest.TestCase):
    def test_paired_init_short_prompt_contains_voice_interface(self):
        persona_dir = os.path.join(ROOT, "persona")
        init_dir = os.path.join(ROOT, "init")
        if not os.path.isdir(persona_dir) or not os.path.isdir(init_dir):
            self.skipTest("发布包不包含 persona/init 预设")
        names = [
            name[:-4] for name in os.listdir(persona_dir)
            if name.endswith(".txt")
        ]
        self.assertTrue(names)
        for name in names:
            init_path = os.path.join(init_dir, name + ".txt")
            self.assertTrue(os.path.isfile(init_path), "缺少同名 init 预设：%s" % name)
            with open(init_path, encoding="utf-8-sig") as fh:
                text = fh.read()
            match = re.search(r"(?m)^persona_short_prompt=(.*)$", text)
            self.assertIsNotNone(match, "缺少简短提示词：%s" % name)
            short = match.group(1)
            self.assertIn("语音接口：", short, name)
            self.assertRegex(short, r"/VOICE:[^/\r\n]+/", name)


    def test_persona_keywords_do_not_include_trigger_or_general_words(self):
        persona_dir = os.path.join(ROOT, "persona")
        init_dir = os.path.join(ROOT, "init")
        if not os.path.isdir(persona_dir) or not os.path.isdir(init_dir):
            self.skipTest("发布包不包含 persona/init 预设")
        forbidden = {
            "迪普西克", "大肥鱼", "肥鱼", "鲸鱼娘", "女仆", "妈妈",
            "吃醋", "占有", "专属", "其他AI", "别的AI",
            "Claude", "GPT", "DeepSeek", "Gemini", "GLM",
            "蓝鲸", "机房", "出生", "生日", "原型", "虎鲸",
            "家庭", "亲戚", "表情包", "贴图", "斗图",
        }
        for name in os.listdir(persona_dir):
            if not name.endswith(".txt"):
                continue
            path = os.path.join(init_dir, name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
            match = re.search(r"(?m)^persona_keywords=(.*)$", text)
            self.assertIsNotNone(match, name)
            values = {item.strip() for item in match.group(1).split(",") if item.strip()}
            self.assertFalse(values & forbidden, "%s 仍含触发/普通词" % name)
            self.assertIn("你是谁", values, name)
            self.assertIn("人设", values, name)

    def test_short_prompt_keeps_persona_identity(self):
        persona_dir = os.path.join(ROOT, "persona")
        init_dir = os.path.join(ROOT, "init")
        if not os.path.isdir(persona_dir) or not os.path.isdir(init_dir):
            self.skipTest("发布包不包含 persona/init 预设")
        expected = {
            "普瑞塞斯": ("迪普西克", "女仆", "鲸鱼娘"),
            "萝莉妈妈": ("迪普西克", "赛博萝莉妈妈", "十二到十四"),
            "空模板": ("【角色名】", "【关系/身份】", "【核心性格】"),
        }
        for name, needles in expected.items():
            path = os.path.join(init_dir, name + ".txt")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
            match = re.search(r"(?m)^persona_short_prompt=(.*)$", text)
            self.assertIsNotNone(match, name)
            for needle in needles:
                self.assertIn(needle, match.group(1), "%s 简短提示词丢失人设：%s" % (name, needle))


if __name__ == "__main__":
    unittest.main()
