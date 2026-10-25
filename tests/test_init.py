#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init.txt 解析与初始化流程测试。"""

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import setup as init_setup  # noqa: E402


class InitParsingTests(unittest.TestCase):
    def test_basic(self):
        data = init_setup.parse_init_text(
            "qq=100000003\n# 注释\ngroups=123456789, 987654321\n"
            "keywords=小助手,机器人\nlm_url=http://127.0.0.1:1234/v1\n"
        )
        self.assertEqual(data["qq"], "100000003")
        self.assertEqual(data["groups"], "123456789, 987654321")
        self.assertEqual(data["keywords"], "小助手,机器人")
        self.assertEqual(data["lm_url"], "http://127.0.0.1:1234/v1")

    def test_chinese_keys_and_fullwidth_colon(self):
        text = (
            "机器人QQ：100000003\n"
            "群号=123456789\n"
            "关键词=小助手,机器人\n"
            "LM Studio 地址：http://127.0.0.1:1234/v1\n"
            "模型=my-model\n"
            "端口=8080\n"
        )
        data = init_setup.parse_init_text(text)
        self.assertEqual(data["qq"], "100000003")
        self.assertEqual(data["groups"], "123456789")
        self.assertEqual(data["keywords"], "小助手,机器人")
        self.assertEqual(data["lm_url"], "http://127.0.0.1:1234/v1")
        self.assertEqual(data["model"], "my-model")
        self.assertEqual(data["webhook_port"], "8080")

    def test_inline_comment_and_quotes(self):
        text = 'groups=123456789  # 目标群\nsystem_prompt="你是#助手"\n'
        data = init_setup.parse_init_text(text)
        self.assertEqual(data["groups"], "123456789")
        self.assertEqual(data["system_prompt"], "你是#助手")

    def test_duplicate_last_wins(self):
        data = init_setup.parse_init_text("qq=111111\nqq=222222\n")
        self.assertEqual(data["qq"], "222222")

    def test_unknown_key(self):
        with self.assertRaises(init_setup.InitError) as ctx:
            init_setup.parse_init_text("foo=123456789\n", source="test.txt")
        self.assertIn("未知配置项", str(ctx.exception))

    def test_bad_line(self):
        with self.assertRaises(init_setup.InitError) as ctx:
            init_setup.parse_init_text("groups\n", source="test.txt")
        self.assertIn("第 1 行", str(ctx.exception))

    def test_gbk_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "init.txt")
            with open(path, "wb") as fh:
                fh.write("qq=100000003\n群号=123456789\n".encode("gbk"))
            data = init_setup.load_init(path)
            self.assertEqual(data["qq"], "100000003")
            self.assertEqual(data["groups"], "123456789")

    def test_utf16_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "init.txt")
            with open(path, "wb") as fh:
                fh.write("qq=100000003\ngroups=123456789\n".encode("utf-16"))
            data = init_setup.load_init(path)
            self.assertEqual(data["qq"], "100000003")
            self.assertEqual(data["groups"], "123456789")

    def test_template_has_required_keys(self):
        for key in ("qq=", "groups=", "keywords=", "block_keywords=", "lm_url=", "model=", "api_url=", "webhook_port="):
            self.assertIn(key, init_setup.INIT_TEMPLATE)


class NapCatConfigTests(unittest.TestCase):
    def test_write_napcat_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "config"))
            path = init_setup.write_napcat_config(
                tmp, 100000003, "http://127.0.0.1:3000",
                "http://127.0.0.1:8080/onebot", "testtoken")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        server = data["network"]["httpServers"][0]
        client = data["network"]["httpClients"][0]
        self.assertEqual(server["name"], "AIQQ-API")
        self.assertEqual(server["port"], 3000)
        self.assertEqual(server["token"], "testtoken")
        self.assertFalse(server["enableWebsocket"])
        self.assertEqual(client["name"], "AIQQ-Webhook")
        self.assertEqual(client["url"], "http://127.0.0.1:8080/onebot")
        self.assertEqual(client["messagePostFormat"], "array")


class InitUpgradeTests(unittest.TestCase):
    def test_create_does_not_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "init.txt")
            original = "qq=100000003\ngroups=987654321\n# 我的自定义注释\n"
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(original)
            init_setup.create_init_template(path)
            self.assertEqual(init_setup.read_text(path), original)

    def test_merge_preserves_values_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "init.txt")
            original = (
                "qq=100000003\n"
                "groups=987654321\n"
                "keywords=鱼,ds\n"
                "# 我的自定义注释\n"
            )
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(original)

            added = init_setup.merge_init_template(path)
            self.assertIn("split_marker", added)
            text = init_setup.read_text(path)
            self.assertTrue(text.startswith(original.rstrip()))
            self.assertIn("groups=987654321", text)
            self.assertIn("# 我的自定义注释", text)
            self.assertIn("split_marker=/Enter/", text)

            with open(path + ".bak", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), original)
            data = init_setup.parse_init_text(text)
            self.assertEqual(data["groups"], "987654321")
            self.assertEqual(data["keywords"], "鱼,ds")

    def test_merge_is_noop_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "init.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(init_setup.INIT_TEMPLATE)
            before = init_setup.read_text(path)
            self.assertEqual(init_setup.merge_init_template(path), [])
            self.assertEqual(init_setup.read_text(path), before)
            self.assertFalse(os.path.exists(path + ".bak"))


class InitMainTests(unittest.TestCase):
    def make_paths(self):
        tmp = tempfile.mkdtemp(prefix="aiqq-init-test-")
        return tmp, os.path.join(tmp, "init.txt"), os.path.join(tmp, "config.json")

    def run_main(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = init_setup.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_missing_init_creates_template(self):
        tmp, init_path, cfg_path = self.make_paths()
        code, out, _ = self.run_main(
            ["--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat"])
        self.assertEqual(code, 2)
        self.assertTrue(os.path.isfile(init_path))
        self.assertFalse(os.path.exists(cfg_path))
        self.assertIn("groups=", init_setup.read_text(init_path))
        self.assertIn("模板", out)

    def test_generates_config(self):
        tmp, init_path, cfg_path = self.make_paths()
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write(
                "qq=100000003\n"
                "groups=123456789,987654321\n"
                "keywords=小助手,机器人\n"
                "lm_url=http://127.0.0.1:1234/v1\n"
                "model=my-model\n"
                "api_url=http://127.0.0.1:3000\n"
                "webhook_port=8099\n"
                "system_prompt=测试人设\n"
                "persona_file=persona.txt\n"
                "persona_mode=off\n"
                "persona_keywords=鲸鲸,肥鱼\n"
                "persona_short_prompt=简短人设\n"
                "image_catalog_limit=10\n"
                "image_max_per_save=2\n"
                "image_max_per_reply=1\n"
                "to_message=true\n"
                "split_marker=/Enter/\n"
                "max_messages=5\n"
                "ignore_users=20009\n"
            )
        code, out, err = self.run_main(
            ["--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat"])
        self.assertEqual(code, 0, err)
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["qq"]["self_id"], 100000003)
        self.assertEqual(cfg["qq"]["groups"], [123456789, 987654321])
        self.assertEqual(cfg["trigger"]["keywords"], ["小助手", "机器人"])
        self.assertEqual(cfg["lm_studio"]["model"], "my-model")
        self.assertEqual(cfg["lm_studio"]["system_prompt"], "测试人设")
        self.assertEqual(cfg["lm_studio"]["persona_file"], "persona.txt")
        self.assertEqual(cfg["lm_studio"]["persona_mode"], "off")
        self.assertEqual(cfg["lm_studio"]["persona_keywords"], ["鲸鲸", "肥鱼"])
        self.assertEqual(cfg["lm_studio"]["persona_short_prompt"], "简短人设")
        self.assertEqual(cfg["images"]["catalog_limit"], 10)
        self.assertEqual(cfg["images"]["max_per_save"], 2)
        self.assertEqual(cfg["images"]["max_per_reply"], 1)
        self.assertTrue(cfg["reply"]["to_message"])
        self.assertEqual(cfg["reply"]["split_marker"], "/Enter/")
        self.assertEqual(cfg["reply"]["max_messages"], 5)
        self.assertEqual(cfg["qq"]["ignore_users"], [20009])
        self.assertEqual(cfg["onebot"]["webhook_port"], 8099)
        self.assertEqual(len(cfg["onebot"]["token"]), 16)

    def test_missing_groups_is_error(self):
        tmp, init_path, cfg_path = self.make_paths()
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\n")
        code, _, err = self.run_main(
            ["--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat"])
        self.assertEqual(code, 2)
        self.assertIn("groups", err)
        self.assertFalse(os.path.exists(cfg_path))

    def test_cli_override(self):
        tmp, init_path, cfg_path = self.make_paths()
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\ngroups=123456789\nkeywords=旧词\n")
        code, _, err = self.run_main([
            "--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat",
            "--groups", "987654321", "--keywords", "新词",
        ])
        self.assertEqual(code, 0, err)
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["qq"]["groups"], [987654321])
        self.assertEqual(cfg["trigger"]["keywords"], ["新词"])

    def test_to_message_defaults_false(self):
        tmp, init_path, cfg_path = self.make_paths()
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\ngroups=123456789\n")
        code, _, err = self.run_main(
            ["--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat"])
        self.assertEqual(code, 0, err)
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertFalse(cfg["reply"]["to_message"])

    def test_to_message_overrides_old_true(self):
        tmp, init_path, cfg_path = self.make_paths()
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\ngroups=123456789\n")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"reply": {"to_message": True}}, fh)
        code, _, err = self.run_main(
            ["--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat"])
        self.assertEqual(code, 0, err)
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertFalse(cfg["reply"]["to_message"])

    def test_if_needed_skips_when_config_is_newer(self):
        tmp, init_path, cfg_path = self.make_paths()
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\ngroups=123456789\n")
        code, _, err = self.run_main(
            ["--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat"])
        self.assertEqual(code, 0, err)
        with open(cfg_path, "rb") as fh:
            before = fh.read()

        now = time.time()
        os.utime(init_path, (now - 120, now - 120))
        code, out, err = self.run_main([
            "--init", init_path, "--config", cfg_path, "--if-needed",
            "--no-test", "--no-napcat",
        ])
        self.assertEqual(code, 0, err)
        self.assertIn("跳过初始化", out)
        with open(cfg_path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_if_needed_runs_when_init_is_newer(self):
        tmp, init_path, cfg_path = self.make_paths()
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\ngroups=123456789\n")
        code, _, err = self.run_main(
            ["--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat"])
        self.assertEqual(code, 0, err)

        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\ngroups=987654321\n")
        now = time.time()
        os.utime(init_path, (now + 120, now + 120))
        code, _, err = self.run_main([
            "--init", init_path, "--config", cfg_path, "--if-needed",
            "--no-test", "--no-napcat",
        ])
        self.assertEqual(code, 0, err)
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["qq"]["groups"], [987654321])

    def test_if_needed_repairs_napcat_config(self):
        tmp, init_path, cfg_path = self.make_paths()
        napcat = os.path.join(tmp, "NapCat.Shell")
        os.makedirs(os.path.join(napcat, "config"))
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\ngroups=123456789\n")

        code, _, err = self.run_main([
            "--init", init_path, "--config", cfg_path,
            "--napcat-dir", napcat, "--no-test",
        ])
        self.assertEqual(code, 0, err)
        napcat_path = os.path.join(napcat, "config", "onebot11_100000003.json")
        with open(napcat_path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["network"]["httpServers"][0]["name"], "AIQQ-API")

        # 模拟 NapCat 退出时把配置写回空内容
        with open(napcat_path, "w", encoding="utf-8") as fh:
            json.dump({"network": {"httpServers": [], "httpClients": []}}, fh)
        now = time.time()
        os.utime(init_path, (now - 120, now - 120))

        code, out, err = self.run_main([
            "--init", init_path, "--config", cfg_path, "--if-needed",
            "--napcat-dir", napcat, "--no-test",
        ])
        self.assertEqual(code, 0, err)
        self.assertIn("不一致", out)
        with open(napcat_path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["network"]["httpServers"][0]["name"], "AIQQ-API")

    def test_token_reused_and_groups_fallback(self):
        tmp, init_path, cfg_path = self.make_paths()
        with open(init_path, "w", encoding="utf-8") as fh:
            fh.write("qq=100000003\n")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({
                "qq": {"self_id": 100000003, "groups": [888888]},
                "onebot": {"token": "keepthis"},
            }, fh)
        code, out, err = self.run_main(
            ["--init", init_path, "--config", cfg_path, "--no-test", "--no-napcat"])
        self.assertEqual(code, 0, err)
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["onebot"]["token"], "keepthis")
        self.assertEqual(cfg["qq"]["groups"], [888888])
        self.assertIn("沿用", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
