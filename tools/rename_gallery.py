#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 DSH/LM Studio 从图库 description 提取要素并重命名图片。

用法：
    python tools/rename_gallery.py --dry-run
    python tools/rename_gallery.py --apply
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aiqq.config import load_config  # noqa: E402
from aiqq.images import sanitize_image_name  # noqa: E402


def _request(url, payload, timeout=180):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _model(config):
    model = str((config.get("lm_studio") or {}).get("model") or "").strip()
    if model:
        return model
    base = str((config.get("lm_studio") or {}).get("base_url") or "http://127.0.0.1:1234/v1")
    root = base.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        with urllib.request.urlopen(root + "/api/v1/models", timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        for item in data.get("models") or []:
            if not isinstance(item, dict) or not item.get("loaded_instances"):
                continue
            name = str(item.get("id") or "")
            lowered = name.lower()
            if name and not any(token in lowered for token in ("tts", "embed", "rerank")):
                return name
    except Exception:
        pass
    return ""


def _extract_names(config, entries):
    base = str((config.get("lm_studio") or {}).get("base_url") or "http://127.0.0.1:1234/v1").rstrip("/")
    model = _model(config)
    if not model:
        raise RuntimeError("没有可用聊天模型")
    numbered = [
        {"id": index + 1, "description": str(item.get("description") or "")}
        for index, item in enumerate(entries)
    ]
    prompt = (
        "根据每张图库图片的描述提取主体、动作或情绪，生成简短中文名字。"
        "名字 4~16 个汉字，不含标点、扩展名、序号；只输出 JSON 对象，"
        "键为 id 字符串，值为名字。不要解释。\n"
        + json.dumps(numbered, ensure_ascii=False)
    )
    data = _request(base + "/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 2048,
        "stream": False,
    })
    text = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise RuntimeError("模型没有返回 JSON：%s" % text[:200])
    raw = json.loads(match.group(0))
    result = {}
    for index, item in enumerate(entries):
        name = str(raw.get(str(index + 1)) or raw.get(index + 1) or "").strip()
        if name:
            result[index] = name
    return result


def _fallback_name(entry):
    description = str(entry.get("description") or "").strip()
    compact = re.sub(r"[\s，。！？!?、,.…~～:：；;（）()【】\[\]]+", "", description)
    return compact[:12] or str(entry.get("name") or "图片")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=ROOT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch", type=int, default=8)
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    image_dir = os.path.join(root, "img")
    index_path = os.path.join(image_dir, "index.json")
    if not os.path.isfile(index_path):
        raise SystemExit("找不到图库索引：" + index_path)
    with open(index_path, encoding="utf-8") as fh:
        entries = json.load(fh)
    if not isinstance(entries, list):
        raise SystemExit("图库索引不是列表")
    config = load_config(os.path.join(root, "config.json"))
    mapping = {}
    batch_size = max(1, min(20, args.batch))
    for start in range(0, len(entries), batch_size):
        batch = entries[start:start + batch_size]
        try:
            names = _extract_names(config, batch)
        except Exception as exc:
            print("[警告] 批次 %d 提取失败：%s" % (start // batch_size + 1, exc))
            names = {}
        for offset, entry in enumerate(batch):
            old = str(entry.get("name") or "").strip()
            proposed = names.get(offset) or _fallback_name(entry)
            proposed = sanitize_image_name(proposed, old or "图片")[:16].strip(" ._-")
            if proposed:
                mapping[start + offset] = proposed
        time.sleep(0.5)

    # 先显示计划
    for index, entry in enumerate(entries):
        proposed = mapping.get(index)
        if proposed and proposed != entry.get("name"):
            print("%s -> %s (%s)" % (entry.get("name"), proposed, entry.get("file")))
    if not args.apply:
        print("共 %d 张，仅预览。使用 --apply 执行重命名。" % len(entries))
        return

    backup = os.path.join(root, "backups", "gallery_rename_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    shutil.copy2(index_path, backup)
    used = set()
    for entry in entries:
        filename = str(entry.get("file") or "")
        used.add(filename.casefold())
    for index, entry in enumerate(entries):
        proposed = mapping.get(index)
        old_file = str(entry.get("file") or "")
        if not proposed or not old_file:
            continue
        extension = os.path.splitext(old_file)[1] or ".png"
        base = proposed
        candidate = base + extension
        suffix = 2
        while candidate.casefold() in used and candidate.casefold() != old_file.casefold():
            candidate = "%s_%d%s" % (base, suffix, extension)
            suffix += 1
        old_path = os.path.join(image_dir, os.path.basename(old_file))
        new_path = os.path.join(image_dir, os.path.basename(candidate))
        if old_path == new_path or not os.path.isfile(old_path):
            continue
        if os.path.exists(new_path):
            continue
        os.rename(old_path, new_path)
        used.discard(old_file.casefold())
        used.add(candidate.casefold())
        entry["name"] = proposed
        entry["file"] = candidate
    tmp = index_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, index_path)
    print("完成 %d 张图库重命名；备份：%s" % (len(entries), backup))


if __name__ == "__main__":
    main()
