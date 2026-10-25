' 启动 Qwen3-TTS Vulkan 服务，无 CMD 窗口。
Set sh = CreateObject("WScript.Shell")
base = "D:\AIQQ\qwentts.cpp\build-vulkan"
sh.CurrentDirectory = base
cmd = "cmd.exe /c """"" & base & "\tts-server.exe"" --model ""D:\AIQQ\qwen3_tts_serveur\qwen-talker-1.7b-voicedesign-Q4_K_M.gguf"" --codec ""D:\AIQQ\qwen3_tts_serveur\qwen-tokenizer-12hz-Q4_K_M.gguf"" --device Vulkan0 --host 127.0.0.1 --port 8190 --alias qwen3-tts-voicedesign >> ""D:\AIQQ\logs\qwen_tts_server.log"" 2>&1"""
sh.Run cmd, 0, False
