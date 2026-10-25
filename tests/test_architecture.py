# -*- coding: utf-8 -*-
"""模块边界与入口文件约束。"""

import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot
import panel


class ArchitectureTests(unittest.TestCase):
    def test_internal_modules_do_not_use_wildcard_imports(self):
        pkg = os.path.join(ROOT, "aiqq")
        for name in os.listdir(pkg):
            if not name.endswith(".py") or name == "__init__.py":
                continue
            with open(os.path.join(pkg, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIsNone(
                re.search(r"from \.[A-Za-z0-9_]+ import \*", text),
                "%s 使用了包内通配导入" % name,
            )

    def test_entrypoints_stay_thin(self):
        for name, limit in (("bot.py", 300), ("panel.py", 300)):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                self.assertLess(fh.read().count("\n") + 1, limit)

    def test_bot_is_composed_from_domain_mixins(self):
        names = {base.__name__ for base in bot.Bot.__mro__}
        self.assertIn("BotSleepMixin", names)
        self.assertIn("BotGenerationMixin", names)
        self.assertIn("BotOutgoingMixin", names)
        self.assertIn("BotPersonaMixin", names)

    def test_panel_store_is_composed_from_domain_mixins(self):
        names = {base.__name__ for base in panel.PanelStore.__mro__}
        self.assertIn("PanelConfigMixin", names)
        self.assertIn("PanelPresetsMixin", names)
        self.assertIn("PanelCommandsMixin", names)
        self.assertIn("PanelGalleryMixin", names)

    def test_internal_modules_have_docstrings(self):
        pkg = os.path.join(ROOT, "aiqq")
        for name in os.listdir(pkg):
            if not name.endswith(".py") or name == "__init__.py":
                continue
            with open(os.path.join(pkg, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            self.assertTrue(ast.get_docstring(tree), "%s 缺少模块文档字符串" % name)


if __name__ == "__main__":
    unittest.main()
