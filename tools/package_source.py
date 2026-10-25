import json
import os
import shutil
import sys
import zipfile

ROOT = r"D:\AIQQ"
STAGE = os.path.join(ROOT, "dist", "AIQQ-src")
if os.path.isdir(STAGE):
    shutil.rmtree(STAGE)
os.makedirs(STAGE, exist_ok=True)

FILES = [
    "bot.py", "panel.py", "panel.html", "setup.py", "prepare_napcat.py",
    "start.bat", "start_all.bat", "start_bot.bat", "start_panel.bat",
    "start_panel_lan.bat", "start_napcat.bat", "start_lmstudio.bat",
    "start_hidden.vbs", "setup.bat", "bootstrap.py",
    ".gitignore", "init.example.txt", "config.example.json", "README.md",
]
for name in FILES:
    source = os.path.join(ROOT, name)
    if os.path.isfile(source):
        shutil.copy2(source, os.path.join(STAGE, name))

shutil.copytree(
    os.path.join(ROOT, "tests"),
    os.path.join(STAGE, "tests"),
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
)
shutil.copytree(
    os.path.join(ROOT, "aiqq"),
    os.path.join(STAGE, "aiqq"),
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
)

REPLACEMENTS = {
    "100000001": "100000001",
    "200000001": "200000001",
    "100000002": "100000002",
    "8888": "change-me",
}


def sanitize(path):
    try:
        text = open(path, encoding="utf-8").read()
    except Exception:
        return
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    open(path, "w", encoding="utf-8").write(text)


for dirpath, _dirs, names in os.walk(STAGE):
    for name in names:
        if name.endswith((".py", ".html", ".md", ".txt", ".json", ".bat", ".cmd", ".vbs")):
            sanitize(os.path.join(dirpath, name))

bot_path = os.path.join(STAGE, "bot.py")
text = open(bot_path, encoding="utf-8").read()
text = text.replace('"admin_users": [100000001]', '"admin_users": []')
text = text.replace('"admin_groups": [200000001]', '"admin_groups": []')
open(bot_path, "w", encoding="utf-8").write(text)

setup_path = os.path.join(STAGE, "setup.py")
text = open(setup_path, encoding="utf-8").read()
text = text.replace("admin_users=100000001", "admin_users=")
text = text.replace("admin_groups=200000001", "admin_groups=")
open(setup_path, "w", encoding="utf-8").write(text)

sys.path.insert(0, ROOT)
import bot

cfg = json.loads(json.dumps(bot.DEFAULT_CONFIG, ensure_ascii=False))
cfg["qq"]["admin_users"] = []
cfg["qq"]["admin_groups"] = []
cfg["qq"]["groups"] = []
cfg["qq"]["self_id"] = 0
cfg["lm_studio"]["model"] = ""
cfg["lm_studio"]["base_url"] = "http://127.0.0.1:1234/v1"
cfg["lm_studio"]["persona_file"] = "persona.txt"
cfg["lm_studio"]["context_save_dir"] = "context"
cfg["lm_studio"]["persona_keywords"] = ["助手", "机器人"]
cfg["lm_studio"]["persona_short_prompt"] = ""
open(os.path.join(STAGE, "config.example.json"), "w", encoding="utf-8").write(
    json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")

persona_source = os.path.join(ROOT, "persona", "空模板.txt")
if os.path.isfile(persona_source):
    shutil.copy2(persona_source, os.path.join(STAGE, "persona.txt"))
else:
    persona = "\n".join([
        "你是群里的 AI 助手，说话简短、自然、有礼貌。",
        "",
        "默认用简体中文回复，一次 1~3 句话；不要使用 Markdown 标题、表格或代码围栏。",
        "",
        "结合群聊上下文理解问题；不确定的信息要说明，不要编造。",
        "",
        "不要输出 CQ 码，不要 @ 别人，不要长篇大论。",
        "",
        "需要分多条消息时，用 /Enter/ 分隔。",
        "",
    ])
    open(os.path.join(STAGE, "persona.txt"), "w", encoding="utf-8").write(persona)

os.makedirs(os.path.join(STAGE, "tools"), exist_ok=True)
qwen_lines = [
    "@echo off",
    'cd /d "%~dp0.."',
    'if not exist "qwentts.cpp\\build-vulkan\\tts-server.exe" exit /b 1',
    'start "" /b "qwentts.cpp\\build-vulkan\\tts-server.exe" ^',
    '  --model "qwen3_tts_serveur\\qwen-talker-1.7b-voicedesign-Q4_K_M.gguf" ^',
    '  --codec "qwen3_tts_serveur\\qwen-tokenizer-12hz-Q8_0.gguf" ^',
    "  --device Vulkan0 --host 127.0.0.1 --port 8190 --alias qwen3-tts-voicedesign",
    "",
]
open(os.path.join(STAGE, "tools", "start_qwen_tts.example.cmd"), "w", encoding="utf-8").write(
    "\r\n".join(qwen_lines))

open(os.path.join(STAGE, "requirements-voice.txt"), "w", encoding="utf-8").write(
    "edge-tts\nmutagen\nimageio-ffmpeg\npilk\n")

license_text = """MIT License

Copyright (c) 2026 AIQQ contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
open(os.path.join(STAGE, "LICENSE"), "w", encoding="utf-8").write(license_text)

source_lines = [
    "# AIQQ 源码包",
    "",
    "这是 AIQQ 的可公开源码版本，不包含真实 QQ 号、群号、token、密码、聊天上下文、图库或模型权重。",
    "",
    "## 环境",
    "",
    "- Windows 10/11",
    "- Python 3.10+",
    "- LM Studio（OpenAI 兼容本地服务）",
    "- NapCat / OneBot 11",
    "",
    "## 快速开始",
    "",
    "1. 复制 `init.example.txt` 为 `init.txt`，填写群号和管理员 QQ；",
    "2. 运行 `setup.bat` 或 `python setup.py`；",
    "3. 启动 NapCat 并登录 QQ；",
    "4. 双击 `start_panel.bat` 或运行 `python panel.py`；",
    "5. 在控制面板配置 LM Studio 模型和 OneBot。",
    "",
    "## 语音",
    "",
    "- 默认 Edge TTS，`requirements-voice.txt` 是可选依赖；",
    "- Qwen3-TTS 需要单独安装 `qwentts.cpp`、Vulkan SDK 和模型权重；",
    "- 参考 `tools/start_qwen_tts.example.cmd`；",
    "- 模型权重、`qwentts.cpp`、LM Studio 模型不包含在压缩包内。",
    "",
    "## 不包含",
    "",
    "`config.json`、`init.txt`、`panel_settings.json`、真实人格、上下文、日志、图库、`.voice_deps`、模型权重、NapCat 二进制和构建产物。",
    "",
    "## 许可证",
    "",
    "MIT，详见 `LICENSE`。",
    "",
]
open(os.path.join(STAGE, "SOURCE.md"), "w", encoding="utf-8").write("\n".join(source_lines))

out = os.path.join(ROOT, "dist", "AIQQ-v1.1.5-full-source.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for dirpath, _dirs, names in os.walk(STAGE):
        for name in names:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, STAGE)
            archive.write(full, os.path.join("AIQQ-src", rel))
print("ZIP", out, os.path.getsize(out))
print("FILES", sum(len(names) for _d, _s, names in os.walk(STAGE)))
