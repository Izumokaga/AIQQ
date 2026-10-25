@echo off
setlocal
call "D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
set "PATH=D:\AIQQ\tools\build\cmake\data\bin;D:\AIQQ\tools\build\bin;%PATH%"
cd /d D:\AIQQ\qwentts.cpp
if exist build-nmake rmdir /s /q build-nmake
cmake -S . -B build-nmake -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=OFF > D:\AIQQ\tools\qwentts_nmake.log 2>&1
if errorlevel 1 exit /b 1
cmake --build build-nmake --config Release -j 8 >> D:\AIQQ\tools\qwentts_nmake.log 2>&1
exit /b %ERRORLEVEL%
