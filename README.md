# AIQQ 最小一键运行包

安装好 **LM Studio、QQ、Python 3.10+** 后，只需要下载本项目，双击 `start.bat` 即可启动。

## 首次启动

1. 确保 QQ 已安装并登录；
2. 确保 LM Studio 已安装，并在 LM Studio 中加载一个模型、启动本地服务器（端口 1234）；
3. 双击 `start.bat`；
4. 按提示填写机器人 QQ、监听群号；管理员可留空；
5. 程序会自动下载 NapCat.Shell、生成 `config.json`；
6. 首次使用 NapCat 时打开 `http://127.0.0.1:8090` 扫描登录二维码；
7. 之后双击 `start.bat` 即可直接启动。

## 包含

- QQ 群聊接入、触发/追问/限流
- 上下文记忆和图库
- 网页控制面板
- QQ 控制面板指令
- 人设预设读取/存储
- 睡眠、唤醒、监控
- 一键下载 NapCat、一键启动和隐藏 CMD

## 不包含

- Qwen3-TTS
- Edge TTS 及语音依赖
- `qwentts.cpp`、模型权重
- 测试
- `persona/` 预设文件（运行时自行添加）

## 外部依赖

- LM Studio 及模型
- QQ / NapCat（NapCat 由首次启动自动下载）
- Python 3.10+

## 许可证

MIT，见 `LICENSE`。
