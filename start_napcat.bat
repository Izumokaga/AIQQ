@echo off
if defined AIQQ_HIDDEN goto :aiqq_main
set "AIQQ_HIDDEN=1"
wscript //nologo "%~dp0start_hidden.vbs" "%~f0" %*
exit /b 0
:aiqq_main
chcp 65001 >nul
cd /d "%~dp0NapCat.Shell"
if not exist "..\config.json" (
    echo [提示] 尚未生成 config.json：请先编辑 init.txt，再双击 setup.bat。
    pause
    exit /b 1
)
if not exist "launcher.bat" (
    echo [错误] 未找到 NapCat.Shell\launcher.bat
    echo 请先把 NapCat 解压到当前目录的 NapCat.Shell 文件夹。
    pause
    exit /b 1
)
set "PY=python"
if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"
"%PY%" "..\prepare_napcat.py"

echo 正在启动 NapCat（会自动请求管理员权限）...
call launcher.bat %*
