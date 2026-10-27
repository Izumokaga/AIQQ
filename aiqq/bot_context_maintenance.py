# -*- coding: utf-8 -*-
"""AIQQ 群上下文整理与人设固化。

上下文超过 5000 字后，在全局维护锁内调用一个不加载上下文和人设的模型：
只输入群上下文和当前群级人设，输出覆盖群级 <预设名>.persona.txt，
成功后删除该群唯一的 context.json。
"""

import os
import threading
import time

from .config import _as_int, log


class BotContextMaintenanceMixin:
    def _context_group_blocked(self, group_id):
        with self._context_maintenance_lock:
            return int(group_id or 0) in self._context_disabled_groups

    def _context_notify_busy(self, group_id):
        """维护期间收到触发时，每组只回复一次“等会再说，在忙”。"""
        group_id = int(group_id or 0)
        with self._context_maintenance_lock:
            if group_id in self._context_busy_groups:
                return
            self._context_busy_groups.add(group_id)
        try:
            self._send_plain_text(group_id, "等会再说，在忙")
        except Exception as exc:  # noqa: BLE001
            log.warning("[上下文] 发送忙碌提示失败：群 %s %s", group_id, exc)

    def _context_notify_done(self, failed_group=0):
        with self._context_maintenance_lock:
            groups = list(set(self._context_busy_groups) | set(self._context_manual_groups))
            self._context_busy_groups.clear()
            self._context_manual_groups.clear()
        for group_id in groups:
            if failed_group and int(group_id) == int(failed_group):
                continue
            try:
                self._send_plain_text(group_id, "忙完了")
            except Exception as exc:  # noqa: BLE001
                log.warning("[上下文] 发送完成提示失败：群 %s %s", group_id, exc)

    def _start_context_consolidation(self, group_id):
        group_id = int(group_id or 0)
        if group_id <= 0 or self._context_group_blocked(group_id):
            return False
        with self._context_maintenance_lock:
            if self._context_maintenance.is_set():
                self._context_pending_groups.add(group_id)
                log.info("[上下文] 已有整理任务，群 %s 等待下一轮", group_id)
                return False
            self._context_maintenance.set()
            self._context_maintenance_group = group_id
            self._context_pending_groups.discard(group_id)
        thread = threading.Thread(
            target=self._run_context_consolidation, args=(group_id,),
            name="aiqq-context-consolidate", daemon=True)
        thread.start()
        return True

    def request_manual_context_consolidation(self, group_id):
        group_id = int(group_id or 0)
        if group_id <= 0:
            return {"ok": False, "error": "缺少群号"}
        if self._context_group_blocked(group_id):
            return {"ok": False, "error": "该群已停止上下文整理"}
        if not self.group_context.messages(group_id):
            return {"ok": False, "error": "该群当前没有可整理的上下文"}
        with self._context_maintenance_lock:
            self._context_manual_groups.add(group_id)
        if self._start_context_consolidation(group_id):
            return {"ok": True, "message": "已开始整理群 %s 的上下文" % group_id}
        with self._context_maintenance_lock:
            if group_id in self._context_pending_groups or group_id == self._context_maintenance_group:
                return {"ok": True, "message": "群 %s 已加入整理队列" % group_id}
            self._context_manual_groups.discard(group_id)
        return {"ok": False, "error": "无法启动整理"}

    def _run_context_consolidation(self, group_id):
        group_id = int(group_id)
        log.warning("[上下文] 开始整理群 %s，暂停所有群请求", group_id)
        self._context_pause_queues()
        self._cancel_idle_image_tasks()
        failed = False
        try:
            context_text = self.group_context.prompt_text(group_id)
            preset = self.group_persona.active_preset() or "persona"
            persona_text = self.group_persona.read(group_id, preset)
            if not persona_text:
                persona_text = str(self.cfg["lm_studio"].get("system_prompt") or "")
            gallery_text = self.images.name_catalog_text() or "空"
            prompt = (
                "根据上下文内容和图库内容对人设内容进行修改，并在人设最后附上对每个活跃群成员"
                "以个人为单位的用户特征总结，限制字数小于2000字，"
                "思考过程尽量缩短，直接输出修改后内容：\n"
                "上下文内容：\n%s\n"
                "图库内容：\n%s\n"
                "人设内容：\n%s\n" % (context_text, gallery_text, persona_text)
            )
            messages = [{"role": "user", "content": prompt}]
            reply = ""
            last_error = None
            for attempt in range(1, 6):
                try:
                    with self._model_lock:
                        reply = self.lm.chat(
                            messages, reasoning_effort="high", max_tokens=32768)
                    reply = str(reply or "").strip()
                    if reply:
                        break
                    last_error = "模型返回空内容"
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    log.warning("[上下文] 群 %s 第 %d 次整理失败：%s",
                                group_id, attempt, exc)
                    time.sleep(min(5, attempt))
            if not reply:
                raise RuntimeError(last_error or "模型连续返回空内容")
            # 人设内容完整保存，不做长度截断；2000 字仅是提示词约束。
            self.group_persona.write(group_id, reply, preset)
            self.group_context.clear(group_id)
            log.info("[上下文] 群 %s 整理完成，已写入 %s.persona.txt 并删除上下文",
                     group_id, preset)
        except Exception as exc:  # noqa: BLE001
            failed = True
            with self._context_maintenance_lock:
                self._context_disabled_groups.add(group_id)
            log.error("[上下文] 群 %s 连续整理失败，永久停止该群触发：%s", group_id, exc)
            self._context_send_failure(group_id)
        finally:
            if not failed:
                self._context_notify_done()
            else:
                self._context_notify_done(failed_group=group_id)
            with self._context_maintenance_lock:
                self._context_maintenance.clear()
                self._context_maintenance_group = 0
                pending = list(self._context_pending_groups)
                self._context_pending_groups.clear()
            if pending:
                self._start_context_consolidation(pending[0])

    def _context_pause_queues(self):
        """维护期间丢弃模型队列中尚未开始的请求，运行中的请求完成后丢弃。"""
        with self._pending_lock:
            self._pending_followups.clear()
        with self._autonomous_lock:
            self._spontaneous_waiting = False
            self._spontaneous_at = 0.0
            self._spontaneous_groups = set()
        with self._model_queue_lock:
            entries = [item[2] for item in self._model_queue if item[2]]
            for entry in entries:
                if isinstance(entry, dict):
                    entry["blocked_by_context_save"] = True
            self._model_queue.clear()
            active = self._model_active_entry
            if isinstance(active, dict):
                active["blocked_by_context_save"] = True
            self._model_queue_lock.notify_all()

    def _context_send_failure(self, group_id):
        admins = self.cfg.get("qq", {}).get("admin_users") or []
        admin = _as_int(admins[0], 0) if admins else 0
        segments = []
        if admin:
            segments.append({"type": "at", "data": {"qq": str(admin)}})
        segments.append({"type": "text", "data": {"text": " 我不行了，要死了，救救我"}})
        try:
            self.onebot.send_group_msg(int(group_id), segments)
        except Exception as exc:  # noqa: BLE001
            log.warning("[上下文] 发送失败求助消息失败：群 %s %s", group_id, exc)


__all__ = ["BotContextMaintenanceMixin"]
