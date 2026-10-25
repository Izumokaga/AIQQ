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
set "PYW=%PY:python.exe=pythonw.exe%"
if /I "%PY%"=="python" set "PYW=pythonw"
if /I not "%PY%"=="python" if not exist "%PYW%" set "PYW=%PY%"
if not exist "logs" mkdir "logs"
start "" "%PYW%" panel.py %* >> "logs\panel_console.log" 2>&1
exit /b 0
