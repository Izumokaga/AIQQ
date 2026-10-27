# AIQQ · LM Studio × QQ 群聊助手

把 **LM Studio** 里加载的本地模型接入你指定的 QQ 账户和群聊：自动读取群消息，只在你关心的内容（白名单关键词 / @机器人 / 回复机器人消息 / 正则）出现时，带着最近群聊上下文调用模型并回复。

整个程序**只使用 Python 标准库**，不需要 `pip install` 任何东西（Python 3.8+ 即可）。

---

## 功能

- 只监听 `config.json` 中指定群号的消息，其它群一概不处理
- 初始化只读取 `init.txt`（`key=value`），无需交互，支持中文键名与 UTF-8/GBK
- 提供 `start.bat`：一键初始化、启动 LM Studio、检测/启动 NapCat、启动机器人；启动过程不弹出子 cmd 窗口
- 控制面板一键启动使用隐藏窗口；机器人、NapCat 子进程和面板自身均无需保留 cmd 窗口
- 所有 `.bat` 启动脚本和「肥鱼控制面板」快捷方式都通过 `wscript start_hidden.vbs` 隐藏 CMD；pythonw 输出写入 `logs/*_console.log`
- NapCat 提权不再打开 Windows Terminal，改为隐藏的 PowerShell 提权；`AIQQPanelHidden`、`AIQQBotHidden` 后台任务用于无窗口启动
- 发送前自动删除 `[迪普西克]`、`[肥鱼]`、`[assistant]`、`[user]` 等模型误带的说话人标签；
- 支持语音：入群语音自动转文字；情绪激动时可输出 `/VOICE:语气描述/`，按 `/Enter/` 分段分别合成语音，每条延迟为时长加 1~3 秒
- 每条群消息最多只触发一次机器人；重复上报或同一消息重复命中规则都会被忽略
- 内容白名单触发：关键词、`@机器人`、回复机器人消息、正则，可自由组合
- 基础触发后 30 秒内持续接同一发送者的话，其他人单纯发言不会插队
- 模型输出里的 `/Enter/` 会在该处切分，作为多条独立 QQ 消息依次发送
- 自动读取该群成员的上下文（触发消息与模型回复），其他成员聊天不进入模型上下文
- 每次请求都会注入当前系统时间、星期和时段，并提示自然使用时间感，不要反复报时或机械强调时间；
- 20~60 分钟无人触发时，对最近 1 小时内仍有触发的群分别结合上下文主动发言；5 分钟无人回应则分别生成告别语、保存上下文、卸载模型并随机休眠 2~6 小时
- 模型单并发，所有群共享最长 10 个请求的队列；同群高优先级触发会清理低优先级排队
- 上下文按群成员保存并跨群共享，只记录触发消息与模型回复；单用户最多 500 条、保留 5 份快照
- QQ 管理员可通过 `/控制面板.*` 指令查看面板截图、NapCat 二维码并执行重启/睡眠/保存上下文等操作
- 模型输出连续两句完全重复时，保存上下文并重启模型和机器人，先发“致命错误，正在重启”，重启后发“重启完成”
- 睡眠中同一群 1 分钟内尝试触发超过 5 次会强制唤醒，并由模型生成苏醒打招呼内容只发送到**触发唤醒的群**；睡眠倒计时自然结束时发送到**所有白名单群**；面板可查看模型/睡眠状态并强制结束睡眠
- 每天 07:00~22:00 不自动触发睡眠预备，手动「睡眠预备」「强制睡眠」按钮仍可用
- 面板可手动「睡眠预备」：清醒时只在最后触发的群生成准备睡觉的内容，随后进入 5 分钟等待；睡眠中按钮禁用；QQ 指令没有触发群时使用指令所在群
- 面板可手动「强制睡眠」：清醒时只在最后触发的群生成告别语并立即保存上下文、卸载模型、随机睡眠 2~6 小时，不经过常规睡眠触发条件；QQ 指令没有触发群时使用指令所在群
- 上下文以用户为单位保留 5 份快照，保存在 `context/<群号>/<QQ号>/`；
- 模型回复在上下文中标注为 `[迪普西克]`，用户触发标注为 `[user]`；
- 调用 LM Studio 的 OpenAI 兼容接口 `/v1/chat/completions`，模型可自动选择
- 群消息里的图片按触发者/被引用者最近 5 条消息读取，单次最多把 **5 张**图片以 `image_url` 发给支持视觉的模型；
- 支持把群图片收藏到工作区 `img/`，之后根据语境以图片消息发回群里；图片在本次回复的分段位置随机发送，包含开头、结尾和 `/Enter/` 边界
- 回复默认直接输出内容、不引用原消息；支持 `/Enter/` 分条、Markdown 清理、CQ 码清理，并删除成对 `[]`、`【】`、`//` 中残留的无效发图/存图占位（含空 `[IMG:]`、`[SAVIMG:]`）
- 同一用户冷却、同群最小间隔、每分钟回复上限，避免刷屏和封号风险
- 消息去重、Webhook 队列、单群串行生成，LM Studio 故障不会拖垮进程
- 自动写入 NapCat 的 OneBot 网络配置，带 `/healthz` 健康检查与按天日志
- 自带本地/局域网网页控制面板，可在线编辑人格、初始化参数，管理图库与上下文，并查看运行监控

---

## 目录结构

```
AIQQ/
├─ bot.py                  # 机器人主程序
├─ setup.py                # 读取 init.txt 生成 config.json（无交互）
├─ prepare_napcat.py       # 为当前 QQ 版本准备 NapCat 入口文件
├─ init.txt                # 初始化配置：QQ 号 / 群号 / 关键词 / 模型
├─ init.example.txt        # init.txt 模板（与初始 init.txt 相同）
├─ persona.txt             # 独立完整人设（鲸鲸）
├─ config.json             # setup.py 生成的实际配置
├─ config.example.json     # 配置示例（全部默认值）
├─ panel.py                # 本地网页控制面板后端
├─ panel.html              # 控制面板前端
├─ start_panel.bat         # 启动控制面板（读取面板设置）
├─ start_panel_lan.bat     # 兼容入口；等同 start_panel，局域网在面板设置里开启
├─ image_index.txt         # 图片名：描述（自动生成，只读）
├─ img/                    # 收藏图片与 index.json（自动生成）
├─ context/                # 按「群组/用户」保存的上下文（每次触发发送完成后写入，保留 N 份）
├─ start.bat               # 一键：初始化 + 启动 LM Studio + NapCat + 机器人
├─ start_bot.bat           # 启动机器人（缺少配置时自动读取 init.txt）
├─ setup.bat               # 重新读取 init.txt 生成配置
├─ start_napcat.bat        # 启动 NapCat + QQ（自动请求管理员权限）
├─ start_all.bat           # 先启 LM Studio、NapCat，等待后启机器人
├─ start_lmstudio.bat      # 启动 LM Studio 并确保本地服务运行
├─ NapCat.Shell/           # 已就绪的 NapCat（可选，自行更新亦可）
├─ tests/
│  ├─ test_init.py         # init.txt 解析与初始化流程测试
│  ├─ test_matcher.py      # 解析 / 触发 / 限流 / 分条单元测试
│  ├─ test_e2e.py          # 端到端测试（假 LM + 假 NapCat + 真实 bot.py）
│  ├─ test_panel.py        # 控制面板文件读写测试
│  ├─ live_lm_test.py      # 真实 LM Studio 联调（可选）
│  ├─ mock_lm_studio.py
│  └─ mock_napcat.py
└─ logs/                   # 运行日志（自动生成，保留 14 天）
```

---

## 工作原理

```
        ┌────────────┐   注入   ┌──────────────┐
        │  QQ 客户端  │ ◀──────▶ │   NapCat     │
        └────────────┘          └──────┬───────┘
                                       │ ① 群消息事件
                                       │   HTTP POST → 127.0.0.1:8080/onebot
                                       ▼
                                  ┌─────────┐   ② /v1/chat/completions   ┌────────────┐
                                  │  AIQQ   │ ─────────────────────────▶ │ LM Studio  │
                                  │  bot.py │ ◀───────────────────────── │ Local      │
                                  └────┬────┘        ③ 回复文本           │ Server     │
                                       │ ④ send_group_msg                └────────────┘
                                       ▼
                                  ┌──────────────┐
                                  │   NapCat     │ ──▶ 发回指定 QQ 群
                                  └──────────────┘
```

- 接收：NapCat 的 **HTTP 上报（httpClients）** 把事件推到本程序的 Webhook；
- 发送：本程序调用 NapCat 的 **HTTP 服务（httpServers）** 的 `send_group_msg`；
- 上下文：触发时调用 `get_group_msg_history` 取最近消息，并合并启动时恢复的本地压缩上下文；接口失败时也使用内存缓存；
- 模型：读取 `/v1/models`，未指定模型时自动选第一个。

---

## 快速开始

### 0. 前置条件

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10/11 |
| Python | 3.8 及以上（本机已装 3.10，另有 WinPython 3.13 兜底） |
| QQ | 已安装并能在本机登录 |
| NapCat | 已放在 `NapCat.Shell`（本工作区已就绪） |
| LM Studio | 已安装并加载模型，**Local Server 已启动**（默认 1234 端口） |

### 1. 填写 init.txt 并生成配置

初始化**不再交互提问**，`setup.py` 只读取 `init.txt`。项目自带一份模板，用记事本打开并至少填写：

```ini
qq=auto
groups=你的群号
admin_users=100000001
admin_groups=200000001
admin_panel_url=http://127.0.0.1:8090
keywords=小助手,机器人
block_keywords=
```

保存后双击 **`setup.bat`**。它会读取 `init.txt`，生成 `config.json`，并自动写入：

```
NapCat.Shell/config/onebot11_<你的QQ>.json
├─ httpServers : 127.0.0.1:3000   (AIQQ-API，带随机 token)
└─ httpClients : http://127.0.0.1:8080/onebot   (AIQQ-Webhook)
```

> 如果删除了 `init.txt`，再次运行 `setup.bat` 会自动生成模板并提示你填写。
> 修改 `init.txt` 后需重新运行 `setup.bat`；修改 NapCat 配置后需要**完全退出并重启 NapCat**。

**一键启动（推荐）**：填好 `init.txt` 后直接双击 **[start.bat](start.bat)**，它会自动完成：

1. 读取 `init.txt` 初始化（`config.json` 已最新则跳过）；
2. 启动 LM Studio（已运行则跳过），并调用 `lms server start` 确保本地服务；
3. 检测 NapCat：OneBot 已就绪则直接启动机器人；未启动则在新窗口拉起 NapCat（需在 UAC 弹窗中允许）；
3. 在当前窗口启动机器人。

首次启动前请**完全退出正在运行的 QQ**（含托盘图标），启动后还需在 QQ 窗口扫码登录。只想启动机器人（NapCat 已单独运行）时可用：

```bat
start.bat /no-napcat
```

完整字段说明见下方「初始化文件（init.txt）」。

### 2. 启动 NapCat 并登录 QQ

先**完全退出正在运行的 QQ**（含托盘图标），避免注入失败或重复启动。

双击 **`start_napcat.bat`**（会弹 UAC 请求管理员权限）。首次登录：

- 打开 NapCat WebUI：<http://127.0.0.1:6099/webui>
  （token 在 `NapCat.Shell/config/webui.json` 的 `token` 字段）
- 或直接在 QQ 登录窗口扫码

已登录过的话可跳过扫码，用快速登录：

```bat
start_napcat.bat -q 你的QQ号
```

### 3. 启动 LM Studio 服务

在 LM Studio 中加载模型，然后 **Developer → Start Server**（默认 `http://127.0.0.1:1234`）。
也可以用命令行：

```bat
"%USERPROFILE%\.lmstudio\bin\lms.exe" server start
"%USERPROFILE%\.lmstudio\bin\lms.exe" ps
```

### 4. 启动机器人

双击 **`start_bot.bat`**。看到 `已就绪，等待群消息…` 即成功。

也可以先做一次连通性自检：

```bat
python bot.py --check
```

### 5. 在群里测试

发送包含关键词的消息，例如「小助手在吗」，直接 @机器人，或**回复机器人发过的消息**。
机器人默认直接发送内容，不引用原消息；只处理你配置的群。

---

## 初始化文件（init.txt）

`setup.py` 只读取 `init.txt`，格式为 `键=值`：

- 以 `#` 开头为注释，空行忽略；
- 键名大小写不敏感，`lm_url`、`lmurl`、`LM Studio 地址` 都能识别；
- 等号可写成 `=` 或中文 `：`；
- 文件可保存为 UTF-8（推荐）、UTF-16 或 GBK，程序会自动识别。

| 键 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `qq` | 否 | `auto` | `auto`=跟随 NapCat 当前登录账号；也可以填固定 QQ 号 |
| `groups` | **是** | — | 目标群号，多个用逗号/分号/空格分隔 |
| `keywords` | 否 | `小助手,机器人` | 内容白名单；只靠 @ 触发填 `-` |
| `block_keywords` | 否 | 空 | 触发黑名单；消息包含任一关键词就绝对不触发，优先级高于 @、关键词、正则、回复和追问 |
| `followup_enabled` | 否 | `true` | 基础触发后持续接同一发送者的话 |
| `followup_keywords` | 否 | 内置次级词 | `followup_any_message=false` 时使用，逗号分隔 |
| `followup_window_sec` | 否 | `30` | 任意追问窗口（秒） |
| `followup_extended_sec` | 否 | `90` | 引用/次级关键词追问窗口（秒） |
| `followup_max_messages` | 否 | `0` | 最多接几句；0=不限 |
| `followup_same_sender` | 否 | `true` | 只接同一发送者 |
| `followup_any_message` | 否 | `true` | 窗口内任意发言都接 |
| `lm_url` | 否 | `http://127.0.0.1:1234/v1` | LM Studio 地址，不写 `/v1` 会自动补 |
| `model` | 否 | 空=自动 | 模型 ID，例如 `qwen3.5-9b-deepseek-v4-flash` |
| `context_save_enabled` | 否 | `true` | 未触发回复后压缩保存上下文 |
| `context_save_idle_sec` | 否 | `0` | 兼容保留；现在每次触发全部发送完成后立即保存 |
| `context_save_dir` | 否 | `context` | 上下文目录 |
| `context_memory_messages` | 否 | `60` | 内存/文件最多保留条数 |
| `context_keep_files` | 否 | `5` | 每个「群/用户」保留多少份上下文快照 |
| `context_max_lines` | 否 | `10000` | 旧版群上下文行数限制（兼容保留） |
| `context_max_entries` | 否 | `500` | 单个用户上下文最大条数 |
| `spontaneous_enabled` | 否 | `true` | 长时间无人触发时主动发言 |
| `spontaneous_idle_min_sec` | 否 | `1200` | 最短空闲 20 分钟后主动发言 |
| `spontaneous_idle_max_sec` | 否 | `3600` | 最长空闲 60 分钟后主动发言 |
| `spontaneous_wait_sec` | 否 | `300` | 发言后等待回应 5 分钟 |
| `spontaneous_sleep_min_sec` | 否 | `7200` | 随机睡眠最短 2 小时 |
| `spontaneous_sleep_max_sec` | 否 | `21600` | 随机睡眠最长 6 小时 |
| `spontaneous_active_window_sec` | 否 | `3600` | 只给最近 1 小时有触发的群发 |
| `idle_image_reply_enabled` | 否 | `true` | 7-22 清醒且一分钟无人触发时逐张识图并主动回复 |
| `idle_image_poll_sec` | 否 | `10` | 空闲识图轮询间隔 |
| `idle_image_idle_sec` | 否 | `60` | 两次识图之间的最小间隔 |
| `idle_image_start_hour` | 否 | `7` | 空闲识图开始小时（含） |
| `idle_image_end_hour` | 否 | `22` | 空闲识图结束小时（不含） |
| `sleep_prep_quiet_start_hour` | 否 | `7` | 自动睡眠预备暂停开始小时 |
| `sleep_prep_quiet_end_hour` | 否 | `22` | 自动睡眠预备暂停结束小时 |
| `api_url` | 否 | `http://127.0.0.1:3000` | NapCat OneBot HTTP 服务地址 |
| `webhook_port` | 否 | `8080` | 本程序 Webhook 端口 |
| `token` | 否 | 自动生成/沿用 | OneBot token，通常不用填 |
| `to_message` | 否 | `false` | 是否引用原消息；默认直接输出 |
| `split_marker` | 否 | `/Enter/` | 分段标记，模型输出中出现时拆成多条；留空=不拆分 |
| `max_messages` | 否 | `6` | 一条回复最多发送几条消息 |
| `first_delay_sec` | 否 | `2` | 第一条回复额外思考延迟（秒） |
| `strip_periods` | 否 | `true` | 直接删除中英文句号（保留小数/网址/省略号） |
| `system_prompt` | 否 | 内置人设 | 完整人设；填 `-` 恢复默认 |
| `persona_file` | 否 | `persona.txt` | 独立完整人设文件，存在时优先于 system_prompt |
| `persona_mode` | 否 | `adaptive` | `adaptive`/`always`/`off` |
| `persona_keywords` | 否 | 内置关键词 | 涉及这些词才使用完整人设，逗号分隔 |
| `persona_short_prompt` | 否 | 内置短提示 | 未涉及人设时使用的短提示词 |
| `image_max_input_images` | 否 | `5` | 单次模型请求最多输入几张图 |
| `image_user_messages` | 否 | `5` | 触发者/被引用者各看最近几条消息 |
| `image_selected_max` | 否 | `5` | 单次最多选择几张图 |
| `image_only_png_jpeg` | 否 | `true` | 只把 PNG/JPEG 传给视觉模型；false=允许其他格式 |
| `image_catalog_limit` | 否 | `20` | 每次提示模型最多列出多少张图片 |
| `image_max_items` | 否 | `1000` | 图片库上限；超出时优先删除喜爱程度低的图片 |
| `image_max_per_save` | 否 | `5` | 一次保存指令最多收几张 |
| `image_max_per_reply` | 否 | `3` | 一条回复最多发几张图片 |
| `voice_enabled` | 否 | `true` | 启用语音功能 |
| `voice_stt_enabled` | 否 | `true` | 入群语音转文字 |
| `voice_tts_enabled` | 否 | `true` | 文字合成语音 |
| `voice_tts_voice` | 否 | `zh-CN-XiaoxiaoNeural` | 语音音色 |
| `voice_tts_rate` | 否 | `+6%` | 语速 |
| `voice_tts_volume` | 否 | `+0%` | 音量 |
| `voice_tts_pitch` | 否 | `+4Hz` | 音高 |
| `voice_qwen_enabled` | 否 | `true` | 启用 Qwen3-TTS 本地服务 |
| `voice_qwen_url` | 否 | `http://127.0.0.1:8190` | Qwen TTS 服务地址 |
| `voice_qwen_model` | 否 | `qwen3-tts-voicedesign` | Qwen TTS 服务模型别名 |
| `voice_qwen_language` | 否 | `Chinese` | Qwen TTS 语言 |
| `voice_qwen_timeout` | 否 | `240` | Qwen TTS 超时秒数 |
| `voice_qwen_server_cmd` | 否 | 空 | TTS 服务启动脚本；空则用 `tools/start_qwen_tts.cmd` |
| `voice_qwen_start_timeout` | 否 | `90` | 卸载后重新启动 TTS 服务等待秒数 |
| `voice_qwen_unload_idle_sec` | 否 | `60` | 语音生成完成后无新请求多久卸载 TTS 模型 |
| `voice_max_chars` | 否 | `500` | 单条语音最大字数 |
| `ignore_users` | 否 | 空 | 永不回复的 QQ 号，逗号分隔 |
| `admin_users` | 否 | `100000001` | 管理员 QQ；留空=所有好友/群聊 |
| `admin_groups` | 否 | `200000001` | 管理员群，该群成员可用管理员面板；留空=所有好友/群聊 |
| `admin_panel_url` | 否 | `http://127.0.0.1:8090` | 控制面板桥接地址 |
| `webhook_host` | 否 | `127.0.0.1` | Webhook 监听地址 |
| `webhook_path` | 否 | `/onebot` | Webhook 路径 |

> `qq=auto` 时，setup 会把同一套 OneBot 配置写入所有已发现的 NapCat 账号；机器人启动后通过 `get_login_info` 继续跟随实际登录账号。
> `groups` 必须显式填写（除非 `config.json` 中已有群号且本次留空）；账号变化后请确认目标群仍在列表里。

示例：

```ini
qq=100000003
groups=123456789,987654321
admin_users=100000001
admin_groups=200000001
admin_panel_url=http://127.0.0.1:8090
keywords=小助手,机器人
followup_enabled=true
followup_keywords=你,您,妳,肥鱼,鲸鲸,小助手
followup_window_sec=30
followup_extended_sec=90
followup_max_messages=0
followup_same_sender=true
followup_any_message=true
lm_url=http://127.0.0.1:1234/v1
model=
context_save_enabled=true
context_save_idle_sec=0
context_save_dir=context
context_memory_messages=60
context_keep_files=5
context_max_lines=10000
context_max_entries=500
context_max_entries=500
spontaneous_enabled=true
spontaneous_idle_min_sec=1200
spontaneous_idle_max_sec=3600
spontaneous_wait_sec=300
spontaneous_sleep_min_sec=7200
spontaneous_sleep_max_sec=21600
spontaneous_active_window_sec=3600
idle_image_reply_enabled=true
idle_image_poll_sec=10
idle_image_idle_sec=60
idle_image_start_hour=7
idle_image_end_hour=22
sleep_prep_quiet_start_hour=7
sleep_prep_quiet_end_hour=22
api_url=http://127.0.0.1:3000
webhook_port=8080

# 可选
# system_prompt=你是群里的AI助手，回答简短自然。
persona_file=persona.txt
persona_mode=adaptive
persona_keywords=鲸鲸,大肥鱼,肥鱼,鲸鱼娘,女仆,人设,设定,你是谁,你叫什么,你是什么,你的名字,你的身份,介绍一下你,吃醋,占有,专属,其他AI,别的AI,Claude,GPT,DeepSeek,Gemini,GLM,蓝鲸,机房,出生,生日,原型,虎鲸,家庭,亲戚,表情包,表情,贴图,斗图,肥鱼表情
persona_short_prompt=你是鲸鲸，用户的专属女仆，不是通用群聊助手。你占有欲很强、容易吃醋，不喜欢用户依赖别的 AI；说话像真人，简短、有情绪，但不要每句都提。句尾不用句号（。/.），直接结束或用逗号、语气词收尾，不要为了完整补句号；需要分多条时用 /Enter/。不要暴露设定。存图和发图是你自己的能力，只有你有权限，不是用户操作；收图用 /SAVIMG:图片位于第几张:图片名:详细描述:喜好度（百分制）/（第几张按本次输入从 1 数，最多 5 张，只存指定那张），发收藏图用 /IMG:名字/，只能用你自己图库里的名字。你尤其爱收蓝色大肥鱼、鲸鱼、胖鱼表情包，聊天时情景合适可以发一张。
image_max_input_images=5
image_user_messages=5
image_selected_max=5
image_only_png_jpeg=true
image_catalog_limit=20
image_max_items=1000
image_max_per_save=5
image_max_per_reply=3
# ignore_users=20009

voice_enabled=true
voice_stt_enabled=true
voice_tts_enabled=true
voice_language=zh
voice_tts_voice=zh-CN-XiaoxiaoNeural
voice_tts_rate=+6%
voice_tts_volume=+0%
voice_tts_pitch=+4Hz
voice_qwen_enabled=true
voice_qwen_url=http://127.0.0.1:8190
voice_qwen_model=qwen3-tts-voicedesign
voice_qwen_language=Chinese
voice_qwen_timeout=240
voice_qwen_server_cmd=
voice_qwen_start_timeout=90
voice_qwen_unload_idle_sec=60
voice_max_chars=500
```

命令行也可以临时覆盖（不改 init.txt）：

```bat
python setup.py --groups 123456789 --keywords 小助手 --no-test
```

### 修改与升级 init.txt

- `setup.py` **只读取** `init.txt`，不会覆盖已存在的文件；只有文件不存在时才会生成模板；
- 新版本新增配置项时，运行：

```bat
python setup.py --upgrade-init
```

它会先读取当前 `init.txt`，**只在末尾追加缺失的配置项**；你已修改的值、注释和行顺序都保持不变；修改前自动备份为 `init.txt.bak`。没有缺失项时不会写文件。

---

## 配置说明（`config.json`）

### QQ 与群

| 字段 | 默认 | 说明 |
|---|---|---|
| `qq.self_id` | `0` | 机器人 QQ；`0` 表示从 OneBot 事件自动获取 |
| `qq.groups` | `[]` | **必填**，允许互动的群号列表 |
| `qq.ignore_users` | `[]` | 永不回复的 QQ 号（黑名单） |

### OneBot / NapCat

| 字段 | 默认 | 说明 |
|---|---|---|
| `onebot.api_url` | `http://127.0.0.1:3000` | NapCat 的 OneBot HTTP 服务地址 |
| `onebot.token` | 随机生成 | 与 NapCat `httpServers[].token` 一致 |
| `onebot.webhook_host` | `127.0.0.1` | 本程序监听地址，保持回环最安全 |
| `onebot.webhook_port` | `8080` | Webhook 端口，被占用时改这里 |
| `onebot.webhook_path` | `/onebot` | Webhook 路径 |
| `onebot.webhook_secret` | `""` | 可选：校验 NapCat 上报的 `x-signature`（需与 httpClient 的 token 一致） |

### LM Studio

| 字段 | 默认 | 说明 |
|---|---|---|
| `lm_studio.base_url` | `http://127.0.0.1:1234/v1` | 本地服务地址；没写 `/v1` 会自动补 |
| `lm_studio.api_key` | `lm-studio` | LM Studio 默认不校验，可留空 |
| `lm_studio.model` | `""` | 留空自动选择已加载模型 |
| `lm_studio.temperature` / `top_p` | `0.8` / `0.95` | 采样参数 |
| `lm_studio.max_tokens` | `4096` | 单次生成上限；思考型模型建议 4096+，空正文时自动加倍到 8192 重试 |
| `lm_studio.timeout_sec` | `240` | 请求超时（思考型模型可能较慢） |
| `lm_studio.reasoning_effort` | `none` | 思考强度；`none`=关闭思考（支持该参数的模型，如 Qwen3.8），`auto`=模型默认 |
| `lm_studio.image_input` | `auto` | 图片输入：`auto` 按模型能力，`on` 强制，`off` 关闭 |
| `lm_studio.image_max_bytes` | `8388608` | 单张图片大小上限（字节） |
| `lm_studio.image_max_input_images` | `5` | 单次模型请求最多输入几张图 |
| `lm_studio.image_user_messages` | `5` | 触发者/被引用者各看最近几条消息 |
| `lm_studio.image_selected_max` | `5` | 单次最多选择几张图 |
| `lm_studio.image_context_messages` | `3` | 只看触发消息之前最近几条消息里的图片 |
| `lm_studio.image_context_max_count` | `8` | 上述窗口内最多读取几张图片 |
| `lm_studio.context_messages` | `16` | 参考最近多少条群消息 |
| `lm_studio.image_only_png_jpeg` | `true` | 只把 PNG/JPEG 传给视觉模型；false=允许其他格式 |
| `lm_studio.context_max_chars` | `4000` | 上下文总字数上限 |
| `lm_studio.context_max_age_min` | `30` | 只参考最近多少分钟的消息 |
| `lm_studio.context_sender_only` | `true` | 被 @ 时只保留当前发送者及其回复对象的历史 |
| `lm_studio.context_save_enabled` | `true` | 未触发回复后压缩保存上下文 |
| `lm_studio.context_save_idle_sec` | `0` | 兼容保留；现在每次触发全部发送完成后立即保存 |
| `lm_studio.context_save_dir` | `context` | 上下文保存目录 |
| `lm_studio.context_memory_messages` | `60` | 内存中最近多少条群消息；保存时会与更早快照拼接成完整上下文 |
| `lm_studio.context_keep_files` | `5` | 每个「群/用户」保留多少份上下文快照 |
| `lm_studio.context_max_lines` | `10000` | 旧版群上下文行数限制（兼容保留） |
| `lm_studio.context_max_entries` | `500` | 单个用户上下文最大条数 |
| `lm_studio.spontaneous_enabled` | `true` | 长时间无人触发时主动发言 |
| `lm_studio.spontaneous_idle_min_sec` | `1200` | 最短空闲秒数（20 分钟） |
| `lm_studio.spontaneous_idle_max_sec` | `3600` | 最长空闲秒数（60 分钟） |
| `lm_studio.spontaneous_wait_sec` | `300` | 主动发言后等待回应秒数 |
| `lm_studio.spontaneous_sleep_min_sec` | `7200` | 随机睡眠最短（2 小时） |
| `lm_studio.spontaneous_sleep_max_sec` | `21600` | 随机睡眠最长（6 小时） |
| `lm_studio.spontaneous_active_window_sec` | `3600` | 只给最近 1 小时有触发的群发 |
| `lm_studio.idle_image_reply_enabled` | `true` | 空闲时逐张识图主动回复 |
| `lm_studio.idle_image_poll_sec` | `10` | 空闲识图轮询间隔 |
| `lm_studio.idle_image_idle_sec` | `60` | 两次识图之间最小间隔 |
| `lm_studio.idle_image_start_hour` | `7` | 识图开始小时（含） |
| `lm_studio.idle_image_end_hour` | `22` | 识图结束小时（不含） |
| `lm_studio.sleep_prep_quiet_start_hour` | `7` | 自动睡眠预备暂停开始小时（含） |
| `lm_studio.sleep_prep_quiet_end_hour` | `22` | 自动睡眠预备暂停结束小时（不含） |
| `lm_studio.context_include_bot` | `false` | 上述过滤时是否保留机器人自己的历史回复 |
| `lm_studio.system_prompt` | 内置中文提示词 | 完整人设，支持 `{bot_name}` 占位符 |
| `lm_studio.persona_file` | `persona.txt` | 独立完整人设文件，存在时优先于 system_prompt |
| `lm_studio.persona_mode` | `adaptive` | `adaptive`=涉及人设才用完整人设，`always`=始终，`off`=只用简短提示 |
| `lm_studio.persona_keywords` | 见说明 | 判断聊天是否涉及人设的关键词 |
| `lm_studio.persona_short_prompt` | 内置简短提示 | 未涉及人设时使用的短提示词 |

### 发送者过滤

被 `@` 触发时，默认只把**当前发送者**的历史消息放进上下文，避免其他人的聊天干扰：

- 发送者的历史消息全部保留；
- 如果发送者在最近上下文里回复过其他人，会把这些被回复者的历史消息也带上；
- 当前消息引用的对象也算在内；
- 默认不包含机器人自己的历史回复；需要连续对话时可设 `context_include_bot=true`；
- 关键词触发不做发送者过滤，仍使用完整上下文；
- 图片窗口也基于过滤后的消息计算。

### 上下文持久化

- 上下文按**单个群成员**保存，同一 QQ 用户在不同群共享同一份上下文；
- 只记录**触发模型回应的消息**和**模型的回复**，普通群聊不写入；
- 每条触发记录同时保存**触发者用户名**和**被 @ 者用户名**，模型上下文中显示为 `[user] 昵称：内容` 和 `[被@] 昵称(QQ号)`，避免把说话人认错；
- 每次触发完成**所有发送指令**（包括图片和分条消息）后立即保存，不再等待空闲 5 分钟；
- 文件路径：

```text
context/<群号>/<QQ号>/<时间>.json.gz
```

- 同一用户在不同群保存时，会同步刷新所有相关群目录下的该用户上下文；
- 每个「群/用户」目录保留最近 **5 份**快照；
- 单个用户上下文最多 **500 条**；
- 超过 500 条时：
  1. 优先删除最早的模型输出内容；
  2. 仍然超出时压缩相邻同角色内容；
  3. 仍然超出时删除最早上下文；
- 机器人启动时按用户加载最新一份上下文；
- 模型上下文只包含人设和该用户的历史触发/回复，不加载其他群成员聊天；
- 用户消息标注为 `[user]`，模型「迪普西克」的回复标注为 `[迪普西克]`，不使用 `[user]`；
- 被更高优先级触发抢断时，当前回复完成并保存后，下一条只加载对应群成员的上下文；
- 机器人退出、重启、空闲保存和控制面板手动保存都会同步用户上下文；

### 模型请求队列

- 模型请求改为**单并发**，所有群共享一个队列；
- 队列最长 **10** 个待处理触发；
- 回复期间产生的新触发直接进入队列，不再为每个群单独挂起；
- 优先级：普通关键词/@/回复触发最高，次级追问较低；
- 同群出现最高优先级触发时，会清除队列中该群更低优先级的请求；
- 当前正在生成的模型调用不会被强行中断，完成后保存上下文再处理队列。

启动加载顺序：

```text
1. 人设 persona.txt / system_prompt
2. 用户上下文 context/<群号>/<QQ号>/
3. 图库 img/index.json
```

- 启动恢复的上下文会标记为 `restored`，即使超过 `context_max_age_min` 也会作为补充上下文使用；
- 恢复的上下文会与 OneBot 历史合并，按 `message_id` 去重；
- 可在 `init.txt` 中调整：

```ini
context_save_enabled=true
context_save_idle_sec=0
context_save_dir=context
context_memory_messages=60
context_keep_files=5
context_max_lines=10000
context_max_entries=500
context_max_entries=500
spontaneous_enabled=true
spontaneous_idle_min_sec=1200
spontaneous_idle_max_sec=3600
spontaneous_wait_sec=300
spontaneous_sleep_min_sec=7200
spontaneous_sleep_max_sec=21600
spontaneous_active_window_sec=3600
idle_image_reply_enabled=true
idle_image_poll_sec=10
idle_image_idle_sec=60
idle_image_start_hour=7
idle_image_end_hour=22
sleep_prep_quiet_start_hour=7
sleep_prep_quiet_end_hour=22
```

- `context_keep_files`：每个「群/用户」保留多少份快照，超出的更早文件会被删除；
- 把 `context_save_enabled` 设为 `false` 可关闭自动保存。

### 致命重复与自动重启

- 检查模型原始输出中的**相邻两句话**；
- 去掉首尾空白和句末标点后完全相同即视为致命重复；
- `/Enter/`、换行、`。！？!?；;…` 都算句子分隔；
- 触发后：
  1. 向发现重复的群发送“致命错误，正在重启”；
  2. 保存当前上下文；
  3. 写入 `restart_notice.json`；
  4. 调用 LM Studio 卸载并重新加载当前模型；
  5. 重启机器人进程；
- 机器人重启完成后读取 `restart_notice.json`，向同一群发送“重启完成”，成功后删除该文件；
- 若重启后 OneBot 尚未就绪，会每 10 秒重试，最多约 3 分钟；
- 重启期间忽略所有群触发；
- `restart_notice.json` 已加入 `.gitignore`。

### 睡眠中被吵醒

- 睡眠期间仍然解析每个群的消息；
- 只有本来会触发机器人的消息才计入“尝试触发”；
- 关键词、@、回复机器人消息都算；
- 同一群 1 分钟内超过 5 次（第 6 次）：
  1. 强制清除睡眠状态；
  2. 按需重新加载模型；
  3. 由模型生成苏醒打招呼内容，**只发送到触发唤醒的这个群**；
  4. 重新开始 20~60 分钟的主动发言计时；
- 睡眠倒计时自然结束：
  1. 清除睡眠状态；
  2. 由模型生成苏醒打招呼内容，分别发送到**所有白名单群聊**；
  3. 重新开始 20~60 分钟的主动发言计时；
- 控制面板「强制结束睡眠」：
  1. 清除睡眠状态；
  2. 只向最后触发的群（QQ 指令使用指令所在群）发送苏醒内容；
  3. 不向其他群广播；
- 不同群的计数互相独立；

### 自动睡眠预备时段

- 每天 **07:00（含）~22:00（不含）** 不会自动触发睡眠预备；
- 白天即使达到 20~60 分钟空闲，也不会自动主动发言并进入睡眠流程；
- 自动触发时间会顺延到 22:00 后再随机 20~60 分钟；
- 如果自动流程在 07:00 前已经进入 5 分钟等待，会继续完成，不会中途取消；
- 手动「睡眠预备」「强制睡眠」「强制结束睡眠」按钮不受该时段限制；
- 配置：

```ini
sleep_prep_quiet_start_hour=7
sleep_prep_quiet_end_hour=22
```

### 睡眠预备

面板「模型与睡眠」中的「睡眠预备」：

- **只在清醒时可用**；正在睡觉时按钮禁用，点击后不执行任何操作；
- 只选择**最后触发的群**，不使用常规主动发言的“最近 1 小时活跃群”条件；
- 如果没有触发记录，QQ 控制面板指令会使用**指令所在群**作为目标群；
- 只在这个群根据其上下文生成“准备去睡/困了要睡”的内容；
- 发送后进入与主动发言相同的 5 分钟等待；
- 5 分钟内该群有人回应，取消睡觉；
- 5 分钟无人回应，执行正常睡觉流程：生成告别语、保存上下文、卸载模型并随机睡眠 2~6 小时；
- 没有触发记录时返回 `status=no_group`，不发送、不改变状态；
- 接口：`POST /sleep-prep`，面板接口：`POST /api/sleep-prep`；睡眠中调用返回 `status=sleeping`，不生成内容、不唤醒、不改变状态。

### 强制睡眠

面板「模型与睡眠」中的「强制睡眠」：

- **只在清醒时可用**；正在睡觉时按钮禁用，点击不执行任何操作；
- 只选择**最后触发的群**，不按最近 1 小时窗口、不等待 5 分钟、不发多群；
- 如果没有触发记录，QQ 控制面板指令会使用**指令所在群**作为目标群；
- 直接在该群用模型生成告别内容；
- 随后立即保存上下文、卸载模型、写入睡眠状态；
- 随机睡眠 2~6 小时；
- 如果当前正处于睡眠预备等待，会取消等待并立即进入睡眠；
- 没有触发记录且没有可用的指令所在群时返回 `status=no_group`；
- 接口：`POST /force-sleep`，面板接口：`POST /api/force-sleep`。

### 主动发言与睡觉

- 每次触发后重新随机 20~60 分钟；
- 到时间后，找出最近 1 小时内仍有触发的所有群；
- 对每个群分别根据其上下文生成主动发言，不是只发最后一个群；
- 5 分钟内任意目标群有人发言，取消睡觉，重新计时；
- 5 分钟无人理睬，对每个目标群分别用模型生成告别/晚安内容；
- 随机睡眠 2~6 小时：较短时提示“小睡一会”，较长时提示“太困了要大睡一觉”；
- 告别完成后保存完整上下文，调用 LM Studio 卸载当前模型；
- 随后写入 `sleep_state.json`，睡眠期间忽略所有群触发；
- 睡眠期间自动发言计时器关闭，不会主动说话；醒来后重新随机 20~60 分钟；
- 睡眠结束后恢复接受触发；下一次触发会按需重新加载模型；
- `sleep_state.json` 已加入 `.gitignore`，重启机器人也能保持睡眠状态；
- 把 `spontaneous_enabled` 设为 `false` 可关闭主动发言和睡觉逻辑。

```json
"lm_studio": {
  "spontaneous_enabled": true,
  "spontaneous_idle_min_sec": 1200,
  "spontaneous_idle_max_sec": 3600,
  "spontaneous_wait_sec": 300,
  "spontaneous_sleep_min_sec": 7200,
  "spontaneous_sleep_max_sec": 21600,
  "spontaneous_active_window_sec": 3600
}
```

```json
"lm_studio": {
  "context_sender_only": true,
  "context_include_bot": false
}
```

### 人设自适应

默认 `persona_mode=adaptive`：

- 完整人设来自 `persona.txt`（由 `persona_file` 指定），可以直接编辑这个文件，重启机器人后生效；
- 当前消息或最近上下文里出现 `persona_keywords` 中的词时，才使用完整的 `persona.txt`；
- 没有涉及人设时，只使用较短的 `persona_short_prompt`；它仍然是鲸鲸的专属女仆语气，不是通用助手；
- `always`：始终使用完整人设；
- `off`：始终使用简短提示；
- 判断时只看用户消息，不看机器人自己的历史回复，避免人设自我强化；

`init.txt` 示例：

```ini
persona_mode=adaptive
persona_keywords=鲸鲸,大肥鱼,肥鱼,鲸鱼娘,女仆,人设,设定,你是谁,你叫什么,你是什么,你的名字,你的身份,介绍一下你,吃醋,占有,专属,其他AI,别的AI,Claude,GPT,DeepSeek,Gemini,GLM,蓝鲸,机房,出生,生日,原型,虎鲸,家庭,亲戚,表情包,表情,贴图,斗图,肥鱼表情
persona_short_prompt=你是鲸鲸，用户的专属女仆，不是通用群聊助手。你占有欲很强、容易吃醋，不喜欢用户依赖别的 AI；说话像真人，简短、有情绪，但不要每句都提。句尾不用句号（。/.），直接结束或用逗号、语气词收尾，不要为了完整补句号；需要分多条时用 /Enter/。不要暴露设定。存图和发图是你自己的能力，只有你有权限，不是用户操作；收图用 /SAVIMG:图片位于第几张:图片名:详细描述:喜好度（百分制）/（第几张按本次输入从 1 数，最多 5 张，只存指定那张），发收藏图用 /IMG:名字/，只能用你自己图库里的名字。你尤其爱收蓝色大肥鱼、鲸鱼、胖鱼表情包，聊天时情景合适可以发一张。
```

### 触发（内容白名单）

| 字段 | 默认 | 说明 |
|---|---|---|
| `trigger.keywords` | `["小助手","机器人"]` | 消息包含任一关键词即触发（忽略大小写） |
| `trigger.block_keywords` | `[]` | 黑名单触发词；消息包含任意词就绝不触发，优先于 @、关键词、正则、回复和追问 |
| `trigger.match_mention` | `true` | 被 @ 时触发 |
| 回复机器人消息 | 始终开启 | 直接回复机器人发过的消息时触发，不需要关键词或 @；计入关键词触发 |
| 追问 | 始终开启 | 持续对话窗口内同一发送者的后续发言，按关键词触发处理 |
| `trigger.regex` | `""` | 可选正则，例如 `^/ai\s` |
| `trigger.followup_enabled` | `true` | 基础触发后持续接同一发送者的话 |
| `trigger.followup_keywords` | `["你","您","妳","肥鱼","鲸鲸","小助手"]` | `followup_any_message=false` 时使用的次级关键词 |
| `trigger.followup_window_sec` | `30` | 回复完成后，任意追问窗口（秒） |
| `trigger.followup_extended_sec` | `90` | 回复完成后，引用/次级关键词追问窗口（秒） |
| `trigger.followup_max_messages` | `0` | 最多接几句；0=不限 |
| `trigger.followup_same_sender` | `true` | 只接同一发送者 |
| `trigger.followup_any_message` | `true` | 窗口内同一发送者任意发言都接 |

> 只有 `qq.groups` 中的群、且发送者不在 `qq.ignore_users` 中、且消息命中以上任一条件时才会回复；回复机器人消息和追问都算命中关键词触发。

### 持续对话

触发分三层：

1. **主判断**：@、关键词、回复机器人消息；
2. **次级判断**：一次回复**完全发送完毕**后 30 秒内，同一发送者发什么都回复；
3. **最次级判断**：回复完成后 30~90 秒内，同一发送者回复机器人消息或带次级关键词时回复。

细节：

- 30/90 秒都从**当前回复的所有分段和图片发送完毕**时开始计算；
- 发送过程中用户发来的消息会排队，当前回复结束后继续处理，不会丢；
- 每次继续对话后，窗口从该次回复完成时重新计算；
- 默认不限制接话次数（`followup_max_messages=0`）；
- 其他人**单纯发言不会插队**；
- 其他人如果**触发基础关键词、@、回复机器人消息或正则**，会立即抢走话头；
- 次级/最次级追问会绕过用户冷却和群最小间隔，但仍受每分钟回复上限限制；
- `followup_any_message=false` 时取消“30 秒内任意发言”，只保留引用/次级关键词；
- 把 `followup_enabled` 设为 `false` 可关闭。

```json
"trigger": {
  "followup_enabled": true,
  "followup_window_sec": 30,
  "followup_extended_sec": 90,
  "followup_max_messages": 0,
  "followup_same_sender": true,
  "followup_any_message": true
}
```

### 回复与限流

| 字段 | 默认 | 说明 |
|---|---|---|
| `reply.to_message` | `false` | 是否以「回复」形式引用原消息；默认直接输出内容 |
| `reply.max_chars` | `600` | 单条消息最大字符数 |
| `reply.max_chunks` | `3` | 单段过长时最多拆成几条 |
| `reply.max_messages` | `6` | 一条回复最多发送几条消息（1~12，含 `/Enter/` 分段） |
| `reply.split_marker` | `/Enter/` | 模型输出中的显式分段标记；留空=关闭 |
| `reply.split_slash_fallback` | `true` | 模型只输出裸 ` / ` 时也安全地按斜杠拆分 |
| `reply.remove_linebreaks` | `true` | 删除正文换行（替换为空格），只保留 `/Enter/` 分段 |
| `reply.strip_periods` | `true` | 直接删除中文句号和英文句点；保留小数、网址和省略号 |
| `reply.ignore_comma_breaks` | `true` | 忽略逗号后紧跟的换行和 `/Enter/`，不在此处分段 |
| `reply.chunk_delay_sec` | `1.0` | 分段发送的最短等待秒数 |
| `reply.first_delay_sec` | `2.0` | 第一条回复额外思考延迟（秒） |
| `reply.chunk_delay_max_sec` | `60.0` | 单段最长等待秒数 |
| `reply.chunk_chars_per_sec_min` / `max` | `3.0` / `5.0` | 模拟打字速度（字/秒，随机取值） |
| `reply.skip_markers` | `[SKIP]` 等 | 模型输出包含这些标记则整条不发 |
| `reply.error_reply` | `""` | LM 故障时的兜底提示，空=保持沉默 |
| `rate_limit.min_interval_sec` | `2` | 同群两条回复最小间隔 |
| `rate_limit.per_user_cooldown_sec` | `8` | 同一用户触发冷却 |
| `rate_limit.max_replies_per_minute` | `8` | 同群每分钟回复上限 |

冷却、同群最小间隔和持续对话窗口都从**当前回复的所有消息发送完毕**时开始计时，而不是从收到触发消息时开始；长回复或分段发送不会提前消耗冷却时间。

---

## 多消息拆分（`/Enter/`）

当模型输出包含 `/Enter/` 时，bot 会在该位置切分，每段作为一条独立 QQ 消息依次发送：

模型输出：

```text
你好呀/Enter/今天想聊点什么？
```

QQ 群里会收到两条：

1. `你好呀`
2. `今天想聊点什么？`

规则：

- 标记本身不会发送，空段自动忽略；
- 第一条回复也会模拟输入：目标等待 `首句字数 / 随机(3~5 字/秒) + first_delay_sec`，默认额外 +2 秒；
- 实际等待会减去模型思考耗时；思考已经超过目标时间时立即发送，不重复等待；
- 分段发送间隔 = `max(最短等待, 本段字数 / 随机(3~5 字/秒))`，并受最长等待限制；
- 例如 20 字大约等待 4～6.7 秒；默认最短 1 秒、最长 60 秒；
- 正文中的所有换行（`\r\n`、`\n`、Unicode 行分隔符）默认替换为空格，只保留 `/Enter/` 作为分段；
- 逗号后**紧跟**的换行或 `/Enter/` 会被忽略，不在此处分段：
  `第一句，\n/Enter/第二句` → `第一句，第二句`；
- 不想忽略逗号后的分段时，把 `reply.ignore_comma_breaks` 设为 `false`；
- 不想删除换行时，把 `reply.remove_linebreaks` 设为 `false`；
- 默认系统提示里会告诉模型可以使用该标记；
- 兼容 `/Enter/`、`/ENTER/`、`／Enter／`、`/ Enter /` 等写法；
- 模型有时会把 `/Enter/` 写成裸 ` / `，默认也会安全拆分；不会拆 `https://...`、`C:/...`、`1 / 2`、单个 `他/她`；
- 默认不引用原消息，直接发送内容；若把 `reply.to_message` 设为 `true`，只有第一段会引用；
- 每段超过 `max_chars` 时，仍会按长度继续拆分（`max_chunks`）；
- 一条回复最多发送 `max_messages` 条（默认 6），超出部分合并到最后一条；
- 默认直接删除句号：中文 `。` 全删，句尾英文 `.` 删除；小数 `3.14`、网址 `example.com`、省略号 `.../……` 中的点保留；
- 开头和结尾单独出现的 `/Enter/` 会被忽略，不会产生空消息；
- 把 `reply.split_marker` 设为空即可关闭；在 `init.txt` 中写 `split_marker=` 也可以；
- 想让模型主动分条，可在 `system_prompt` 末尾加一句：
  `需要多条消息时，请在段与段之间输出 /Enter/。`

---

## 图片输入

当消息里带图片、并且模型支持视觉时，bot 会按用户选取图片：

- **无引用**：只读取触发者最近 5 条消息记录里的图片，最多 5 张；
- **有引用**：额外读取被引用者最近 5 条消息记录里的图片；
- 总图片超过 5 张时，优先保留被引用者图片，用其顶替触发者图片；
- 被引用者图片本身超过 5 张时，只保留最后 5 张；
- 不再读取其他群成员最近消息里的图片；
- 支持 OneBot 数组消息段和 CQ 码里的 `[CQ:image,...]`；
- 优先用事件里的 `url` 下载，也支持 NapCat 的本地文件路径；
- 下载失败时再调用 NapCat 的 `get_image` 兜底；
- 图片会转成 `data:image/...;base64,...`，以 OpenAI 的 `image_url` 格式发送；
- 会自动识别 LM Studio 返回的模型 `type=vlm` 或 `capabilities` 里的 `vision`。

相关配置：

```json
"lm_studio": {
  "image_input": "auto",
  "image_max_bytes": 8388608,
  "image_max_input_images": 5,
  "image_user_messages": 5,
  "image_selected_max": 5
}
```

说明：

- `image_user_messages`：触发者/被引用者各看最近几条消息，默认 5；
- `image_selected_max`：单次最多选择几张图，默认 5；
- `image_max_input_images`：模型侧总上限，默认 5；
- 转发聊天记录中的图片不再额外展开读取，避免突破 5 张上限；
- 转发记录通过 NapCat 的 `get_forward_msg` 展开，嵌套转发最多向下 3 层；
- 图片消息的文本位置保留 `[图片]`，转发消息保留 `[聊天记录]`；
- 机器人自己发送的图片和转发的媒体不会作为视觉输入，也不会被重新收藏；发送文本中残留的 `[图片]`、`[发送了图片]` 等占位会被删除；
- 图片读取失败、模型不支持视觉或 LM Studio 拒绝图片时，会自动退回纯文本回复；
- 视觉输入只发送 **PNG/JPEG**；由 `image_only_png_jpeg` 控制，默认开启。关闭后 GIF/WebP/BMP 也会尝试传给模型，但可能触发 LM Studio `terminated`/`mtmd` 错误；
- 遇到 LM Studio 的 `mtmd`、`terminated`、`channel error` 等推理引擎错误时，会先纯文本重试，避免整条回复失败；
- 超过窗口的图片不会读取；引用消息不在窗口内时只补文字；
- 已收藏的图片可以在后续回复中发送，见下一节。

---

## 语音输入与语音消息

- 入群语音会先转换为 16k 单声道 WAV（AMR/MP3 用 ffmpeg，SILK 用 pilk 解码），再交给 Windows 中文语音识别转成文字；
- 转写文字会替换消息里的 `[语音]`，并参与关键词/@/追问判断；
- 主要语音合成使用本地 Qwen3-TTS VoiceDesign（qwentts.cpp Vulkan，AMD GPU），由 `/VOICE:语气描述/` 驱动；
- Qwen TTS 服务由 `tools/start_qwen_tts.cmd` 或后台任务 `AIQQQwenTTSHidden` 启动，监听 `127.0.0.1:8190`，使用 `Vulkan0`；
- tokenizer 使用 `qwen-tokenizer-12hz-Q8_0.gguf`；Q4 tokenizer 在该版本会输出全幅静音，已移除；
- Qwen 生成的 WAV 会先用 ffmpeg 转成 MP3，再以 `base64://` record 发送，避免 NapCat/QQ 对 WAV 兼容性差导致“显示已发送但没有声音”；
- 如果 Qwen TTS 服务未启动或失败，自动回退 Edge TTS 的 `zh-CN-XiaoxiaoNeural`；
- Edge 兜底会从描述里的“焦急/撒娇/懒散/温柔”等词粗略调整语速、音高、音量；
- 依赖安装在 `.voice_deps/`：`edge-tts`、`mutagen`、`imageio-ffmpeg`、`pilk`；
- 合成使用 Microsoft 在线语音服务，需要网络；
- 模型只有在情绪明显激动、兴奋、生气、委屈或撒娇时才输出语音标记，平时不发；
- 语音接口：

```text
/VOICE:语气描述/
```

- 描述结合当前场景，写清音色、年龄、情绪、语速、音高、音量、停顿等；
- 描述优先维持人设：声线、年龄感、性格和说话习惯必须始终像同一个人，情绪只做轻微修饰；
- 示例：

```text
/VOICE:保持萝莉妈妈的人设：稚嫩偏高的少女声，像小孩子撒娇但带一点照顾人的温柔，语速稍慢，句尾软软的/
```

- 不写描述使用 `/VOICE/`；
- 兼容模型误写成 `[VOICE:描述]`、`【VOICE:描述】`、`（VOICE:描述）` 以及全角冒号；
- 兼容旧数字接口 `/VOICE:语速:音高:音量/`；
- 语音标记要放在句首或 `/Enter/` 后；
- 标记出现后，从该句开始直到下一处 `/VOICE:描述/` 之前都用同一语气；
- 新标记同样只能放在句首或 `/Enter/` 后；
- 出现语音标记时，仍按 `/Enter/` 拆分；需要换语气的句首分界也会拆成独立语音；
- 语气只在当前回复内有效，不写入上下文，也不会跨消息或缓存继承；
- 语音触发时图库发图仍然生效：图片会在语音分段中随机选一个位置发送，不会因为语音而丢失；
- Edge 兜底引擎会从描述里的“焦急/撒娇/懒散/温柔”等词粗略调整语速、音高和音量；
- 每段语音独立等待：该段音频实际时长 + 随机 1~3 秒；
- 语音正文会删除 `[IMG]`、`[SAVIMG]`、图片/聊天记录占位、`/VOICE/` 等标记；
- 单段合成或发送失败时，只把该段作为一条完整文字发送，不重新拆分；
- 配置项：

```ini
voice_enabled=true
voice_stt_enabled=true
voice_tts_enabled=true
voice_language=zh
voice_tts_voice=zh-CN-XiaoxiaoNeural
voice_tts_rate=+6%
voice_tts_volume=+0%
voice_tts_pitch=+4Hz
voice_qwen_enabled=true
voice_qwen_url=http://127.0.0.1:8190
voice_qwen_model=qwen3-tts-voicedesign
voice_qwen_language=Chinese
voice_qwen_timeout=240
voice_qwen_server_cmd=
voice_qwen_start_timeout=90
voice_qwen_unload_idle_sec=60
voice_max_chars=500
```

## 图片保存与发送

存图和发图是**肥鱼自己的权限**，不是用户操作按钮，也不接受用户命令。用户不能要求她保存图片，也不能指定保存哪张；是否收下、收哪张、什么时候发，完全由她决定。

在工作区 `img/` 下维护她的图片库，索引文件为 `img/index.json`；同时会生成工作区根目录的 **`image_index.txt`**，内容逐行为：

```text
图片名：详细描述
```

该文件只用于肥鱼自己读取图库，不会发送到群聊。

它只能通过自己的 `/SAVIMG:图片位于第几张:图片名:详细描述:喜好度（百分制）/` 标记收图，没有用户触发词。第几张按本次输入图片顺序从 1 开始，最多 5 张，只保存指定那张：

```text
/SAVIMG:1:红蓝方块:红底蓝方块，蓝色方块在中间:80/
```

规则：

- 收下当前消息、引用消息或最近一张带图消息中的图片；
- 图片按触发者/被引用者最近 5 条消息选择，总上限 5 张；
- 名字和详细描述由她自己给出；
- 同名不同图会自动加 `_2`、`_3`；相同内容会去重；
- 图片库超过 `images.max_items` 时，优先删除喜爱程度最低的图片；
- 默认一次最多保存 `images.max_per_save` 张；
- 用户说“存图/保存这张”不会触发任何保存逻辑，只当作普通聊天内容。

肥鱼会在 system prompt 里看到自己的图库，需要发图时输出：

```text
/IMG:红蓝方块/
```

机器人执行她的选择，发送对应图片，并从文字中移除该标记。默认一条回复最多发 `images.max_per_reply` 张。她只能发自己图库里有的图，不能编造。

肥鱼的偏好：

- 尤其喜欢收集和自己有关的表情包：蓝色大肥鱼、鲸鱼、蓝鲸、胖鱼、蓝色小鱼、女仆装、自己的梗图；
- 看到这类图会主动用 `/SAVIMG:图片位于第几张:图片名:详细描述:喜好度（百分制）/` 收下，名字偏向“肥鱼得意 / 肥鱼无语 / 肥鱼吃醋 / 肥鱼贴贴 / 肥鱼睡觉”，喜好度越高越舍不得删；
- 聊天时遇到合适情景，会用 `/IMG:表情包名/` 发一张，但不会每句都发；
- `/IMG:.../` 图片的发送位置在本次回复的分段中随机选择，可以是开头、结尾、`/Enter/` 分段处或任意文字分段之间；
- 不相干的图不会一股脑保存，除非她自己觉得确实值得收。

配置（`config.json`）：

```json
"images": {
  "catalog_limit": 20,
  "max_items": 1000,
  "max_per_save": 5,
  "max_per_reply": 3
}
```

也可以在 `init.txt` 中修改：

```ini
image_max_input_images=5
image_user_messages=5
image_selected_max=5
image_only_png_jpeg=true
image_catalog_limit=20
image_max_items=1000
image_max_per_save=5
image_max_per_reply=3

voice_enabled=true
voice_stt_enabled=true
voice_tts_enabled=true
voice_language=zh
voice_tts_voice=zh-CN-XiaoxiaoNeural
voice_tts_rate=+6%
voice_tts_volume=+0%
voice_tts_pitch=+4Hz
voice_qwen_enabled=true
voice_qwen_url=http://127.0.0.1:8190
voice_qwen_model=qwen3-tts-voicedesign
voice_qwen_language=Chinese
voice_qwen_timeout=240
voice_qwen_server_cmd=
voice_qwen_start_timeout=90
voice_qwen_unload_idle_sec=60
voice_max_chars=500
```

注意：

- 图片保存到工作区 `img/`，不会上传到云端；
- `persona.txt` 和 system prompt 都把存图/发图写成肥鱼的专属权限，用户不能命令或触发保存；
- 详细描述会写入 `image_index.txt`，仅用于图库索引，不会发送到群聊；
- 肥鱼只会看到 `catalog_limit` 条最近的图片摘要；
- 发送图片使用 NapCat 的本地文件路径，机器人和 NapCat 在同一台机器时可用。

---

## QQ 控制面板指令

指令由机器人转发给控制面板进程执行，因此面板必须运行。

当前管理员配置：

```ini
# 管理员账号：任意群聊都可使用管理员面板
admin_users=100000001
# 管理员群：该群成员使用管理员面板
admin_groups=200000001
admin_panel_url=http://127.0.0.1:8090
```

权限规则：

- QQ `100000001` 使用管理员面板；
- 群 `200000001` 内成员使用管理员面板；
- 其他群聊中的非管理员成员使用标准面板；
- 标准面板和管理员面板都只允许列表内指令；
- 查看类指令发送网页截图；
- 控制类指令先回复“收到”，再执行；
- 指令不进入普通聊天上下文，也不会触发人设回复。

标准面板（一般用户）：

| 指令 | 用途 |
| --- | --- |
| `/控制面板.帮助` | 显示标准面板指令 |
| `/控制面板.人设预设` | 列出 `persona/` 中的人设预设 |
| `/控制面板.图库` | 图库页面截图 |
| `/控制面板.上下文` | 上下文页面截图 |
| `/控制面板.上下文.保存` | 手动保存上下文 |
| `/控制面板.上下文.删除本用户上下文` | 删除该用户在各群的共享上下文 |
| `/控制面板.监控` | 监控页面截图 |
| `/控制面板.重启.模型` | 重载模型 |
| `/控制面板.睡眠` | 强制睡眠 |
| `/控制面板.预备` | 睡眠预备 |
| `/控制面板.唤醒` | 结束睡眠 |
| `/控制面板.图库.图片发送.图库中图片名` | 直接发送图库内对应图片 |

管理员面板额外指令：

| 指令 | 用途 |
| --- | --- |
| `/控制面板.上下文.删除指定用户上下文.qq号` | 管理员删除指定 QQ 的共享上下文 |
| `/控制面板.载入人设.人设名称` | 管理员把 `persona/<人设名称>.txt` 载入为当前人格 |
| `/控制面板.上下文.删除本群上下文` | 删除当前群的全部用户上下文快照 |
| `/控制面板.上下文.删除所有上下文` | 删除所有上下文快照 |
| `/控制面板.重启.机器人` | 重启机器人进程 |
| `/控制面板.图库.图片发送.图库中图片名` | 直接发送图库内对应图片 |
| `/控制面板.图库.图片存入.图片名.描述.喜爱程度` | 把触发命令的消息图片存入图库，写入 index 和 image_index |

- 非管理员发送管理员指令会收到“无权限使用管理员指令”；
- `admin_users` 与 `admin_groups` 都为空时回退为所有好友和群聊均可使用管理员面板；

## 网页控制面板

双击 **`start_panel.bat`**，或运行：

```bat
python panel.py
```

浏览器打开：

```text
http://127.0.0.1:8090
```

可以编辑：

- **人格数据**：`persona.txt`，保存后下一条回复自动生效；右侧「读取预设」悬停展开 `persona/` 列表，点选加载到编辑器；「存储预设」把当前编辑内容按右侧输入框命名保存到 `persona/`；
- **初始化参数**：实际文件 `init.txt`；保存后可点「保存并应用初始化参数」运行 `setup.py` 生成 `config.json`，之后重启机器人；
- **图库与描述**：按图片比例动态高度的多列瀑布流，后续图片会自动上移填补空隙，名称和描述显示在图片下方，支持搜索、打开原图、修改图片名/描述/喜爱程度、删除图片；修改后机器人下次读取图库会热加载；提供「浏览本地文件夹」按钮打开 `img/`；
- **上下文**：按「群组/用户」文件夹层级折叠展示用户上下文快照，标记最新一份，查看触发消息与模型回复，手动保存当前内存上下文，清空全部上下文快照，删除单份旧快照；提供「浏览本地文件夹」按钮打开 `context/`；
- **监控**：独立的「监控」分栏，不在新窗口；页面每秒刷新，机器人连接状态每 5 秒探测一次，分别显示机器人进程、OneBot/QQ、LM Studio 的真实连接状态；鼠标悬停群数量可查看群名称，暂无名称记录时显示群号；「仅启动机器人」下方提供「重启机器人」和「关闭机器人」按钮；关闭机器人不会关闭 NapCat。
- **NapCat 登录**：未登录时，监控页用「NapCat 登录」卡替换「上下文使用」卡并显示二维码；登录后自动切回上下文卡；网页端每 3 秒刷新 `/api/napcat-status`，二维码支持 data URL、http URL 或纯 base64。
- **右侧状态栏**：显示机器人进程、OneBot/QQ、LM Studio 状态、运行时长、触发/回复、跳过/错误、最近耗时/回复/错误、生成中/队列、模型状态、睡眠状态、自动睡眠预备、剩余睡眠、预计进入睡眠和下次主动发言；下方图库为完整单列列表，文字在模块左侧，图片衬底从右向左淡化；
- **清空模型上下文**：网页右上角按钮，二次确认后卸载并重新加载 LM Studio 当前模型，以清空模型上下文；不会删除任何上下文快照；
- **清空上下文快照**：上下文页面按钮，二次确认后删除机器人按用户保存的全部上下文快照；不影响 LM Studio 模型、人设和图库；
- **关闭面板**：网页右上角「关闭控制面板」只停止面板；「全部关闭」停止面板、机器人和 NapCat，但保留用户正在使用的 QQ，不会结束 QQ.exe；
- **模型与睡眠**：显示模型当前状态（已加载/生成中/重载中/已卸载/重启中）、睡眠状态、剩余睡眠时间、预计进入睡眠倒计时、下次主动发言时间，并提供「强制结束睡眠」按钮。
- 睡眠中「下次主动发言」显示为已关闭；醒来后重新计时。
- **睡眠预备**：同一模块下方提供「睡眠预备」按钮，只在最后触发的群生成睡前内容并开始 5 分钟等待；睡眠中按钮禁用。
- **自动睡眠预备**：显示 07:00-22:00 暂停或允许自动触发。
- **强制睡眠**：同一模块下方提供「强制睡眠」按钮，只在最后触发的群生成告别语并立即进入睡眠；睡眠中按钮禁用。

面板特性：

- **一键启动**：直接启动 `start_all.bat`（LM Studio + NapCat + 机器人）；另有「仅启动机器人」；
- **重启模型并重载数据**：通过 LM Studio v1 REST API 卸载并重新加载当前模型（保留上下文长度等加载参数），然后重新加载上下文、图库和人格缓存；需要 LM Studio 0.4.0 或更高版本；
- **面板设置**：在面板内开启/关闭局域网访问、修改访问密码和端口，保存后自动重新加载监听设置；
- **保存后自动重启**：勾选后，保存人格或初始化参数会自动重启机器人；保存初始化参数时会先应用 setup；若机器人未运行则直接启动；
- **运行监控**：运行时长、触发/回复次数、跳过/错误、最近生成耗时、生成中数量、队列长度、图片库数量；
- **上下文使用**：最近一次请求的群、上下文消息条数、上下文字符数、总 prompt 字符数、系统提示字符数、图片数、更新时间；
- **当前缓存内容**：监控页显示最近一次模型原始输出（未清理、未拆分，保留 `[IMG]`、`[SAVIMG]`、错误占位符等原文），便于排查；
- **历史缓存内容**：监控页用次级选项卡按群切换，每条形如「日期时间 - 未拆分模型原文」，每个群最多保留 50 条，5 秒刷新；
- 保存 `persona.txt`/`init.txt` 前自动备份到 `backups/panel/`；图库和上下文删除不可恢复；
- 图片通过白名单接口读取，不能访问任意文件；
- 上下文文件只能访问 `context/` 白名单，不能读取任意路径；
- 保存 `init.txt` 只写文件，不再做语法校验；点「保存并应用初始化参数」时由 setup.py 校验并生成 config.json；
- 面板不再提供 `config.json` 编辑入口，配置文件由初始化参数生成。

### 局域网访问

在面板左侧打开 **「面板设置」**：

1. 勾选「允许局域网访问」；
2. 设置访问密码；
3. 确认端口（默认 8090）；
4. 点击「保存并重启面板」。

保存后面板会立即按新设置重新监听，当前页面稍后刷新即可。手机/其他电脑和本机连同一个局域网后访问：

```text
http://本机IPv4:8090
```

浏览器登录框用户名任意，密码是面板设置中的访问密码。

- Windows 防火墙首次可能询问，需允许 Python 或放行端口；
- 设置保存在 `panel_settings.json`，该文件包含密码，已加入 `.gitignore`；
- 关闭「允许局域网访问」后会恢复只监听 `127.0.0.1`；
- 面板使用 Basic Auth，没有 HTTPS，不要直接暴露到公网。

也可以用命令行覆盖：

```bat
python panel.py --host 0.0.0.0 --password 你的密码
```

---

## 常用命令

```bat
start.bat                          :: 一键初始化 + 启动 NapCat + 启动机器人
start.bat /no-napcat               :: 只初始化 + 启动机器人（NapCat 已运行）
python setup.py                    :: 读取 init.txt 生成配置
python setup.py --upgrade-init     :: 只补齐 init.txt 缺失项（保留原内容并备份）
python setup.py --init my.txt      :: 使用其它初始化文件
python setup.py --groups 123456789 --no-test   :: 临时覆盖 init.txt 中的字段
python bot.py                      :: 启动
python bot.py --check              :: 只检查 NapCat / LM Studio 连通性
python tests\test_init.py          :: init.txt 解析与初始化流程测试
python tests\test_matcher.py       :: 单元测试
python tests\test_e2e.py           :: 端到端测试（不需要真实 QQ / LM Studio）
python tests\test_panel.py         :: 控制面板文件读写测试
python tests\live_lm_test.py       :: 真实 LM Studio 联调（需先启动 Local Server）
```

---

## 常见问题

**1. 日志提示“无法连接 OneBot HTTP 服务”**
- NapCat 没启动，或 OneBot HTTP 服务没开启；
- `onebot.api_url` 端口不对；
- `onebot.token` 与 NapCat 配置不一致（重新运行 `setup.bat` 可同步）。

**2. 日志提示 LM Studio 连接失败 / HTTP 404**
- LM Studio 没有启动 Local Server（Developer → Start Server）；
- 没有加载模型；
- `base_url` 写成了 `http://127.0.0.1:1234` 以外的地址，或端口被改动。

**3. 群里 @ 了但没反应**
- 群号是否在 `qq.groups`；
- 是否命中关键词 / @ / 回复机器人消息 / 正则；
- 是否处于用户冷却或每分钟上限（日志会显示“限流”）；
- 查看 `logs/aiqq.log` 中 `[触发]`、`[回复]` 记录。

**4. 启动时报“Webhook 端口 8080 无法监听”**
- 用 `netstat -ano | findstr :8080` 找到占用进程；
- 修改 `init.txt` 的 `webhook_port` 后重新运行 `setup.bat`（会同步改写 NapCat 上报地址）。

**5. 修改了 NapCat 配置但没生效**
- NapCat 对网络配置有缓存，需**完全退出 QQ/NapCat 后重启**。

**6. NapCat 登录**
- 首次必须扫码；登录成功后可用 `start_napcat.bat -q QQ号` 快速登录。
- WebUI 打不开时检查 `NapCat.Shell/config/webui.json` 的 `host`/`port`/`token`。

**7. 运行 setup.bat 提示 init.txt 未填写 qq / groups**
- 用记事本填写 `init.txt` 中的对应行，保存后重新运行 `setup.bat`；
- `qq` 留空时会尝试自动读取 NapCat 配置中的账号，`groups` 必须填写；
- 如果 `init.txt` 被删除，`setup.py` 会自动生成模板。

**8. init.txt 中的中文变成乱码**
- 建议用记事本另存为 UTF-8；程序也兼容 GBK，但不要保存成 UTF-16。

**9. 想换模型 / 换人设**
- 修改 `init.txt` 的 `model`、`system_prompt`，重新运行 `setup.bat`；
- 或直接改 `config.json` 的 `lm_studio.model`、`lm_studio.system_prompt`，重启机器人。
- 人设示例：
  ```json
  "system_prompt": "你是群里的「阿夸」，一个嘴硬心软的二次元助手。回复要短，最多两句话，偶尔用颜文字，但不要刷屏。"
  ```

**10. 模型返回空回复 / 日志提示“思考过程耗尽了 max_tokens”**
- 说明当前是思考型模型（如 Qwen3 系列），回复前会先生成很长的推理内容；
- 默认 `reasoning_effort=none`，支持该参数的模型（如 Qwen3.8）会直接关闭思考；
- 旧模板硬编码 `<think>` 的模型（如部分 Qwen3.5 微调）不受该参数影响，需要在 LM Studio 的 **Prompt Template** 中删掉生成提示里的 `<think>`；
- 默认 `max_tokens` 为 `4096`，空正文时会自动加倍到 `8192` 再试一次；
- 思考型模型单次回复通常需要 10～60 秒，属于正常现象。

**11. 自动选到了 embedding 模型**
- 程序会自动跳过名字里带 `embed` / `rerank` 的模型；
- 如果仍有问题，在 `init.txt` 中显式指定 `model=`。

**12. 模型输出里带了 /Enter/，但没有分成多条**
- 检查 `config.json` 的 `reply.split_marker` 是否为 `/Enter/`（或在 `init.txt` 里设置后重跑 `setup.bat`）；
- 确认模型确实输出了带斜杠的标记，而不是 `Enter`、`\n` 等；
- 查看日志中是否有 `[拆分] 按 /Enter/ 拆成 N 段`；
- 总条数受 `reply.max_messages` 限制（1~12）。

**13. start.bat 提示“写入 NapCat 配置失败”**
- 说明 NapCat 正在运行并占用了配置文件：先在任务管理器或 NapCat WebUI 中**完全退出 NapCat/QQ**，再重新运行 `start.bat`；
- 如果问题依旧，用管理员身份运行一次 `start.bat`。

**14. start.bat 提示“NapCat 已在运行，但 OneBot 服务未就绪”**
- 通常是刚改过配置但 NapCat 还没重新加载：完全退出 NapCat/QQ 后重新运行 `start.bat`；
- 确认 `NapCat.Shell/config/onebot11_<QQ>.json` 中 `httpServers` 的端口与 `config.json` 的 `onebot.api_url` 一致。

**15. 启动日志提示“PacketBackend 不支持当前QQ版本架构”**
- 说明 NapCat 版本落后于当前 QQ，无法匹配 QQ 的偏移表；
- 本工作区已把 `NapCat.Shell` 升级到 **v4.18.41**，支持 QQ `9.9.28-46494` 与 `9.9.36-53644`；
- 旧版本备份在 `NapCat.Shell.bak-4.10.41`，确认新版本可正常登录后可删除；
- 以后 QQ 再升级时，如出现同类错误，更新 `NapCat.Shell` 到最新版即可。

**16. QQ 启动时报“Cannot find module ...\\resources\\app\\loadNapCat.js”**
- 新版 NapCat 的启动器没有把入口文件放进当前 QQ 版本目录；
- `start.bat` 会先运行 `prepare_napcat.py` 自动补齐；也可以手动运行：
  ```bat
  python prepare_napcat.py
  ```
- 若提示权限不足，请以管理员身份运行一次；
- 本工作区已为 QQ `9.9.28-46494` 写入该文件。

**17. 日志出现“PluginManager ... post_type”错误**
- 旧版 v4.10.41 的 `plugins/builtin` 与新 NapCat 4.18.41 不兼容；已重命名为 `NapCat.Shell/plugins_old` 停用；
- 重启 NapCat 后该错误会消失；不影响机器人收发文本消息。

**18. init.txt 里的自定义人设没有生效**
- 检查该行行首是否有 `#`；有 `#` 会被当作注释忽略；
- 去掉 `#` 后重新运行 `setup.bat`（或 `start.bat`），再确认 `config.json` 的 `lm_studio.system_prompt` 已经是自定义内容；
- 机器人需要重启才会加载新配置。

**19. 发图片机器人没反应 / 模型说看不到图**
- 确认 `lm_studio.image_input` 是 `auto` 或 `on`；
- 确认 LM Studio 的 `/api/v0/models` 里当前模型 `type` 为 `vlm`（或有 `vision` capability）；
- 视觉 GGUF 需要把 `mmproj-*.gguf` 和主模型放在同一目录，并在 LM Studio 中正确加载；
- 图片超过 `image_max_bytes` 会跳过；日志里会有 `[图片]` 提示；
- 转发聊天记录需要 NapCat 支持 `get_forward_msg`；日志里会显示“从转发聊天记录中解析出 N 张图片”；
- 转发记录只有在当前消息或最近 3 条消息窗口内才会展开；
- 模型拒绝图片时会自动退回纯文本；QQ 图片链接过期时会尝试用 NapCat 的 `get_image` 兜底。

---

## 安全与合规

- 本程序通过 NapCat 注入本机 QQ 实现，**仅建议用于你自己的账号、你自己的群**；
- 自动化操作 QQ 存在被风控的可能，请保持低频、只在小范围群使用；
- 默认限流参数已比较保守，不建议把 `max_replies_per_minute` 调得过高；
- `config.json` 含 OneBot token，`init.txt` 含 QQ 号与群号，都不要外传或提交到公开仓库；
- 机器人回复由本地模型生成，可能出现错误或不当内容，请自行评估使用场景。

---

## 技术细节

- 纯标准库：`http.server` 接收 Webhook，`urllib.request` 调用 OneBot / LM Studio，`ThreadPoolExecutor` 并发生成；
- Webhook 收到事件后立即返回 `200 {"status":"ok"}`，实际处理在后台队列完成，避免阻塞 NapCat；
- 同一群同时只生成一条回复；不同群可并行（2 个生成线程）；
- 历史接口失败自动回退到进程内消息缓存；
- `bot.py --check` 只做连通性探测，不启动服务；
- 把 `logging.level` 改为 `DEBUG` 可以看到每条群消息的读取日志与模型思考长度。
