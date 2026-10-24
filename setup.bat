@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
where python >nul 2>&1
if errorlevel 1 if not exist ".venv\Scripts\python.exe" set "PY=Windows-quickstart-kirara-ai-v3.2.0\WPy64-31320\python\python.exe"

echo 正在读取 init.txt 并生成配置...
"%PY%" setup.py %*
pause
