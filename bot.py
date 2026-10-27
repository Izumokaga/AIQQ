#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIQQ 启动入口：参数解析与控制流程，具体逻辑位于 aiqq 包。"""

import argparse
import os
import sys

from aiqq.config import *
from aiqq.media import *
from aiqq.voice import *
from aiqq.text import *
from aiqq.storage import *
from aiqq.llm import *
from aiqq.onebot import *
from aiqq.reply import *
from aiqq.webhook import *
from aiqq.images import *
from aiqq.group_context import *
from aiqq.bot_engine import Bot


def _ensure_std_streams():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))




# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def print_banner(bot):
    cfg = bot.cfg
    log.info("=" * 58)
    log.info(" AIQQ · LM Studio × QQ 群聊助手")
    log.info("-" * 58)
    log.info(" 机器人 QQ  : %s（%s）", bot.self_id or "未知", bot.self_nick)
    log.info(" 监听群聊   : %s", ", ".join(str(g) for g in sorted(bot.groups)))
    log.info(" OneBot API : %s", cfg["onebot"]["api_url"])
    log.info(" Webhook    : http://%s:%d%s", cfg["onebot"]["webhook_host"],
             cfg["onebot"]["webhook_port"], cfg["onebot"]["webhook_path"])
    log.info(" LM Studio  : %s", cfg["lm_studio"]["base_url"])
    log.info(" 模型       : %s", bot.lm.model or "（自动）")
    keywords = "、".join(cfg["trigger"]["keywords"]) or "（无）"
    log.info(" 触发关键词 : %s%s", keywords, " + @机器人" if cfg["trigger"]["match_mention"] else "")
    log.info(" 健康检查   : http://%s:%d/healthz", cfg["onebot"]["webhook_host"],
             cfg["onebot"]["webhook_port"])
    log.info("=" * 58)


def run_check(cfg):
    bot = Bot(cfg)
    onebot_ok, lm_ok = bot.probe()
    log.info("-" * 58)
    log.info("检查结果：OneBot %s | LM Studio %s",
             "正常" if onebot_ok else "异常", "正常" if lm_ok else "异常")
    if not onebot_ok:
        log.info("提示：先启动 NapCat，并在 WebUI 中开启 OneBot HTTP 服务（端口与 config.json 一致）。")
    if not lm_ok:
        log.info("提示：在 LM Studio 中加载模型，然后 Developer → Start Server（默认端口 1234）。")
    return 0 if (onebot_ok and lm_ok) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="把 LM Studio 接入 QQ 群聊的自动应答机器人")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="配置文件路径（默认 config.json）")
    parser.add_argument("--check", action="store_true", help="只检查 LM Studio 与 NapCat 连通性后退出")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print("配置错误：%s" % exc, file=sys.stderr)
        return 2

    setup_logging(cfg, config_path=args.config)

    if args.check:
        return run_check(cfg)

    try:
        bot = Bot(cfg)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        bot.start()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    bot.probe()
    print_banner(bot)
    log.info("已就绪，等待群消息…（Ctrl+C 退出）")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        log.info("收到退出信号…")
    finally:
        bot.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
