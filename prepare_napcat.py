#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为当前 QQ 版本准备 NapCat 的入口文件。

新版 NapCat 的 launcher 在某些 QQ 版本下不会把 loadNapCat.js 放进
QQ 的 resources/app，导致 QQ 启动时报：
    Cannot find module '...\\resources\\app\\loadNapCat.js'

本脚本读取 QQ 的 versions/config.json，找到当前版本目录，写入一个只负责
加载 D:\\AIQQ\\NapCat.Shell\\napcat.mjs 的引导文件。重复运行不会产生变化。
"""

import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
NAPCAT_MAIN = BASE_DIR / "NapCat.Shell" / "napcat.mjs"


def find_qq_install():
    """优先读注册表，失败时尝试常见路径。"""
    try:
        import winreg

        key_path = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            uninstall, _ = winreg.QueryValueEx(key, "UninstallString")
        path = Path(uninstall.strip('"')).parent
        if path.is_dir():
            return path
    except Exception:
        pass

    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tencent" / "QQNT",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tencent" / "QQNT",
        Path(r"E:\Tencent\QQNT"),
    ):
        if (candidate / "versions").is_dir():
            return candidate
    return None


def main():
    if not NAPCAT_MAIN.is_file():
        print(" [提示] 未找到 NapCat.Shell/napcat.mjs，跳过入口文件准备。")
        return 0

    qq_dir = find_qq_install()
    if qq_dir is None:
        print(" [提示] 未找到 QQ 安装目录，跳过入口文件准备。")
        return 0

    config_path = qq_dir / "versions" / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8-sig") as fh:
            version = json.load(fh)["curVersion"]
    except (OSError, KeyError, ValueError) as exc:
        print(" [提示] 读取 QQ 版本失败（%s），跳过入口文件准备。" % exc)
        return 0

    app_dir = qq_dir / "versions" / version / "resources" / "app"
    if not app_dir.is_dir():
        print(" [提示] 未找到 QQ 应用目录：%s" % app_dir)
        return 0

    loader = app_dir / "loadNapCat.js"
    content = (
        "// AIQQ: load NapCat from its installation directory\n"
        "(async () => {\n"
        "  await import(%s);\n"
        "})();\n" % json.dumps(NAPCAT_MAIN.as_uri())
    )

    try:
        if loader.is_file():
            existing = loader.read_text(encoding="utf-8", errors="ignore")
            if "napcat.mjs" in existing:
                print(" [OK] NapCat 入口文件已存在：%s" % loader)
                return 0
        loader.write_text(content, encoding="utf-8", newline="\n")
        print(" [OK] 已准备 NapCat 入口文件：%s" % loader)
    except OSError as exc:
        print(" [提示] 无法写入 NapCat 入口文件：%s" % exc)
        print("        请以管理员身份运行 start.bat，或手动复制。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
