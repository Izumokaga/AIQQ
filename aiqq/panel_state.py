#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_state：面板共享运行状态与地址辅助函数。"""
import ipaddress
import socket
import urllib.error
import urllib.request

PANEL_PASSWORD = ""
PANEL_RUNTIME = None
PANEL_SCREENSHOT_TOKEN = ""


def _is_loopback(host):
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
def _lan_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""
def _panel_alive(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1) as response:
            return "AIQQPanel" in (response.headers.get("Server") or "")
    except urllib.error.HTTPError as exc:
        return "AIQQPanel" in (exc.headers.get("Server") or "")
    except Exception:  # noqa: BLE001
        return False
__all__ = [name for name in globals() if not name.startswith("__")]
