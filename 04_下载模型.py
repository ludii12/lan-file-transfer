# -*- coding: utf-8 -*-
"""下载语音识别模型（约 300MB），下到 模型/ 目录。

    python 04_下载模型.py

包含两个模型：
  1. sherpa-onnx-paraformer-zh-2023-09-14        中文识别
  2. sherpa-onnx-punct-ct-transformer-...-int8   自动补标点

GitHub 直连失败时会自动改用 gh-proxy 镜像。
"""

import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote

APP_DIR = Path(__file__).resolve().parent
MODEL_DIR = APP_DIR / "模型"

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
MIRROR = "https://gh-proxy.com/"

MODELS = [
    (f"{BASE}/asr-models/sherpa-onnx-paraformer-zh-2023-09-14.tar.bz2",
     "sherpa-onnx-paraformer-zh-2023-09-14", "中文识别模型", 234),
    (f"{BASE}/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2",
     "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8", "标点模型", 62),
]


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024


def download(url, dest):
    for candidate in (url, MIRROR + url):
        try:
            request = urllib.request.Request(candidate, headers={"User-Agent": "lan-transfer"})
            with urllib.request.urlopen(request, timeout=30) as response:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                started = time.time()
                with dest.open("wb") as fh:
                    while True:
                        block = response.read(256 * 1024)
                        if not block:
                            break
                        fh.write(block)
                        done += len(block)
                        if total:
                            pct = done / total * 100
                            speed = done / max(time.time() - started, 0.001)
                            print(f"\r  {pct:5.1f}%  {human(done)} / {human(total)}  "
                                  f"{human(speed)}/s   ", end="")
                print()
            return True
        except Exception as exc:
            print(f"  失败（{type(exc).__name__}）：{candidate[:60]}…")
            dest.unlink(missing_ok=True)
    return False


def main():
    MODEL_DIR.mkdir(exist_ok=True)
    for url, folder, label, approx in MODELS:
        target = MODEL_DIR / folder
        if target.exists() and any(target.glob("*.onnx")):
            print(f"[跳过] {label} 已存在：{target}")
            continue
        print(f"[下载] {label}（约 {approx}MB）")
        archive = MODEL_DIR / (unquote(url.rsplit("/", 1)[-1]))
        if not download(url, archive):
            print(f"[失败] {label} 没下下来，检查网络后重跑本脚本。")
            return 1
        print(f"[解压] {archive.name}")
        with tarfile.open(archive, "r:bz2") as tar:
            try:
                tar.extractall(MODEL_DIR, filter="data")
            except TypeError:      # Python < 3.12 不支持 filter 参数
                tar.extractall(MODEL_DIR)
        archive.unlink(missing_ok=True)
        print(f"[完成] {target}  {human(sum(f.stat().st_size for f in target.rglob('*') if f.is_file()))}")
    print("\n模型齐了，可以运行 python 01_启动.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
