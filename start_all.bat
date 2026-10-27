@echo off
if defined AIQQ_HIDDEN goto :aiqq_main
set "AIQQ_HIDDEN=1"
wscript //nologo "%~dp0start_hidden.vbs" "%~f0" %*
exit /b 0
:aiqq_main
chcp 65001 >nul
cd /d "%~dp0"
if not exist "config.json" (
    echo [AIQQ] config.json not found. Edit init.txt and run setup.bat first.
    pause
    exit /b 1
)
echo [AIQQ] Starting LM Studio...
call "%~dp0start_lmstudio.bat"
echo [AIQQ] Waiting for LM Studio local server...
timeout /t 15 /nobreak >nul
echo [AIQQ] Starting NapCat / QQ...
start "" /b /d "%~dp0" cmd /c start_napcat.bat
echo [AIQQ] Waiting 20 seconds for QQ and NapCat...
timeout /t 20 /nobreak >nul
echo [AIQQ] Starting bot...
call start_bot.bat
if exist "%~dp0qwentts.cpp\build-vulkan\tts-server.exe" (
    echo [AIQQ] Starting Qwen3-TTS server...
    call "%~dp0tools\start_qwen_tts.cmd"
)
