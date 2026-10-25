@echo off
if defined AIQQ_HIDDEN goto :aiqq_main
set "AIQQ_HIDDEN=1"
wscript //nologo "%~dp0start_hidden.vbs" "%~f0" %*
exit /b 0
:aiqq_main
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
where python >nul 2>&1
if errorlevel 1 if not exist ".venv\Scripts\python.exe" set "PY=Windows-quickstart-kirara-ai-v3.2.0\WPy64-31320\python\python.exe"

if not exist "config.json" (
    echo [提示] 未找到 config.json，正在读取 init.txt 生成配置...
    if not exist "init.txt" echo        未找到 init.txt，将先生成模板。
    "%PY%" setup.py
    if not exist "config.json" (
        echo [错误] 初始化未完成。请编辑 init.txt（至少填写 qq 和 groups）后重试。
        pause
        exit /b 1
    )
)

title AIQQ Bot
set "PYW=%PY:python.exe=pythonw.exe%"
if /I "%PY%"=="python" set "PYW=pythonw"
if /I not "%PY%"=="python" if not exist "%PYW%" set "PYW=%PY%"
if not exist "logs" mkdir "logs"
start "" "%PYW%" bot.py %* >> "logs\bot_console.log" 2>&1
exit /b 0
