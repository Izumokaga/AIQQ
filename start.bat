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

set "START_NAPCAT=1"
if /I "%~1"=="/no-napcat" set "START_NAPCAT=0"
if /I "%~1"=="-n" set "START_NAPCAT=0"

echo [LM Studio] Starting and checking local server...
call "%~dp0start_lmstudio.bat"
ping -n 16 127.0.0.1 >nul

echo ============================================================
echo  AIQQ one-click start
echo ============================================================

if not exist "init.txt" (
    echo [1/3] init.txt not found, generating a template...
    "%PY%" setup.py
    echo.
    echo Edit init.txt (at least qq and groups), then run start.bat again.
    pause
    exit /b 1
)

echo [1/3] Applying init.txt...
"%PY%" setup.py --if-needed
if errorlevel 1 (
    echo.
    echo [ERROR] Initialization failed. Check init.txt.
    echo         If writing NapCat config failed, exit NapCat/QQ completely and retry.
    pause
    exit /b 1
)
if not exist "config.json" (
    echo [ERROR] config.json was not generated.
    pause
    exit /b 1
)

if "%START_NAPCAT%"=="0" goto start_bot

echo [2/3] Preparing and checking NapCat...
"%PY%" prepare_napcat.py

del "%TEMP%\aiqq_onebot_port.txt" >nul 2>&1
del "%TEMP%\aiqq_webui_port.txt" >nul 2>&1
set "ONEBOT_PORT=3000"
set "WEBUI_PORT=6099"
"%PY%" -c "import json;print(json.load(open('config.json',encoding='utf-8'))['onebot']['api_url'].rsplit(':',1)[-1].split('/')[0])" > "%TEMP%\aiqq_onebot_port.txt" 2>nul
if exist "%TEMP%\aiqq_onebot_port.txt" set /p ONEBOT_PORT=<"%TEMP%\aiqq_onebot_port.txt"
"%PY%" -c "import json;print(json.load(open(r'NapCat.Shell\config\webui.json',encoding='utf-8')).get('port',6099))" > "%TEMP%\aiqq_webui_port.txt" 2>nul
if exist "%TEMP%\aiqq_webui_port.txt" set /p WEBUI_PORT=<"%TEMP%\aiqq_webui_port.txt"
if not defined ONEBOT_PORT set "ONEBOT_PORT=3000"
if not defined WEBUI_PORT set "WEBUI_PORT=6099"

echo        Checking NapCat OneBot port %ONEBOT_PORT%...
netstat -ano | findstr /C:":%ONEBOT_PORT% " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
    echo        NapCat is ready.
    goto start_bot
)

netstat -ano | findstr /C:":%WEBUI_PORT% " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
    echo        [NOTE] NapCat is running but OneBot is not ready.
    echo               If config changed, exit NapCat/QQ and run start.bat again.
    goto start_bot
)

if not exist "NapCat.Shell\launcher.bat" (
    echo        [NOTE] NapCat.Shell\launcher.bat not found, skipping NapCat.
    goto start_bot
)

echo        NapCat not detected, starting...
echo        First run: exit QQ completely, then scan the QR code.
ping -n 4 127.0.0.1 >nul
start "" /b /d "%~dp0NapCat.Shell" cmd /c launcher.bat
echo        Waiting 15 seconds for NapCat / QQ...
ping -n 16 127.0.0.1 >nul

:start_bot
echo [3/3] Starting bot...
call "%~dp0start_bot.bat"
exit /b 0
