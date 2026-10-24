@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
where python >nul 2>&1
if errorlevel 1 if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)
if not exist "init.txt" goto bootstrap
if not exist "NapCat.Shell\launcher.bat" goto bootstrap
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
