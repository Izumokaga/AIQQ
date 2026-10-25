@echo off
setlocal
call "D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
set "PATH=D:\AIQQ\tools\build\cmake\data\bin;D:\AIQQ\tools\build\bin;%PATH%"
set "VULKAN_SDK=C:\VulkanSDK\1.4.363.0"
set "PATH=%VULKAN_SDK%\Bin;%PATH%"
cd /d D:\AIQQ\qwentts.cpp
if exist build-vulkan rmdir /s /q build-vulkan
cmake -S . -B build-vulkan -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON -DCMAKE_PREFIX_PATH=%VULKAN_SDK% > D:\AIQQ\tools\qwentts_vulkan.log 2>&1
if errorlevel 1 exit /b 1
cmake --build build-vulkan --config Release -j 8 >> D:\AIQQ\tools\qwentts_vulkan.log 2>&1
exit /b %ERRORLEVEL%
