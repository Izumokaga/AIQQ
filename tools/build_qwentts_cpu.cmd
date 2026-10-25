@echo off
setlocal
call "D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
set "PATH=D:\AIQQ\tools\build\cmake\data\bin;D:\AIQQ\tools\build\bin;%PATH%"
cd /d D:\AIQQ\qwentts.cpp
cmake -S . -B build-cpu -G "Visual Studio 17 2022" -A x64 -DGGML_VULKAN=OFF > D:\AIQQ\tools\qwentts_cmake.log 2>&1
if errorlevel 1 exit /b 1
cmake --build build-cpu --config Release -j 8 > D:\AIQQ\tools\qwentts_build.log 2>&1
exit /b %ERRORLEVEL%
