# AIQQ 架构说明

## 目标

`bot.py` 和 `panel.py` 是两个稳定的启动入口，负责参数解析、进程入口和对外兼容。
业务逻辑放在 `aiqq/` 包中，按领域拆分，模块之间使用显式导入。

## 分层

```text
bot.py / panel.py                 启动入口，只做参数和组装
        │
        ▼
bot_engine.py / panel_store.py    组合根（只保留 __init__ 和 MRO）
        │
        ▼
bot_* / panel_* mixin             领域流程
        │
        ▼
config / media / voice / ...      基础能力
```

依赖方向只能从入口指向组合根，再指向领域和基础能力，不允许反向依赖。

## 机器人模块

| 模块 | 内聚职责 |
| --- | --- |
| `config.py` | 默认配置、配置校验、日志 |
| `media.py` | CQ 码、图片、引用、转发、语音段 |
| `voice.py` | 语音识别、TTS 生命周期、语音标记 |
| `text.py` | 触发匹配、限流、去重、群消息缓存 |
| `storage.py` | 群上下文、用户上下文、专业模式上下文 |
| `llm.py` | LM Studio 客户端和模型选择 |
| `onebot.py` | OneBot API |
| `reply.py` | 回复清理、分段、延迟 |
| `webhook.py` | Webhook HTTP 服务 |
| `images.py` | 图片库和图片标记 |
| `bot_reply_job.py` | 单次触发任务的调度与上下文写入 |
| `bot_persona.py` | 人设、上下文构建、提示词 |
| `bot_generation.py` | 模型生成和专业模式 |
| `bot_messages.py` | 群消息和图片选择 |
| `bot_outgoing.py` | 图片、语音、文字输出 |
| `bot_sleep.py` | 睡眠和主动发言 |
| `bot_idle.py` | 空闲识图和 TTS 卸载 |
| `bot_dispatch.py` | 模型队列、命令分发、事件入口 |
| `bot_lifecycle.py` | 启动、重启、关闭、重载 |
| `bot_connectivity.py` | 连通性检测 |
| `bot_status.py` | 上下文保存、统计和快照 |
| `bot_engine.py` | 只负责 Bot 初始化和 mixin 组合 |

## 面板模块

| 模块 | 内聚职责 |
| --- | --- |
| `panel_state.py` | 面板共享状态、地址检查 |
| `panel_common.py` | 公共常量、异常、setup 引用 |
| `panel_config.py` | 面板设置、文件读写、备份 |
| `panel_presets.py` | 人设/初始化预设 |
| `panel_process.py` | NapCat、机器人、面板进程控制 |
| `panel_bridge.py` | 上下文、图库、睡眠桥接 |
| `panel_napcat.py` | NapCat 登录和二维码 |
| `panel_commands.py` | QQ 面板命令、帮助、截图 |
| `panel_contexts.py` | 上下文浏览和监控状态 |
| `panel_gallery.py` | 图库浏览、更新、删除 |
| `panel_server.py` | HTTP 处理和面板运行时 |
| `panel_store.py` | 只负责 PanelStore 初始化和 mixin 组合 |

## 耦合规则

1. 包内模块使用显式导入，不使用 `from .module import *`。
2. `bot.py` / `panel.py` 可以使用通配导入作为兼容门面，内部模块不得依赖门面。
3. 组合根只负责继承和初始化，不新增业务方法。
4. mixin 可以调用宿主的其他领域方法，但不得直接实例化其他 mixin。
5. 基础模块不导入 `bot_engine` 或 `panel_store`。
6. 循环依赖通过局部导入解决，例如语音分段函数只在调用时导入。
7. 所有跨模块 I/O 通过已有的 `LMStudioClient`、`OneBotClient`、`PanelStore` 边界完成。

## 主流程

```text
ONEBOAT WEBHOOK
  → bot_dispatch.handle_event
  → bot_messages.handle_group_message
  → bot_dispatch._enqueue_model_request
  → bot_reply_job._reply_job
  → bot_persona._system_prompt / bot_generation._generate_reply
  → bot_outgoing._send_reply
  → storage.UserContextStore
```

## 面板流程

```text
浏览器
  → panel_server.PanelHandler
  → PanelStore (mixin 组合)
  → panel_bridge / panel_gallery / panel_commands
  → 文件系统或 bot Webhook
```

## 错误边界

- `LMStudioError`：模型连接、协议和生成错误。
- `OneBotError`：QQ/NapCat API 错误。
- `VoiceError`：语音识别和合成错误。
- `PanelError`：面板操作错误。
- 业务 mixin 捕获领域错误并记录日志，入口层负责进程退出和重启。

## 扩展方式

- 新增 QQ 消息行为：放在 `bot_messages` 或独立 `bot_*` mixin，并在 `bot_engine.Bot` 的 MRO 中注册。
- 新增面板页面/操作：放在对应 `panel_*` mixin，不修改 `panel_server` 的通用 HTTP 分发。
- 新增模型能力：只改 `llm.py`，不要在其他模块复制 API 请求逻辑。
- 新增存储：只改 `storage.py`，业务层只使用存储接口。

## 测试

```text
python tests\test_matcher.py
python tests\test_panel.py
```

测试同时校验入口导出和模块导入兼容性。
