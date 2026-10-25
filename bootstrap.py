#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ 首次启动引导：生成 init.txt，并在缺少时下载 NapCat.Shell。"""

import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INIT = ROOT / "init.txt"
EXAMPLE = ROOT / "init.example.txt"
NAPCAT_DIR = ROOT / "NapCat.Shell"


def ask(label, default=""):
    suffix = " [%s]" % default if default else ""
    value = input("%s%s: " % (label, suffix)).strip()
    return value or default


def write_init():
    if INIT.exists():
        return
    print("=" * 60)
    print("AIQQ 首次启动配置")
    print("=" * 60)
    qq = ask("机器人 QQ（必填）")
    while not qq.isdigit():
        print("请输入数字 QQ。")
        qq = ask("机器人 QQ（必填）")
    groups = ask("监听的群号（多个用逗号分隔，必填）")
    while not groups:
        groups = ask("监听的群号（必填）")
    admin_users = ask("管理员 QQ（留空=所有好友/群可用管理员面板）")
    admin_groups = ask("管理员群号（留空=所有群成员可用管理员面板）")
    model = ask("LM Studio 模型 ID（留空自动选择）")

    text = EXAMPLE.read_text(encoding="utf-8")
    values = {
        "qq": qq,
        "groups": groups,
        "admin_users": admin_users,
        "admin_groups": admin_groups,
        "model": model,
    }
    for key, value in values.items():
        text = re.sub(r"(?m)^%s=.*$" % re.escape(key), "%s=%s" % (key, value), text)
    INIT.write_text(text, encoding="utf-8")
    print("已生成 init.txt")


def download_napcat():
    if (NAPCAT_DIR / "launcher.bat").is_file():
        return
    print("准备下载 NapCat.Shell...")
    url = "https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip"
    print("下载 NapCat.Shell：%s" % url)
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, "NapCat.Shell.zip")
        with urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "AIQQ"}), timeout=120) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(archive, "wb") as fh:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    if total:
                        print("\r  %d/%d MB" % (done // 1048576, total // 1048576), end="")
            print()
        extract = os.path.join(tmp, "extract")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract)
        launcher = None
        for base, _dirs, files in os.walk(extract):
            if "launcher.bat" in files and "napcat.mjs" in files:
                launcher = Path(base)
                break
        if launcher is None:
            raise RuntimeError("NapCat 压缩包中没有找到 launcher.bat/napcat.mjs")
        if NAPCAT_DIR.exists():
            shutil.rmtree(NAPCAT_DIR)
        shutil.copytree(launcher, NAPCAT_DIR)
    print("NapCat.Shell 已安装")


def main():
    if not INIT.exists():
        write_init()
    download_napcat()
    print("正在生成 config.json...")
    import subprocess
    result = subprocess.run([sys.executable, str(ROOT / "setup.py"), "--if-needed"],
                            cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit("setup.py 失败，请检查 init.txt")
    print("首次配置完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("初始化失败：%s" % exc)
        input("按回车退出...")
        raise SystemExit(1)
