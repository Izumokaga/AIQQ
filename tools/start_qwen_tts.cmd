@echo off
cd /d D:\AIQQ\qwentts.cpp\build-vulkan
start "" /b "D:\AIQQ\qwentts.cpp\build-vulkan\tts-server.exe" --model "D:\AIQQ\qwen3_tts_serveur\qwen-talker-1.7b-voicedesign-Q4_K_M.gguf" --codec "D:\AIQQ\qwen3_tts_serveur\qwen-tokenizer-12hz-Q8_0.gguf" --device Vulkan0 --host 127.0.0.1 --port 8190 --alias qwen3-tts-voicedesign >> "D:\AIQQ\logs\qwen_tts_server.log" 2>&1
exit /b 0
