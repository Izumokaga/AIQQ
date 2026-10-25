import os
import sys
import time

sys.path.insert(0, r"D:\AIQQ\.voice_deps")
import requests
import urllib3

urllib3.disable_warnings()
PROXY = {"http": "http://127.0.0.1:10808", "https": "http://127.0.0.1:10808"}
BASE = "https://hf-mirror.com/Serveurperso/Qwen3-TTS-GGUF/resolve/main"
TARGET = r"D:\AIQQ\qwen3_tts_serveur"
FILES = [
    "qwen-talker-1.7b-voicedesign-Q4_K_M.gguf",
    "qwen-tokenizer-12hz-Q4_K_M.gguf",
]
CHUNK = 8 * 1024 * 1024


def download(name):
    url = BASE + "/" + name
    path = os.path.join(TARGET, name)
    os.makedirs(TARGET, exist_ok=True)
    for attempt in range(30):
        have = os.path.getsize(path) if os.path.exists(path) else 0
        headers = {"Range": "bytes=%d-" % have} if have else {}
        try:
            with requests.get(url, headers=headers, proxies=PROXY, timeout=60,
                              verify=False, stream=True) as response:
                if response.status_code == 416:
                    print("DONE", name, have, flush=True)
                    return
                if response.status_code == 206:
                    mode = "ab"
                elif response.status_code == 200:
                    mode = "wb"
                    have = 0
                else:
                    raise RuntimeError("HTTP %s" % response.status_code)
                total = have
                with open(path, mode) as fh:
                    for data in response.iter_content(CHUNK):
                        if not data:
                            continue
                        fh.write(data)
                        total += len(data)
                        if total // CHUNK != (total - len(data)) // CHUNK:
                            print("PROGRESS", name, total, flush=True)
                print("DONE", name, total, flush=True)
                return
        except Exception as exc:
            print("RETRY", name, attempt, type(exc).__name__, exc, flush=True)
            time.sleep(3)
    raise SystemExit("failed: " + name)


for filename in FILES:
    download(filename)
print("ALL DONE", flush=True)
