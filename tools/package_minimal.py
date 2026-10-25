import os
import re
import shutil
import zipfile

ROOT = r"D:\AIQQ"
STAGE = os.path.join(ROOT, "dist", "AIQQ-minimal")
if os.path.isdir(STAGE):
    shutil.rmtree(STAGE)
os.makedirs(STAGE, exist_ok=True)

FILES = [
    "bot.py", "panel.py", "panel.html", "setup.py", "prepare_napcat.py",
    "setup.bat", "start_all.bat", "start_bot.bat",
    "start_panel.bat", "start_lmstudio.bat", "start_napcat.bat",
    "start_hidden.vbs", "init.example.txt", ".gitignore", "LICENSE",
    "bootstrap.py",
]
for name in FILES:
    path = os.path.join(ROOT, name)
    if os.path.isfile(path):
        shutil.copy2(path, os.path.join(STAGE, name))

shutil.copytree(
    os.path.join(ROOT, "aiqq"),
    os.path.join(STAGE, "aiqq"),
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
)

# 最小人格：使用现有空模板
shutil.copy2(os.path.join(ROOT, "persona", "空模板.txt"),
             os.path.join(STAGE, "persona.txt"))

REPLACEMENTS = {
    "100000001": "100000001",
    "200000001": "200000001",
    "100000002": "100000002",
    "8888": "change-me",
}


def clean_text(path):
    text = open(path, encoding="utf-8").read()
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    return text


def write_text(path, text):
    open(path, "w", encoding="utf-8", newline="\n").write(text)


# 清理默认管理员，避免把占位 ID 当成真实管理员
bot_path = os.path.join(STAGE, "bot.py")
text = clean_text(bot_path)
text = text.replace('"admin_users": [100000001]', '"admin_users": []')
text = text.replace('"admin_groups": [200000001]', '"admin_groups": []')
text = text.replace('"enabled": True,\n        "stt_enabled": True,\n        "tts_enabled": True,',
                    '"enabled": False,\n        "stt_enabled": False,\n        "tts_enabled": False,')
text = text.replace('"qwen_tts_enabled": True,', '"qwen_tts_enabled": False,')
write_text(bot_path, text)

for dirpath, _dirs, names in os.walk(os.path.join(STAGE, "aiqq")):
    for name in names:
        if name.endswith(".py"):
            path = os.path.join(dirpath, name)
            write_text(path, clean_text(path))
config_path = os.path.join(STAGE, "aiqq", "config.py")
if os.path.isfile(config_path):
    text = open(config_path, encoding="utf-8").read()
    text = text.replace('"admin_users": [100000001]', '"admin_users": []')
    text = text.replace('"admin_groups": [200000001]', '"admin_groups": []')
    write_text(config_path, text)

setup_path = os.path.join(STAGE, "setup.py")
text = clean_text(setup_path)
text = text.replace("admin_users=100000001", "admin_users=")
text = text.replace("admin_groups=200000001", "admin_groups=")
write_text(setup_path, text)

# 最小发行包关闭语音/Qwen，避免缺少运行时依赖
init_path = os.path.join(STAGE, "init.example.txt")
text = clean_text(init_path)
text = re.sub(r"(?m)^voice_enabled=.*$", "voice_enabled=false", text)
text = re.sub(r"(?m)^voice_stt_enabled=.*$", "voice_stt_enabled=false", text)
text = re.sub(r"(?m)^voice_tts_enabled=.*$", "voice_tts_enabled=false", text)
# 去掉 Qwen/Edge 语音专用配置，减少非必要项
text = re.sub(r"(?m)^voice_(stt_model|language|tts_voice|tts_rate|tts_volume|tts_pitch|qwen_enabled|qwen_url|qwen_model|qwen_language|qwen_timeout|max_bytes|max_chars)=.*\n?", "", text)
write_text(init_path, text)


# 最小启动脚本不再探测 Qwen TTS
start_all = os.path.join(STAGE, "start_all.bat")
text = clean_text(start_all)
text = re.sub(r"\nif exist \"%~dp0qwentts\.cpp\\build-vulkan\\tts-server\.exe\" \(\n.*?\n\)\n?",
              "\n", text, flags=re.S)
write_text(start_all, text)

# 一键启动：首次运行自动生成 init.txt、下载 NapCat、生成 config.json
start_bat = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if exist ".venv\\Scripts\\python.exe" set "PY=.venv\\Scripts\\python.exe"
where python >nul 2>&1
if errorlevel 1 if not exist ".venv\\Scripts\\python.exe" (
    echo [ERROR] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)
if not exist "init.txt" goto bootstrap
if not exist "NapCat.Shell\\launcher.bat" goto bootstrap
if not exist "config.json" goto setup
goto run

:bootstrap
"%PY%" bootstrap.py
if errorlevel 1 (
    pause
    exit /b 1
)
goto run

:setup
"%PY%" setup.py --if-needed
if errorlevel 1 (
    pause
    exit /b 1
)

:run
echo [AIQQ] Starting LM Studio...
call "%~dp0start_lmstudio.bat"
timeout /t 15 /nobreak >nul
echo [AIQQ] Starting NapCat...
call "%~dp0start_napcat.bat"
timeout /t 20 /nobreak >nul
echo [AIQQ] Starting bot...
call "%~dp0start_bot.bat"
echo [AIQQ] Starting panel...
call "%~dp0start_panel.bat"
echo [AIQQ] Started. Panel: http://127.0.0.1:8090
exit /b 0
"""
write_text(os.path.join(STAGE, "start.bat"), start_bat)

# 最小说明
readme = """# AIQQ 最小一键运行包

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
"""
write_text(os.path.join(STAGE, "README.md"), readme)

# 确保启动脚本包含 prepare 依赖；最小包不包含测试和语音工具
out = os.path.join(ROOT, "dist", "AIQQ-minimal-v1.0.0-source.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for dirpath, _dirs, names in os.walk(STAGE):
        for name in names:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, STAGE)
            archive.write(full, os.path.join("AIQQ-minimal", rel))
print("ZIP", out, os.path.getsize(out))
print("FILES", sum(len(names) for _d, _s, names in os.walk(STAGE)))
