# -*- coding: utf-8 -*-
"""控制面板文件读写测试。"""

import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import panel  # noqa: E402


class AdminPanelModeTests(unittest.TestCase):
    def test_standard_and_admin_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            standard = store._help_text(False)
            admin = store._help_text(True)
            self.assertIn("控制面板-一般用户", standard)
            self.assertNotIn("删除所有上下文", standard)
            self.assertIn("/控制面板.图库.图片发送.图库中图片名", standard)
            self.assertGreater(standard.index("/控制面板.图库.图片发送"),
                               standard.index("/控制面板.图库\n"))
            self.assertLess(standard.index("/控制面板.图库.图片发送"),
                            standard.index("/控制面板.上下文\n"))
            self.assertNotIn("图片存入", standard)
            self.assertIn("控制面板-管理员", admin)
            self.assertIn("/控制面板.图库.图片发送.图库中图片名", admin)
            self.assertIn("/控制面板.图库.图片存入.图片名.描述.喜爱程度", admin)
            self.assertLess(admin.index("/控制面板.图库.图片存入"),
                            admin.index("/控制面板.上下文\n"))
            self.assertIn("上下文.删除本群上下文", admin)
            self.assertIn("上下文.删除所有上下文", admin)
            self.assertIn("/控制面板.人设预设", admin)
            self.assertIn("/控制面板.载入人设.人设名称", admin)
            self.assertIn("/控制面板.人设预设", store._help_text(False))
            self.assertNotIn("/控制面板.载入人设", store._help_text(False))
            self.assertLess(admin.index("删除本用户上下文"),
                            admin.index("删除指定用户上下文"))
            self.assertLess(admin.index("删除指定用户上下文"),
                            admin.index("删除本群上下文"))
            self.assertIn("重启.机器人", admin)
            self.assertLess(admin.index("删除本群上下文"), admin.index("/控制面板.监控"))
            self.assertLess(admin.index("重启.机器人"), admin.index("/控制面板.睡眠"))

    def test_admin_account_and_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"qq": {"admin_users": [100000001],
                                  "admin_groups": [200000001]}}, fh)
            store = panel.PanelStore(tmp)
            self.assertTrue(store._admin_allowed(
                {"user_id": 100000001, "group_id": 1}))
            self.assertTrue(store._admin_allowed(
                {"user_id": 1, "group_id": 200000001}))
            self.assertFalse(store._admin_allowed(
                {"user_id": 1, "group_id": 2}))


    def test_non_admin_cannot_run_admin_only_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            sent = []
            store._admin_send_text = lambda event, text: sent.append(text)
            store._run_admin_command(
                {"user_id": 1, "group_id": 2}, "上下文.删除所有上下文", False)
            self.assertTrue(sent)
            self.assertIn("无权限使用管理员指令", sent[0])

    def test_help_for_admin_and_standard(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            sent = []
            store._admin_send_text = lambda event, text: sent.append(text)
            store._run_admin_command({"user_id": 1, "group_id": 2}, "帮助", False)
            store._run_admin_command({"user_id": 1, "group_id": 2}, "帮助", True)
            self.assertIn("控制面板-一般用户", sent[0])
            self.assertIn("控制面板-管理员", sent[1])
            self.assertIn("重启.机器人", sent[1])


    def test_standard_image_send_uses_gallery_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "img"))
            with open(os.path.join(tmp, "img", "demo.png"), "wb") as fh:
                fh.write(b"png-bytes")
            with open(os.path.join(tmp, "img", "index.json"), "w", encoding="utf-8") as fh:
                json.dump([{"name": "测试图", "file": "demo.png",
                            "description": "描述", "favorite": 80}], fh)
            store = panel.PanelStore(tmp)
            captured = {}
            store._admin_send_image = lambda event, raw: captured.update({"raw": raw})
            store._run_admin_command(
                {"user_id": 1, "group_id": 2}, "图库.图片发送.测试图", False)
            self.assertEqual(captured["raw"], b"png-bytes")

    def test_non_admin_cannot_delete_specified_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            sent = []
            store._admin_send_text = lambda event, text: sent.append(text)
            store._run_admin_command(
                {"user_id": 1, "group_id": 2},
                "上下文.删除指定用户上下文.12345", False)
            self.assertTrue(sent)
            self.assertIn("无权限使用管理员指令", sent[0])

    def test_admin_can_delete_specified_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            sent = []
            deleted = []
            store._admin_send_text = lambda event, text: sent.append(text)
            store.delete_user_context = lambda user_id: deleted.append(user_id)
            store._run_admin_command(
                {"user_id": 1, "group_id": 2},
                "上下文.删除指定用户上下文.12345", True)
            self.assertEqual(sent, ["收到"])
            self.assertEqual(deleted, [12345])

    def test_napcat_status_qr_when_not_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"onebot": {"api_url": "http://127.0.0.1:1",
                                      "token": "t"}}, fh)
            store = panel.PanelStore(tmp)
            store._napcat_qr_payload = lambda: {
                "code": 0, "data": {"qrcode": "data:image/png;base64,abc"}}
            status = store.napcat_status()
            self.assertTrue(status["running"])
            self.assertFalse(status["logged_in"])
            self.assertEqual(status["qrcode"], "data:image/png;base64,abc")
            self.assertEqual(store._find_qr_value(
                {"data": {"qrCode": "xyz"}}), "xyz")

    def test_napcat_status_logged_in_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"onebot": {"api_url": "http://127.0.0.1:1",
                                      "token": "t"}}, fh)
            store = panel.PanelStore(tmp)
            store._napcat_qr_payload = lambda: {
                "code": -1, "message": "QQ Is Logined"}
            status = store.napcat_status()
            self.assertTrue(status["logged_in"])

    def test_shutdown_napcat_keeps_user_qq(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            calls = []
            old_run = panel.subprocess.run
            panel.subprocess.run = lambda args, **kwargs: calls.append(list(args)) or None
            try:
                store._kill_napcat()
            finally:
                panel.subprocess.run = old_run
            images = [item[-1] for item in calls]
            self.assertIn("NapCatWinBootMain.exe", images)
            self.assertNotIn("QQ.exe", images)

    def test_persona_preset_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "persona"))
            with open(os.path.join(tmp, "persona", "温柔.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("温柔人格")
            store = panel.PanelStore(tmp)
            sent = []
            store._admin_send_text = lambda event, text: sent.append(text)
            store._run_admin_command(
                {"user_id": 1, "group_id": 2}, "人设预设", False)
            self.assertIn("温柔", sent[-1])
            store._run_admin_command(
                {"user_id": 1, "group_id": 2}, "载入人设.温柔", True)
            self.assertEqual(sent[-1], "已载入人设：温柔")
            self.assertEqual(
                store.read("persona.txt").strip(), "温柔人格")
            store._run_admin_command(
                {"user_id": 1, "group_id": 2}, "载入人设.温柔", False)
            self.assertIn("无权限", sent[-1])

    def test_persona_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "persona"))
            with open(os.path.join(tmp, "persona", "温柔.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("温柔人格")
            store = panel.PanelStore(tmp)
            presets = store.list_persona_presets()
            self.assertEqual([item["name"] for item in presets], ["温柔"])
            loaded = store.read_persona_preset("温柔")
            self.assertEqual(loaded["content"].strip(), "温柔人格")
            store.write_persona_preset("新预设", "新内容")
            self.assertEqual(
                open(os.path.join(tmp, "persona", "新预设.txt"),
                     encoding="utf-8").read().strip(), "新内容")
            with self.assertRaises(panel.PanelError):
                store.read_persona_preset("../逃逸")

    def test_activate_persona_with_paired_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "persona"))
            os.makedirs(os.path.join(tmp, "init"))
            with open(os.path.join(tmp, "init", "空模板.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("qq=auto\ngroups=\n")
            with open(os.path.join(tmp, "persona", "成对.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("人格内容")
            with open(os.path.join(tmp, "init", "成对.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("groups=123456\nblock_keywords=广告\n")
            store = panel.PanelStore(tmp)
            applied = []
            restarted = []
            store.apply_init = lambda: (applied.append(True) or {"ok": True})
            store.restart_bot = lambda: (restarted.append(True) or {"ok": True})
            result = store.activate_persona_preset("成对")
            self.assertTrue(result["init"])
            self.assertEqual(result["persona_content"].strip(), "人格内容")
            self.assertEqual(store.read("persona.txt").strip(), "人格内容")
            self.assertIn("block_keywords=广告", store.read("init.txt"))
            self.assertEqual(len(applied), 1)
            self.assertEqual(len(restarted), 1)

            os.remove(os.path.join(tmp, "init", "成对.txt"))
            result = store.activate_persona_preset("成对")
            self.assertFalse(result["init"])
            self.assertEqual(len(applied), 1)
            self.assertEqual(len(restarted), 1)

            with open(os.path.join(tmp, "init", "成对.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("groups=\n")
            result = store.activate_persona_preset("成对")
            self.assertFalse(result["init"])
            self.assertTrue(result.get("template_loaded"))
            self.assertIn("空模板", result.get("warning", ""))
            self.assertIn("groups=", store.read("init.txt"))
            self.assertEqual(len(applied), 1)

    def test_napcat_webui_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "NapCat.Shell", "config"))
            with open(os.path.join(tmp, "NapCat.Shell", "config", "webui.json"),
                      "w", encoding="utf-8") as fh:
                json.dump({"host": "0.0.0.0", "port": 6099,
                           "token": "a b"}, fh)
            store = panel.PanelStore(tmp)
            self.assertEqual(
                store._napcat_webui_url(),
                "http://127.0.0.1:6099/webui?token=a%20b")

    def test_init_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "init"))
            with open(os.path.join(tmp, "init", "本地.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("groups=1\nblock_keywords=广告\n")
            store = panel.PanelStore(tmp)
            presets = store.list_init_presets()
            self.assertEqual([item["name"] for item in presets], ["本地"])
            loaded = store.read_init_preset("本地")
            self.assertIn("block_keywords=广告", loaded["content"])
            store.write_init_preset("新初始化", "groups=2\n")
            self.assertEqual(
                open(os.path.join(tmp, "init", "新初始化.txt"),
                     encoding="utf-8").read().strip(), "groups=2")
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "init", "新初始化.txt")))
            with self.assertRaises(panel.PanelError):
                store.read_init_preset("../逃逸")

    def test_non_admin_cannot_store_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            sent = []
            store._admin_send_text = lambda event, text: sent.append(text)
            store._run_admin_command(
                {"user_id": 1, "group_id": 2, "images": [{"url": "x"}]},
                "图库.图片存入.测试.描述.80", False)
            self.assertTrue(sent)
            self.assertIn("无权限使用管理员指令", sent[0])

    def test_sleep_command_feedback_matches_web_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            sent = []
            store._admin_send_text = lambda event, text: sent.append(text)
            store._admin_send_sleep_result({}, "睡眠", {"status": "no_group"})
            store._admin_send_sleep_result({}, "预备", {"status": "running"})
            store._admin_send_sleep_result({}, "唤醒", {"was_sleeping": True})
            self.assertEqual(sent, ["没有可用的触发群", "睡眠预备正在进行中", "已强制结束睡眠"])


class PanelStoreTests(unittest.TestCase):
    def test_write_read_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            write = store.write("persona.txt", "你是鲸鲸")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "persona.txt")))
            self.assertEqual(store.read("persona.txt"), "你是鲸鲸\n")
            self.assertTrue(write["backup"] == "")

            write = store.write("persona.txt", "新的人设")
            self.assertTrue(os.path.isfile(write["backup"]))
            self.assertEqual(store.read("persona.txt"), "新的人设\n")

    def test_rejects_invalid_names_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            with self.assertRaises(panel.PanelError):
                store.read("../config.json")
            with self.assertRaises(panel.PanelError):
                store.read("config.json")
            with self.assertRaises(panel.PanelError):
                store.write("image_index.txt", "x")
            with self.assertRaises(panel.PanelError):
                store.write("config.json", "{}")

    def test_panel_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            data = store.panel_settings()
            self.assertFalse(data["lan_enabled"])
            self.assertEqual(data["port"], 8090)
            self.assertFalse(data["password_set"])
            with self.assertRaises(panel.PanelError):
                store.save_panel_settings(True, "", 8090)
            saved = store.save_panel_settings(True, "secret", 8091)
            self.assertTrue(saved["lan_enabled"])
            self.assertTrue(saved["password_set"])
            self.assertEqual(saved["port"], 8091)
            self.assertNotIn("password", saved)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "panel_settings.json")))
            with open(os.path.join(tmp, "panel_settings.json"), encoding="utf-8") as fh:
                self.assertIn("secret", fh.read())

    def test_init_save_skips_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = panel.PanelStore(tmp)
            store.write("init.txt", "qq=123456789\ngroups=987654321\n")
            result = store.write("init.txt", "unknown_key=1\n")
            self.assertIn("path", result)
            with open(os.path.join(tmp, "init.txt"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "unknown_key=1\n")
            with self.assertRaises(panel.PanelError):
                store.start_script("nope")

    def test_image_update_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "img"))
            image_path = os.path.join(tmp, "img", "red.png")
            with open(image_path, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
            with open(os.path.join(tmp, "img", "index.json"), "w", encoding="utf-8") as fh:
                json.dump([{"name": "红蓝方块", "description": "红底蓝方块",
                            "file": "red.png", "created_at": "2026-01-01 00:00:00"}], fh,
                          ensure_ascii=False)
            store = panel.PanelStore(tmp)
            store.update_image("红蓝方块", new_name="新名字", description="新描述", favorite=88)
            state = store.state()
            self.assertEqual(state["images"][0]["name"], "新名字")
            self.assertEqual(state["images"][0]["description"], "新描述")
            self.assertEqual(state["images"][0]["favorite"], 88)
            self.assertTrue(os.path.exists(os.path.join(tmp, "img", "新名字.png")))
            with open(os.path.join(tmp, "image_index.txt"), encoding="utf-8") as fh:
                self.assertIn("新名字：新描述（喜爱 88）", fh.read())
            store.delete_image("新名字")
            self.assertFalse(os.path.exists(os.path.join(tmp, "img", "新名字.png")))
            self.assertEqual(store.state()["images"], [])

    def test_panel_html_title_and_auto_restart(self):
        with open(os.path.join(ROOT, "panel.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("<title>控制面板</title>", html)
        self.assertNotIn("<title>AIQQ", html)
        self.assertIn('id="autoRestart"', html)
        self.assertIn('id="restartModelBtn"', html)
        self.assertIn('id="restartBotBtn"', html)
        self.assertIn('id="saveContextBtn"', html)
        self.assertIn('id="settingsBtn"', html)
        self.assertIn('id="rawReply"', html)
        self.assertIn('id="rawHistoryTabs"', html)
        self.assertIn('id="rawHistoryList"', html)
        self.assertIn('id="browseGalleryBtn"', html)
        self.assertIn('id="browseContextBtn"', html)
        self.assertIn('id="stopBotBtn"', html)
        self.assertIn('id="clearContextBtn"', html)
        self.assertIn('id="clearSnapshotsBtn"', html)
        self.assertIn('id="closePanelBtn"', html)
        self.assertIn('id="closeAllBtn"', html)
        self.assertIn('id="napcatLoginCard"', html)
        self.assertIn('id="napcatQrImage"', html)
        self.assertIn('id="spontaneousState"', html)
        self.assertIn('id="wakeSleepBtn"', html)
        self.assertIn('id="sleepPrepBtn"', html)
        self.assertIn('id="forceSleepBtn"', html)
        self.assertIn('id="sleepQuietState"', html)
        self.assertIn(">睡眠预备<", html)
        self.assertIn('id="sleepRemaining"', html)
        self.assertIn('id="savePanelSettings"', html)
        self.assertNotIn('data-name="image_index.txt"', html)

    def test_monitor_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"qq": {"self_id": 1, "groups": [2]},
                           "onebot": {"webhook_port": 65530}}, fh)
            store = panel.PanelStore(tmp)
            data = store.monitor_state()
            self.assertIn("bot", data)
            self.assertIn("config", data)
            self.assertEqual(data["config"]["self_id"], 1)
            self.assertFalse(data["bot"]["online"])

    def test_context_list_detail_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            context_dir = os.path.join(tmp, "context")
            os.makedirs(context_dir)
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"lm_studio": {"context_save_dir": "context"}}, fh)
            for index, text in enumerate(("旧", "新")):
                path = os.path.join(context_dir, "123_20260101_00000%d_000000.json.gz" % index)
                with gzip.open(path, "wt", encoding="utf-8") as fh:
                    json.dump({"group_id": 123, "saved_at": "2026-01-01 00:00:0%d" % index,
                               "messages": [{"role": "user", "text": text}]}, fh,
                              ensure_ascii=False)
                time.sleep(0.01)
            store = panel.PanelStore(tmp)
            contexts = store.contexts()
            self.assertEqual(len(contexts), 2)
            self.assertTrue(any(item["latest"] for item in contexts))
            detail = store.context_detail(contexts[0]["file"])
            self.assertTrue(detail["messages"])
            store.delete_context(contexts[0]["file"])
            self.assertEqual(len(store.contexts()), 1)
            with self.assertRaises(panel.PanelError):
                store.context_detail("../config.json")

    def test_state_and_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "img"))
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"qq": {"self_id": 1, "groups": [2]},
                           "onebot": {"webhook_port": 65530}}, fh)
            with open(os.path.join(tmp, "img", "index.json"), "w", encoding="utf-8") as fh:
                json.dump([{"name": "红蓝方块", "description": "红底蓝方块",
                            "file": "红蓝方块.png"}], fh)
            with open(os.path.join(tmp, "img", "红蓝方块.png"), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
            store = panel.PanelStore(tmp)
            state = store.state()
            self.assertEqual(state["images"][0]["name"], "红蓝方块")
            self.assertEqual(state["images"][0]["description"], "红底蓝方块")
            self.assertGreater(state["images"][0]["size"], 0)
            self.assertEqual(state["config"]["self_id"], 1)
            self.assertFalse(state["bot"]["online"])
            info = store.image_info("红蓝方块")
            self.assertTrue(info["path"].endswith("红蓝方块.png"))
            self.assertEqual(info["mime"], "image/png")
            self.assertIsNone(store.image_info("../config.json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
