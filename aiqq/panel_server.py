#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ panel_server：面板 HTTP 处理、监听运行时与换绑。"""
import base64
import gzip
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from . import panel_state
from .panel_common import PanelError, MAX_BODY

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class PanelHandler(BaseHTTPRequestHandler):
    store = None
    server_version = "AIQQPanel/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stdout.write("[panel] " + (fmt % args) + "\n")
        sys.stdout.flush()

    def _authorized(self):
        cookie = self.headers.get("Cookie", "")
        if panel_state.PANEL_SCREENSHOT_TOKEN and cookie:
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "aiqq_screenshot" and hmac.compare_digest(
                        value, panel_state.PANEL_SCREENSHOT_TOKEN):
                    return True
        if not panel_state.PANEL_PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return False
        _, _, password = decoded.partition(":")
        return hmac.compare_digest(password, panel_state.PANEL_PASSWORD)

    def _require_auth(self):
        if self._authorized():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Control Panel"')
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        return False

    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise PanelError("Content-Length 无效")
        if length > MAX_BODY:
            raise PanelError("请求体超过 2MB")
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise PanelError("请求体不是合法 JSON：%s" % exc)
        if not isinstance(data, dict):
            raise PanelError("请求体必须是对象")
        return data

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/screenshot-login":
            params = parse_qs(parsed.query)
            token = (params.get("token") or [""])[0]
            if not panel_state.PANEL_SCREENSHOT_TOKEN or not hmac.compare_digest(
                    token, panel_state.PANEL_SCREENSHOT_TOKEN):
                self._send_json({"error": "forbidden"}, 403)
                return
            view = (params.get("view") or ["editor"])[0]
            if view not in ("library", "contexts", "monitor", "editor", "settings"):
                view = "editor"
            self.send_response(302)
            self.send_header(
                "Set-Cookie",
                "aiqq_screenshot=%s; Path=/; HttpOnly; SameSite=Lax" % panel_state.PANEL_SCREENSHOT_TOKEN)
            self.send_header("Location", "/?view=" + view)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._require_auth():
            return
        if parsed.path in ("/", "/index.html"):
            try:
                with open(os.path.join(BASE_DIR, "panel.html"), encoding="utf-8") as fh:
                    raw = fh.read().encode("utf-8")
            except OSError as exc:
                self._send_json({"error": "读取 panel.html 失败：%s" % exc}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path == "/api/state":
            self._send_json(self.store.state())
            return
        if parsed.path == "/api/debug-screenshot":
            view = (parse_qs(parsed.query).get("view") or ["library"])[0]
            try:
                path = self.store._admin_screenshot(view)
                size = os.path.getsize(path)
                os.remove(path)
                self._send_json({"ok": True, "size": size})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        if parsed.path == "/api/panel-settings":
            self._send_json(self.store.panel_settings())
            return
        if parsed.path == "/api/monitor":
            self._send_json(self.store.monitor_state())
            return
        if parsed.path == "/api/raw-history":
            try:
                self._send_json(self.store.raw_history())
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 503)
            return
        if parsed.path == "/api/napcat-status":
            try:
                self._send_json(self.store.napcat_status())
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 503)
            return
        if parsed.path == "/api/file":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                content = self.store.read(name)
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json({"name": name, "content": content,
                             "editable": name in self.store.EDITABLE})
            return
        if parsed.path == "/api/persona-presets":
            self._send_json({"presets": self.store.list_persona_presets()})
            return
        if parsed.path == "/api/persona-preset":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                result = self.store.read_persona_preset(name)
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json(result)
            return
        if parsed.path == "/api/init-presets":
            self._send_json({"presets": self.store.list_init_presets()})
            return
        if parsed.path == "/api/init-preset":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                result = self.store.read_init_preset(name)
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json(result)
            return
        if parsed.path == "/api/image":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                info = self.store.image_info(name)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
                return
            if not info:
                self._send_json({"error": "图片不存在"}, 404)
                return
            try:
                with open(info["path"], "rb") as fh:
                    raw = fh.read()
            except OSError as exc:
                self._send_json({"error": "读取图片失败：%s" % exc}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", info["mime"])
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/api/contexts":
            self._send_json({"contexts": self.store.contexts()})
            return
        if parsed.path == "/api/context":
            filename = (parse_qs(parsed.query).get("file") or [""])[0]
            try:
                self._send_json(self.store.context_detail(filename))
            except PanelError as exc:
                self._send_json({"error": str(exc)}, 404)
            return
        self._send_json({"error": "not found"}, 404)

    def _admin_bridge_authorized(self):
        config = self.store._config()
        token = str((config.get("onebot") or {}).get("token") or "")
        provided = self.headers.get("X-AIQQ-Admin-Token") or ""
        if token:
            return hmac.compare_digest(provided, token)
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/admin-command":
            if not self._admin_bridge_authorized():
                self._send_json({"ok": False, "status": "forbidden"}, 403)
                return
            try:
                body = self._read_body()
            except Exception:
                self._send_json({"ok": False, "status": "bad_body"}, 400)
                return
            try:
                self._send_json(self.store.enqueue_admin_command(body))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        if not self._require_auth():
            return
        try:
            body = self._read_body()
            if parsed.path == "/api/open-folder":
                folder = body.get("folder")
                if folder not in ("img", "context"):
                    raise PanelError("缺少文件夹类型")
                self._send_json(self.store.open_folder(folder))
                return
            if parsed.path == "/api/file":
                name = (parse_qs(parsed.query).get("name") or [""])[0]
                content = body.get("content")
                if not isinstance(content, str):
                    raise PanelError("缺少 content")
                result = self.store.write(name, content)
                self._send_json({"ok": True, **result})
                return
            if parsed.path == "/api/persona-presets":
                try:
                    result = self.store.write_persona_preset(
                        body.get("name"), body.get("content"))
                except PanelError as exc:
                    self._send_json({"error": str(exc)}, 400)
                    return
                self._send_json({"ok": True, **result})
                return
            if parsed.path == "/api/init-presets":
                try:
                    result = self.store.write_init_preset(
                        body.get("name"), body.get("content"))
                except PanelError as exc:
                    self._send_json({"error": str(exc)}, 400)
                    return
                self._send_json({"ok": True, **result})
                return
            if parsed.path == "/api/load-persona-preset":
                try:
                    result = self.store.activate_persona_preset(body.get("name"))
                except PanelError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
                    return
                self._send_json(result)
                return
            if parsed.path == "/api/apply-init":
                result = self.store.apply_init()
                self._send_json(result)
                return
            if parsed.path == "/api/start":
                result = self.store.start_script(body.get("mode", "all"))
                self._send_json(result)
                return
            if parsed.path == "/api/restart":
                result = self.store.restart_bot()
                self._send_json(result)
                return
            if parsed.path == "/api/stop-bot":
                result = self.store.stop_bot()
                self._send_json(result)
                return
            if parsed.path == "/api/shutdown-panel":
                result = self.store.shutdown_panel()
                self._send_json(result)
                return
            if parsed.path == "/api/shutdown-all":
                result = self.store.shutdown_all()
                self._send_json(result)
                return
            if parsed.path == "/api/reload-model":
                result = self.store.reload_model()
                self._send_json(result)
                return
            if parsed.path == "/api/save-context":
                result = self.store.save_context()
                self._send_json(result)
                return
            if parsed.path == "/api/clear-context":
                result = self.store.clear_context_snapshots()
                self._send_json(result)
                return
            if parsed.path == "/api/wake":
                result = self.store.wake_sleep(body.get("group_id", 0))
                self._send_json(result)
                return
            if parsed.path == "/api/sleep-prep":
                result = self.store.sleep_prep(body.get("group_id", 0))
                self._send_json(result)
                return
            if parsed.path == "/api/force-sleep":
                result = self.store.force_sleep(body.get("group_id", 0))
                self._send_json(result)
                return
            if parsed.path == "/api/panel-settings":
                result = self.store.save_panel_settings(
                    body.get("lan_enabled", False),
                    body.get("password"),
                    body.get("port", 8090),
                )
                self._send_json({"ok": True, **result})
                runtime = panel_state.PANEL_RUNTIME
                if runtime is not None:
                    threading.Thread(
                        target=runtime.rebind_from_settings,
                        name="panel-rebind", daemon=False).start()
                return
            if parsed.path == "/api/image/update":
                name = body.get("name")
                if not isinstance(name, str):
                    raise PanelError("缺少图片名")
                result = self.store.update_image(
                    name,
                    new_name=body.get("new_name"),
                    description=body.get("description"),
                    favorite=body.get("favorite"),
                )
                self._send_json(result)
                return
            if parsed.path == "/api/image/delete":
                name = body.get("name")
                if not isinstance(name, str):
                    raise PanelError("缺少图片名")
                self._send_json(self.store.delete_image(name))
                return
            if parsed.path == "/api/context/delete":
                filename = body.get("file")
                if not isinstance(filename, str):
                    raise PanelError("缺少上下文文件名")
                self._send_json(self.store.delete_context(filename))
                return
        except PanelError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": "服务器错误：%s" % exc}, 500)
            return
        self._send_json({"error": "not found"}, 404)
class PanelHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    allow_reuse_port = False
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        self._request_slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        self._request_slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            self._request_slots.release()
class PanelRuntime(object):
    """控制面板监听器；修改设置时原地换绑，不退出进程。"""

    def __init__(self, store, host, port, password):
        self.store = store
        self.host = host
        self.port = port
        self.password = password
        self.server = None
        self.thread = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _bind(self, host, port):
        last_error = None
        server = None
        for _ in range(20):
            try:
                server = PanelHTTPServer((host, port), PanelHandler)
                break
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        if server is None:
            raise last_error
        thread = threading.Thread(
            target=server.serve_forever, name="panel-http", daemon=True)
        thread.start()
        self.server = server
        self.thread = thread
        self.host = host
        self.port = port
        print("控制面板：http://%s:%d" % (host, port))
        if not panel_state._is_loopback(host):
            lan_ip = panel_state._lan_ip()
            if lan_ip:
                print("局域网访问：http://%s:%d" % (lan_ip, port))
            print("用户名可任意填写，密码为面板设置中的访问密码。")
        sys.stdout.flush()

    def start(self):
        self._bind(self.host, self.port)

    def rebind_from_settings(self):
        data = self.store._panel_settings_data()
        host = "0.0.0.0" if data["lan_enabled"] else "127.0.0.1"
        self.rebind(host, data["port"], data["password"])

    def rebind(self, host, port, password):
        with self._lock:
            old_host, old_port, old_password = self.host, self.port, self.password
            old_server = self.server
            if old_server is not None:
                try:
                    old_server.shutdown()
                    old_server.server_close()
                except Exception:  # noqa: BLE001
                    pass
            panel_state.PANEL_PASSWORD = password or ""
            self.password = password or ""
            try:
                self._bind(host, port)
            except OSError as exc:
                print("[panel] 换绑 %s:%d 失败：%s，尝试恢复旧监听…" % (host, port, exc))
                panel_state.PANEL_PASSWORD = old_password or ""
                self.password = old_password or ""
                try:
                    self._bind(old_host, old_port)
                except OSError as restore_exc:
                    print("[panel] 恢复旧监听也失败：%s" % restore_exc)
                raise
            print("[panel] 新设置已生效")
            sys.stdout.flush()

    def wait(self):
        while not self._stop.wait(0.5):
            pass

    def shutdown(self):
        self._stop.set()
        server = self.server
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
def _open_panel_browser(port):
    if os.environ.get("AIQQ_PANEL_NO_BROWSER"):
        return
    try:
        webbrowser.open("http://127.0.0.1:%d/" % port)
    except Exception:  # noqa: BLE001
        pass
__all__ = [name for name in globals() if not name.startswith("__")]
