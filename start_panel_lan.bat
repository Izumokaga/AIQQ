@echo off
if defined AIQQ_HIDDEN goto :aiqq_main
set "AIQQ_HIDDEN=1"
wscript //nologo "%~dp0start_hidden.vbs" "%~f0" %*
exit /b 0
:aiqq_main
chcp 65001 >nul
cd /d "%~dp0"
echo Open the panel, then use Panel Settings to enable LAN access.
call "%~dp0start_panel.bat" %*
