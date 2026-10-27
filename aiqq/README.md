# AIQQ 模块结构

`bot.py` 与 `panel.py` 只保留启动入口和参数控制，内部逻辑按职责拆分到本目录。

## 机器人

| 模块 | 职责 |
| --- | --- |
| `config.py` | 默认配置、配置读取、配置校验、日志初始化 |
| `media.py` | CQ 码、图片、引用、转发、语音段解析 |
| `voice.py` | 语音识别/合成、Qwen TTS 服务生命周期、语音标记 |
| `text.py` | 触发匹配、追问、限流、去重、群消息内存 |
| `storage.py` | 群上下文、用户上下文、专业模式上下文 |
| `llm.py` | LM Studio 客户端、模型选择、深度思考参数 |
| `onebot.py` | OneBot HTTP API 客户端 |
| `reply.py` | 回复清理、分段、延迟、消息处理工具 |
| `webhook.py` | Webhook HTTP 服务 |
| `images.py` | 图片库、图片识别标记、发图/存图标记 |
| `bot_engine.py` | Bot 初始化与组合 |
| `bot_reply_job.py` | 单次触发的回复任务调度与上下文写入 |
| `bot_persona.py` | 人设、上下文构建、提示词 |
| `bot_sleep.py` | 睡眠、主动发言、睡眠准备 |
| `bot_idle.py` | 空闲识图、TTS 空闲卸载 |
| `bot_lifecycle.py` | 启动、Webhook、模型重载、重启关闭 |
| `bot_dispatch.py` | 模型队列、命令分发、事件入口、追问 |
| `bot_messages.py` | 群消息处理、图片选择、存图 |
| `bot_generation.py` | 人设、上下文、专业模式、模型生成 |
| `bot_outgoing.py` | 图片/语音/文字/专业模式发送 |
| `bot_status.py` | 连通性、上下文保存、统计状态 |

## 网页面板

| 模块 | 职责 |
| --- | --- |
| `panel_state.py` | 面板共享运行状态与地址辅助函数 |
| `panel_common.py` | 面板公共常量、错误、初始化模块 |
| `panel_store.py` | PanelStore 初始化与组合 |
| `panel_config.py` | 面板设置、文件读写、备份 |
| `panel_presets.py` | 人设/初始化预设、应用配置 |
| `panel_process.py` | NapCat/机器人/面板进程控制 |
| `panel_bridge.py` | 上下文、图库、睡眠桥接 |
| `panel_commands.py` | QQ 控制面板指令、截图 |
| `panel_napcat.py` | NapCat 登录、二维码、状态 |
| `panel_contexts.py` | 上下文浏览、配置摘要、监控状态 |
| `panel_gallery.py` | 图库、上下文、监视状态 |
| `panel_server.py` | HTTP 处理、面板监听、运行时换绑 |
| `panel.py` | 面板启动入口 |

## 启动方式

```text
python bot.py
python panel.py
```

两者仍可直接用原命令启动，配置文件和外部路径保持不变。
