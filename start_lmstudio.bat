@echo off
if defined AIQQ_HIDDEN goto :aiqq_main
set "AIQQ_HIDDEN=1"
wscript //nologo "%~dp0start_hidden.vbs" "%~f0" %*
exit /b 0
:aiqq_main
setlocal
set "LMSTUDIO_EXE="
if exist "D:\LM Studio\LM Studio.exe" set "LMSTUDIO_EXE=D:\LM Studio\LM Studio.exe"
if not defined LMSTUDIO_EXE if exist "%LOCALAPPDATA%\Programs\LM Studio\LM Studio.exe" set "LMSTUDIO_EXE=%LOCALAPPDATA%\Programs\LM Studio\LM Studio.exe"
if not defined LMSTUDIO_EXE if exist "%PROGRAMFILES%\LM Studio\LM Studio.exe" set "LMSTUDIO_EXE=%PROGRAMFILES%\LM Studio\LM Studio.exe"
if not defined LMSTUDIO_EXE if exist "%PROGRAMFILES(X86)%\LM Studio\LM Studio.exe" set "LMSTUDIO_EXE=%PROGRAMFILES(X86)%\LM Studio\LM Studio.exe"

tasklist /FI "IMAGENAME eq LM Studio.exe" /NH 2>nul | find /I "LM Studio.exe" >nul
if errorlevel 1 (
    if defined LMSTUDIO_EXE (
        echo [LM Studio] starting...
        start "" "%LMSTUDIO_EXE%"
        ping -n 8 127.0.0.1 >nul
    ) else (
        echo [LM Studio] executable not found, please start it manually.
    )
) else (
    echo [LM Studio] already running.
)

set "LMS_CLI=%USERPROFILE%\.lmstudio\bin\lms.exe"
if exist "%LMS_CLI%" (
    "%LMS_CLI%" server start >nul 2>&1
) else (
    where lms >nul 2>&1
    if not errorlevel 1 lms server start >nul 2>&1
)
exit /b 0
