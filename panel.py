#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ 网页控制面板启动入口；具体逻辑位于 aiqq.panel_* 模块。"""

import argparse
import os
import sys
import webbrowser

from aiqq.panel_store import *
from aiqq.panel_server import *
from aiqq import panel_state

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_is_loopback = panel_state._is_loopback
_lan_ip = panel_state._lan_ip
_panel_alive = panel_state._panel_alive


def _ensure_std_streams():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="AIQQ 网页控制面板")
    parser.add_argument("--host", default=None, help="监听地址；默认读取面板设置")
    parser.add_argument("--port", type=int, default=None, help="监听端口；默认读取面板设置")
    parser.add_argument("--password", default=None, help="访问密码；默认读取面板设置")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_args(argv)

    store = PanelStore(BASE_DIR)
    settings = store._panel_settings_data()
    host = args.host if args.host is not None else (
        "0.0.0.0" if settings["lan_enabled"] else "127.0.0.1")
    port = args.port if args.port is not None else settings["port"]
    password = args.password if args.password is not None else settings["password"]

    if not _is_loopback(host) and not password:
        print("局域网监听地址缺少访问密码，暂时只监听 127.0.0.1。")
        print("启动后请在面板「面板设置」中设置密码并重新开启局域网。")
        host = "127.0.0.1"

    if args.host is not None or args.port is not None or args.password is not None:
        # 命令行覆盖也写入设置，保证面板内显示与重启后的监听一致
        store.save_panel_settings(not _is_loopback(host), password, port)

    panel_state.PANEL_PASSWORD = password or ""
    panel_state.PANEL_SCREENSHOT_TOKEN = secrets.token_urlsafe(24)
    panel_state.PANEL_IMAGE_TOKEN = secrets.token_urlsafe(24)
    PanelHandler.store = store
    if panel_state._panel_alive(port):
        print("控制面板已经在运行：http://127.0.0.1:%d" % port)
        if not args.no_browser:
            _open_panel_browser(port)
        return 0
    runtime = PanelRuntime(store, host, port, password)
    panel_state.PANEL_RUNTIME = runtime
    try:
        runtime.start()
    except OSError as exc:
        if panel_state._panel_alive(port):
            print("控制面板已经在运行：http://127.0.0.1:%d" % port)
            return 0
        print("无法监听 %s:%d：%s" % (host, port, exc))
        return 1

    if panel_state.PANEL_PASSWORD:
        print("已启用访问密码")
    if not args.no_browser:
        _open_panel_browser(port)
    print("按 Ctrl+C 停止")
    try:
        runtime.wait()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
