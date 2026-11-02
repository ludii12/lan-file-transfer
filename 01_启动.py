# -*- coding: utf-8 -*-
"""局域网文件传输：电脑 GUI + 手机浏览器上传/下载"""

import gc
import json
import ctypes
import mimetypes
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
import winreg
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue, Empty
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import qrcode
    from PIL import Image, ImageTk
    QR_ENABLED = True
except Exception:
    QR_ENABLED = False

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

WEB_FILE = APP_DIR / "web" / "index.html"
CONFIG_FILE = APP_DIR / "config.json"
TEXT_FILE = APP_DIR / "文字记录.json"
MODEL_DIR = APP_DIR / "模型"
CERT_DIR = APP_DIR / "证书"
DEFAULT_SHARE = APP_DIR / "分享文件"

CERT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>装证书</title>
<style>
body{margin:0;background:#f4f5f2;color:#1d1d1d;
font:16px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:640px;margin:0 auto;padding:24px 18px 60px}
h1{font-size:20px;margin:0 0 6px}
p{color:#555;margin:8px 0}
.card{background:#fff;border:1px solid #e3e5e1;border-radius:14px;padding:14px 18px;margin-top:14px}
h2{font-size:15px;margin:0 0 6px}
ol{margin:6px 0 0;padding-left:22px}
li{margin:6px 0}
a.btn{display:block;text-align:center;background:#2f6f4f;color:#fff;text-decoration:none;
border-radius:11px;padding:14px;font-size:16px;margin-top:16px}
code{background:#eef2ee;padding:1px 6px;border-radius:6px;font-size:14px;word-break:break-all}
.small{font-size:13px;color:#8a8f88}
</style></head><body><div class="wrap">
<h1>装一次证书，以后不再提示不安全</h1>
<p class="small">装好后用 <code>https://__IP__:__SECURE_PORT__/</code> 打开：地址栏不再报警，
语音也能在网页里按住说话。</p>
<a class="btn" href="/cert.crt">下载证书</a>
<div class="card"><h2>安卓</h2><ol>
<li>先点上面「下载证书」</li>
<li>设置 → 安全（或「密码与安全」）→ 加密与凭据 → 安装证书 → CA 证书</li>
<li>提示风险时点「仍然安装」，选中刚下载的文件</li>
<li>用 <code>https://__IP__:__SECURE_PORT__/</code> 打开</li>
</ol></div>
<div class="card"><h2>iPhone</h2><ol>
<li>下载后进 设置，点最上面的「已下载描述文件」→ 安装</li>
<li>设置 → 通用 → 关于本机 → 证书信任设置 → 打开对应的开关</li>
<li>Safari 打开 <code>https://__IP__:__SECURE_PORT__/</code></li>
</ol></div>
<div class="card"><h2>装好了吗？</h2>
<p>点下面按钮测一下，能通就说明装好了：<span id="r" style="color:#2f6f4f"></span></p>
<button class="btn" id="test" type="button" style="border:0;cursor:pointer">测试安全连接</button>
</div>
<script>
document.querySelector('#test').onclick = async function () {
  var el = document.getElementById('r');
  el.style.color = '#8a8f88';
  el.textContent = '测试中…';
  try {
    await fetch('https://__IP__:__SECURE_PORT__/favicon.ico', {mode: 'no-cors', cache: 'no-store'});
    el.style.color = '#2f6f4f';
    el.textContent = '通了！以后用 https 打开就不会再提示。';
  } catch (e) {
    el.style.color = '#c0453a';
    el.textContent = '还没通：证书没装成功，或装完没重启浏览器。';
  }
};
</script>
<div class="card"><h2>不想装证书？</h2>
<p>继续用 <code>http://__IP__:__HTTP_PORT__/</code>。地址栏那个「不安全」只是说没加密，
同一局域网里传文件不受影响；只是网页内录音用不了，点麦克风会打开系统录音机。</p>
</div>
</div></body></html>"""

CHUNK = 256 * 1024
MAX_NAME_LEN = 150
BAD_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"}


def safe_name(raw):
    if not raw:
        return None
    name = urllib.parse.unquote(raw).replace("\\", "/").split("/")[-1].strip()
    name = BAD_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name or name in (".", ".."):
        return None
    return name[:MAX_NAME_LEN]


def unique_path(folder, name):
    target = folder / name
    if not target.exists():
        return target
    stem, ext = os.path.splitext(name)
    i = 1
    while target.exists():
        target = folder / f"{stem} ({i}){ext}"
        i += 1
    return target


def inside(folder, path):
    try:
        return os.path.commonpath([str(folder), str(path)]) == str(folder)
    except ValueError:
        return False


def human(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0


def content_disposition(name, inline):
    fallback = name.encode("ascii", "ignore").decode()
    stem, ext = os.path.splitext(fallback)
    if not stem.strip(" ."):
        fallback = "download" + ext if ext.strip(" .") else "download"
    fallback = fallback.replace('"', "'").replace("\\", "/").split("/")[-1]
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{urllib.parse.quote(name)}"


def _ip_score(ip):
    """越小越像真正的 WiFi/有线局域网地址，Docker/WSL 虚拟网卡排后面。"""
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
            return 2 if 16 <= second <= 31 else 9
        except ValueError:
            return 9
    if ip.startswith("169.254.") or ip.startswith("127."):
        return 9
    return 5


def lan_ip():
    found = []
    for target in ("192.168.1.1", "192.168.0.1", "192.168.31.1", "10.0.0.1", "8.8.8.8"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target, 80))
            ip = sock.getsockname()[0]
        except OSError:
            continue
        finally:
            sock.close()
        if ip and _ip_score(ip) < 9 and ip not in found:
            found.append(ip)
    if not found:
        return "127.0.0.1"
    return min(found, key=_ip_score)


class TextStore:
    """两端共用的文字列表，落盘到 文字记录.json。"""

    def __init__(self, path, limit=100):
        self.path = Path(path)
        self.limit = limit
        self.lock = threading.Lock()
        self.items = []
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if isinstance(data, list):
                self.items = [x for x in data if isinstance(x, dict) and x.get("text")][:limit]
                for item in self.items:
                    item.setdefault("kind", "text")
        except Exception:
            self.items = []

    def _save(self):
        try:
            self.path.write_text(json.dumps(self.items[:self.limit], ensure_ascii=False, indent=1),
                                 "utf-8")
        except OSError:
            pass

    def listing(self):
        with self.lock:
            return [dict(x) for x in self.items]

    def add(self, text, source="", kind="text"):
        text = (text or "").replace("\r\n", "\n").strip()
        if not text:
            raise ValueError("内容是空的")
        if len(text) > 20000:
            raise ValueError("太长了，一次最多 2 万字")
        if kind not in ("text", "voice"):
            kind = "text"
        item = {"id": f"{time.time():.6f}", "text": text, "time": time.time(),
                "from": source, "kind": kind}
        with self.lock:
            self.items.insert(0, item)
            voices = [x for x in self.items if x.get("kind") == "voice"]
            if len(voices) > 30:
                keep = {id(x) for x in voices[:30]}
                self.items = [x for x in self.items
                              if x.get("kind") != "voice" or id(x) in keep]
            del self.items[self.limit:]
            self._save()
        return item

    def delete(self, item_id):
        with self.lock:
            before = len(self.items)
            self.items = [x for x in self.items if x.get("id") != item_id]
            self._save()
            return len(self.items) < before


_TEXT_STORE = None


def get_texts():
    global _TEXT_STORE
    if _TEXT_STORE is None:
        _TEXT_STORE = TextStore(TEXT_FILE)
    return _TEXT_STORE


def find_openssl():
    found = shutil.which("openssl")
    if found:
        return found
    for guess in (r"C:\Program Files\Git\usr\bin\openssl.exe",
                  r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if Path(guess).exists():
            return guess
    return None


def ensure_cert(ip):
    """自签名证书；手机上装一次，之后就能用 https 打开、页面里直接录音。"""
    cert, key, mark = CERT_DIR / "lan.crt", CERT_DIR / "lan.key", CERT_DIR / "ip.txt"
    try:
        if cert.exists() and key.exists() and mark.read_text("utf-8").strip() == ip:
            return cert, key
    except OSError:
        pass
    openssl = find_openssl()
    if not openssl:
        return None, None
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "3650",
         "-subj", "/CN=LanTransfer", "-addext",
         f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost"],
        capture_output=True, text=True)
    if result.returncode != 0 or not cert.exists():
        return None, None
    mark.write_text(ip, "utf-8")
    return cert, key


_ASR = None
_ASR_LOCK = threading.Lock()
PARAFORMER_DIR = MODEL_DIR / "sherpa-onnx-paraformer-zh-2023-09-14"
PUNCT_DIR = MODEL_DIR / "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8"

_PUNCT = None
_PUNCT_LOCK = threading.Lock()
_ASR_LAST_USE = 0.0
ASR_IDLE_SECONDS = 600


def punctuate(text):
    """给识别结果补标点；模型不在就直接原样返回。"""
    global _PUNCT
    if not text or not (PUNCT_DIR / "model.int8.onnx").exists():
        return text
    with _PUNCT_LOCK:
        if _PUNCT is None:
            import sherpa_onnx
            _PUNCT = sherpa_onnx.OfflinePunctuation(
                sherpa_onnx.OfflinePunctuationConfig(
                    model=sherpa_onnx.OfflinePunctuationModelConfig(
                        ct_transformer=str(PUNCT_DIR / "model.int8.onnx"), num_threads=2)))
        try:
            return _PUNCT.add_punctuation(text) or text
        except Exception:
            return text


def asr_ready():
    return (PARAFORMER_DIR / "model.int8.onnx").exists() and (PARAFORMER_DIR / "tokens.txt").exists()


def get_asr():
    global _ASR, _ASR_LAST_USE
    with _ASR_LOCK:
        if _ASR is None:
            import sherpa_onnx
            _ASR = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=str(PARAFORMER_DIR / "model.int8.onnx"),
                tokens=str(PARAFORMER_DIR / "tokens.txt"),
                num_threads=4, sample_rate=16000, feature_dim=80,
                decoding_method="greedy_search")
            _ASR_LAST_USE = time.time()
    return _ASR


def load_16k(path):
    """任意格式（webm/m4a/wav）解成 16k 单声道 float32。"""
    import av
    import numpy as np
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        chunks = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def release_asr():
    """把模型从内存里放掉，腾出约 450MB。"""
    global _ASR, _PUNCT
    with _ASR_LOCK:
        _ASR = None
    with _PUNCT_LOCK:
        _PUNCT = None
    gc.collect()
    try:
        ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
    except Exception:
        pass


def asr_idle_watch(report):
    while True:
        time.sleep(5)
        if (_ASR is not None or _PUNCT is not None)                 and time.time() - _ASR_LAST_USE > ASR_IDLE_SECONDS:
            release_asr()
            report("语音模型闲置 10 分钟，已释放内存；下次说话会重新加载约 1.5 秒。")


def transcribe(path):
    global _ASR_LAST_USE
    _ASR_LAST_USE = time.time()
    samples = load_16k(path)
    if len(samples) < 1600:
        return ""
    recognizer = get_asr()
    with _ASR_LOCK:
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        recognizer.decode_stream(stream)
        text = (stream.result.text or "").strip()
    return punctuate(text)


def foreground_pid():
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def simulate_paste():
    """给当前前台窗口发一次 Ctrl+V。"""
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(0x11, 0, 0, 0)
        user32.keybd_event(0x56, 0, 0, 0)
        user32.keybd_event(0x56, 0, 2, 0)
        user32.keybd_event(0x11, 0, 2, 0)
    except Exception:
        pass


class TransferHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    @property
    def root(self):
        return self.server.root_path

    @property
    def log(self):
        return self.server.report

    def _send(self, code, body=b"", ctype="application/octet-stream", headers=None, length=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body) if length is None else length))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, code, message):
        try:
            self._json(code, {"ok": False, "error": message})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.log(f"读取失败: {exc}")
            self._fail(500, str(exc))

    def _route_get(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            try:
                html = WEB_FILE.read_bytes()
            except OSError:
                html = b"<h3>\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0 web/index.html</h3>"
            self._send(200, html, "text/html; charset=utf-8")
            return

        if path == "/favicon.ico":
            self._send(204)
            return

        if path == "/api/list":
            self._send(200, json.dumps(self._listing(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return

        if path == "/api/text":
            body = json.dumps({"items": self.server.texts.listing()}, ensure_ascii=False)
            self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")
            return

        if path == "/api/settings":
            body = json.dumps({"autopaste": SETTINGS.autopaste,
                               "model": _ASR is not None}, ensure_ascii=False)
            self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")
            return

        if path == "/cert":
            port = self.server.server_address[1]
            page = (CERT_PAGE.replace("__IP__", lan_ip())
                    .replace("__SECURE_PORT__", str(port + 1))
                    .replace("__HTTP_PORT__", str(port)))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/cert.crt":
            cert = CERT_DIR / "lan.crt"
            if cert.exists():
                self._send(200, cert.read_bytes(), "application/x-x509-ca-cert",
                           {"Content-Disposition": 'attachment; filename="LanTransfer.crt"'})
            else:
                self._fail(404, "证书还没生成")
            return

        if path.startswith("/f/"):
            self._download(urllib.parse.unquote(path[3:]),
                           query.get("inline", ["0"])[0] == "1")
            return

        self._fail(404, "not found")

    def _listing(self):
        root = self.root
        items = []
        if root.exists():
            for entry in root.iterdir():
                if entry.name.startswith(".") or entry.name.endswith(".part"):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                if not entry.is_file():
                    continue
                items.append({
                    "name": entry.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "image": entry.suffix.lower() in IMAGE_EXT,
                })
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return {"files": items, "root": str(root)}

    def _download(self, raw_name, inline):
        name = safe_name(raw_name)
        if not name:
            self._fail(400, "文件名不合法")
            return
        target = self.root / name
        if not inside(self.root, target) or not target.is_file():
            self._fail(404, "文件不存在")
            return

        size = target.stat().st_size
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", content_disposition(name, inline))
        self.end_headers()
        sent = 0
        with target.open("rb") as fh:
            while sent < size:
                block = fh.read(min(CHUNK, size - sent))
                if not block:
                    break
                self.wfile.write(block)
                sent += len(block)
        self.log(f"下载 {name}  {human(sent)}  ← {self.client_address[0]}")
        self.server.note_transfer("down", sent)

    def do_PUT(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/u":
            self._fail(404, "not found")
            return
        name = safe_name(urllib.parse.parse_qs(parsed.query).get("name", [""])[0])
        if not name:
            self._fail(400, "文件名不合法")
            return
        length = self.headers.get("Content-Length")
        if length is None:
            self._fail(411, "需要 Content-Length")
            return
        remaining = int(length)
        if remaining <= 0:
            self._fail(400, "空文件")
            return

        target = unique_path(self.root, name)
        tmp = target.with_name(target.name + ".part")
        started = time.time()
        received = 0
        try:
            with tmp.open("wb") as fh:
                while received < remaining:
                    block = self.rfile.read(min(CHUNK, remaining - received))
                    if not block:
                        break
                    fh.write(block)
                    received += len(block)
            if received != remaining:
                raise OSError("上传中断")
            os.replace(tmp, target)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            self.log(f"上传失败 {name}: {exc}")
            self._fail(500, str(exc))
            return

        cost = max(time.time() - started, 0.001)
        self.log(f"上传 {name}  {human(received)}  {human(received / cost)}/s  ← {self.client_address[0]}")
        self._json(200, {"ok": True, "name": target.name, "size": received})
        self.server.note_transfer("up", received)

    def _handle_asr(self, quiet=False):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._fail(400, "没收到录音")
            return
        if length > 25 * 1024 * 1024:
            self._fail(413, "录音太大")
            return
        if not asr_ready():
            self._fail(503, "识别模型不在：检查 模型/ 目录")
            return

        ctype = (self.headers.get("Content-Type") or "").lower()
        suffix = ".m4a" if ("mp4" in ctype or "m4a" in ctype or "aac" in ctype) else ".webm"
        if "wav" in ctype:
            suffix = ".wav"
        fd, path = tempfile.mkstemp(suffix=suffix)
        received = 0
        try:
            with os.fdopen(fd, "wb") as fh:
                while received < length:
                    block = self.rfile.read(min(CHUNK, length - received))
                    if not block:
                        break
                    fh.write(block)
                    received += len(block)
            started = time.time()
            text = transcribe(path)
            cost = time.time() - started
            if not quiet:
                self.log(f"语音识别 {len(text)} 字，耗时 {cost:.1f}s  ← {self.client_address[0]}")
            self._json(200, {"ok": True, "text": text, "seconds": round(cost, 2)})
        except Exception as exc:
            self.log(f"语音识别失败：{exc}")
            self._fail(500, f"识别失败：{exc}")
        finally:
            Path(path).unlink(missing_ok=True)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/asr":
            self._handle_asr(quiet="partial" in parsed.query)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            payload = {}

        if parsed.path == "/api/delete":
            name = safe_name(payload.get("name", ""))
            target = self.root / name if name else None
            if not name or not inside(self.root, target):
                self._fail(400, "文件名不合法")
                return
            if not target.is_file():
                self._fail(404, "文件不存在")
                return
            try:
                target.unlink()
                self.log(f"删除 {name}  ← {self.client_address[0]}")
                self._json(200, {"ok": True})
            except OSError as exc:
                self._fail(500, str(exc))
            return

        if parsed.path == "/api/settings":
            if "autopaste" in payload:
                SETTINGS.set("autopaste", bool(payload["autopaste"]))
                self.log(f"自动粘贴：{'开' if SETTINGS.autopaste else '关'}  ← {self.client_address[0]}")
            if "model" in payload:
                if payload["model"]:
                    get_asr()
                    self.log(f"语音模型已加载进内存  ← {self.client_address[0]}")
                else:
                    release_asr()
                    self.log(f"语音模型已释放  ← {self.client_address[0]}")
            self._json(200, {"ok": True, "autopaste": SETTINGS.autopaste,
                             "model": _ASR is not None})
            return

        if parsed.path == "/api/text":
            try:
                item = self.server.texts.add(payload.get("text", ""), self.client_address[0],
                                             str(payload.get("kind", "text")))
            except ValueError as exc:
                self._fail(400, str(exc))
                return
            label = "语音" if item["kind"] == "voice" else "文字"
            self.log(f"收到{label} {len(item['text'])} 字  ← {self.client_address[0]}")
            self._json(200, {"ok": True, "item": item})
            return

        if parsed.path == "/api/text/delete":
            ok = self.server.texts.delete(str(payload.get("id", "")))
            self._json(200 if ok else 404, {"ok": ok})
            return

        self._fail(404, "not found")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, root_path, report):
        self.root_path = Path(root_path)
        self.report = report
        self.texts = get_texts()
        self.bytes_up = 0
        self.bytes_down = 0
        self.count = 0
        super().__init__(address, TransferHandler)

    def note_transfer(self, kind, size):
        if kind == "up":
            self.bytes_up += size
        else:
            self.bytes_down += size
        self.count += 1


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text("utf-8"))
    except Exception:
        return {}


def write_config(**values):
    try:
        data = load_config()
        data.update({k: str(v) if isinstance(v, Path) else v for k, v in values.items()})
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "LanFileTransfer"


class Runtime:
    """运行时开关，手机和电脑改的是同一份。"""

    def __init__(self):
        self.autopaste = bool(load_config().get("autopaste", True))

    def set(self, name, value):
        setattr(self, name, value)
        write_config(**{name: value})


SETTINGS = Runtime()


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    return f'"{pythonw}" "{Path(__file__).resolve()}"'


def autostart_on():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0] == autostart_command()
    except OSError:
        return False


def set_autostart(enable):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, access=winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
    if autostart_on() != enable:
        raise OSError("启动项被安全软件拦掉了，请在 360 的弹窗里选「允许 / 加白名单」")


# ---- Ant Design（阿里）设计令牌：色板 / 字号 / 控件高度 ----
# 字号 12/14/16/20 -> 9/10/12/15 pt；控件默认高 32px
THEMES = {
    "浅色": {"layout": "#f5f5f5", "container": "#ffffff", "border": "#d9d9d9", "split": "#f0f0f0",
             "text": "#141414", "text2": "#666666", "text3": "#bfbfbf", "fill": "#fafafa",
             "primary": "#1677ff", "primary_hover": "#4096ff", "on_primary": "#ffffff",
             "tag_bg": "#e6f4ff", "tag_ink": "#1677ff", "field": "#ffffff",
             "console_bg": "#141414", "console_ink": "#d9d9d9", "danger": "#ff4d4f"},
    "深色": {"layout": "#141414", "container": "#1f1f1f", "border": "#424242", "split": "#303030",
             "text": "#e8e8e8", "text2": "#a6a6a6", "text3": "#595959", "fill": "#1a1a1a",
             "primary": "#1668dc", "primary_hover": "#3c89e8", "on_primary": "#ffffff",
             "tag_bg": "#111a2c", "tag_ink": "#3c89e8", "field": "#141414",
             "console_bg": "#0d0d0d", "console_ink": "#c8c8c8", "danger": "#d9484b"},
}
FONT = "Microsoft YaHei UI"
MONO = "Consolas"


class Switch(tk.Frame):
    """Ant Design 的 Switch：44x22 胶囊轨道 + 18px 圆形滑块。"""

    def __init__(self, parent, variable, command, theme):
        super().__init__(parent, bg=theme["container"], cursor="hand2")
        self.variable, self.command, self.theme = variable, command, theme
        self.canvas = tk.Canvas(self, width=44, height=22, highlightthickness=0,
                                bg=theme["container"], cursor="hand2")
        self.canvas.pack()
        for widget in (self, self.canvas):
            widget.bind("<Button-1>", self.toggle)
        self.draw()

    def toggle(self, _=None):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()
        self.draw()

    def draw(self):
        on = bool(self.variable.get())
        c = self.canvas
        c.delete("all")
        c.create_line(11, 11, 33, 11, width=22, capstyle="round",
                      fill=self.theme["primary"] if on else self.theme["text3"])
        center = 33 if on else 11
        c.create_oval(center - 9, 2, center + 9, 20, fill="#ffffff", outline="")


class ScrollList(tk.Frame):
    """可滚动的卡片列表（Canvas + 内嵌 Frame）。"""

    def __init__(self, parent, theme):
        super().__init__(parent, bg=theme["container"])
        self.canvas = tk.Canvas(self, bg=theme["container"], highlightthickness=0, bd=0)
        self.bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                troughcolor=theme["container"], bg=theme["split"],
                                activebackground=theme["border"], relief="flat", bd=0, width=8)
        self.inner = tk.Frame(self.canvas, bg=theme["container"])
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.inner.bind("<MouseWheel>", self._wheel)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()


class App:
    def __init__(self):
        self.win = tk.Tk()
        self.win.title("局域网文件传输")

        config = load_config()
        self.folder_var = tk.StringVar(value=config.get("folder") or str(DEFAULT_SHARE))
        self.port_var = tk.StringVar(value=str(config.get("port") or 8000))
        self.theme_name = config.get("theme") if config.get("theme") in THEMES else "浅色"
        self.autostart_var = tk.BooleanVar(value=autostart_on())
        self.autopaste_var = tk.BooleanVar(value=SETTINGS.autopaste)
        self.last_text_id = None

        self.log_queue = Queue()
        self.log_lines = []
        self.texts = get_texts()
        self.text_sig = None
        self.tab = "传输记录"
        self.server = None
        self.thread = None
        self.https_server = None
        self.https_thread = None
        self._cert = (None, None)
        self.qr_image = None
        self.url = ""
        self._files = 0
        self._tick = 0

        self._build()
        self._ensure_folder()
        existing = self.texts.listing()
        self.last_text_id = existing[0]["id"] if existing else None
        self.log("手机扫下面的二维码，或在浏览器里直接输入地址。")
        self.win.after(120, self._pump)
        self.win.after(350, self._auto_start)
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- 界面 ----------
    def _build(self):
        for child in self.win.winfo_children():
            child.destroy()
        t = self.t = THEMES[self.theme_name]
        self.win.configure(bg=t["layout"])
        self.win.geometry("1000x720")
        self.win.minsize(920, 680)

        style = ttk.Style(self.win)
        style.theme_use("clam")
        style.configure("Ant.TEntry", padding=(11, 6), relief="flat", font=(FONT, 10),
                        fieldbackground=t["field"], background=t["field"], foreground=t["text"],
                        bordercolor=t["border"], lightcolor=t["border"], darkcolor=t["border"])
        style.map("Ant.TEntry",
                  bordercolor=[("focus", t["primary"])],
                  lightcolor=[("focus", t["primary"])],
                  darkcolor=[("focus", t["primary"])],
                  fieldbackground=[("readonly", t["fill"])])

        self._header(t)
        body = tk.Frame(self.win, bg=t["layout"])
        body.pack(fill="both", expand=True, padx=24, pady=24)

        left, left_body = self._card(body, "服务设置", width=392)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right, log_page, text_page = self._right_card(body)
        right.pack(side="left", fill="both", expand=True, padx=(24, 0))

        self._field(left_body, "共享文件夹")
        row = tk.Frame(left_body, bg=t["container"])
        row.pack(fill="x", pady=(6, 16))
        self.folder_entry = ttk.Entry(row, textvariable=self.folder_var, style="Ant.TEntry")
        self.folder_entry.pack(side="left", fill="x", expand=True)
        self._button(row, "浏 览", command=self.pick_folder).pack(side="left", padx=(8, 0))

        self._field(left_body, "端口")
        row = tk.Frame(left_body, bg=t["container"])
        row.pack(fill="x", pady=(6, 16))
        self.port_entry = ttk.Entry(row, textvariable=self.port_var, style="Ant.TEntry", width=8)
        self.port_entry.pack(side="left")
        self.port_entry.bind("<Return>", lambda e: self.start())
        self.start_btn = self._button(row, "启动服务", "primary", self.start)
        self.start_btn.pack(side="left", padx=(12, 0))
        self.stop_btn = self._button(row, "停 止", command=self.stop)
        self.stop_btn.pack(side="left", padx=(8, 0))

        auto_row = tk.Frame(left_body, bg=t["container"])
        auto_row.pack(fill="x", pady=(0, 16))
        tk.Label(auto_row, text="开机自动启动", bg=t["container"], fg=t["text"],
                 font=(FONT, 10)).pack(side="left")
        self.autostart_switch = Switch(auto_row, self.autostart_var, self.toggle_autostart, t)
        self.autostart_switch.pack(side="right")

        tk.Frame(left_body, bg=t["split"], height=1).pack(fill="x", pady=(0, 16))

        self._field(left_body, "手机访问地址")
        addr = tk.Frame(left_body, bg=t["container"])
        addr.pack(fill="x", pady=(6, 10))
        self.addr_var = tk.StringVar(value="未启动")
        tk.Label(addr, textvariable=self.addr_var, bg=t["fill"], fg=t["text"],
                 font=(MONO, 12), anchor="w", padx=12, pady=10,
                 highlightbackground=t["split"], highlightthickness=1).pack(
            side="left", fill="x", expand=True)
        self._button(addr, "复 制", command=self.copy_url).pack(side="left", padx=(8, 0))

        links = tk.Frame(left_body, bg=t["container"])
        links.pack(anchor="w")
        self._button(links, "本机打开", "link", self.open_local).pack(side="left")
        self._button(links, "刷新地址", "link", self.start).pack(side="left", padx=(16, 0))

        self.qr_box = tk.Label(left_body, bg="#ffffff", fg=t["text2"], font=(FONT, 9))
        self.qr_box.pack(pady=(14, 0))

        self.log_box = tk.Text(log_page, bg=t["console_bg"], fg=t["console_ink"],
                               insertbackground=t["console_ink"], relief="flat", bd=0,
                               padx=14, pady=12, wrap="char", font=(MONO, 9), state="disabled")
        self.log_box.pack(fill="both", expand=True)
        for line in self.log_lines[-400:]:
            self._append(line)

        self._repaint()

    def _header(self, t):
        head = tk.Frame(self.win, bg=t["container"], height=56)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Frame(self.win, bg=t["split"], height=1).pack(fill="x")

        tk.Label(head, text="局域网文件传输", bg=t["container"], fg=t["text"],
                 font=(FONT, 12, "bold")).pack(side="left", padx=(24, 0))
        self.tag = tk.Label(head, text="", bg=t["tag_bg"], fg=t["tag_ink"],
                            font=(FONT, 9), padx=8, pady=2)
        self.tag.pack(side="left", padx=(12, 0))

        seg = tk.Frame(head, bg=t["fill"], highlightbackground=t["border"], highlightthickness=1)
        seg.pack(side="right", padx=(0, 24))
        for name in THEMES:
            active = name == self.theme_name
            item = tk.Label(seg, text=name, font=(FONT, 9, "bold" if active else "normal"),
                            bg=t["container"] if active else t["fill"],
                            fg=t["text"] if active else t["text2"], padx=12, pady=4,
                            cursor="hand2")
            item.pack(side="left")
            item.bind("<Button-1>", lambda e, n=name: self.set_theme(n))

    def _card(self, parent, title, width=None, stats=False):
        t = self.t
        card = tk.Frame(parent, bg=t["container"], highlightbackground=t["split"],
                        highlightthickness=1)
        if width:
            card.configure(width=width)
        head = tk.Frame(card, bg=t["container"])
        head.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(head, text=title, bg=t["container"], fg=t["text"],
                 font=(FONT, 12, "bold")).pack(side="left")
        if stats:
            self.stats_var = tk.StringVar(value="")
            tk.Label(head, textvariable=self.stats_var, bg=t["container"], fg=t["text2"],
                     font=(FONT, 9)).pack(side="right")
        tk.Frame(card, bg=t["split"], height=1).pack(fill="x", pady=(14, 0))
        body = tk.Frame(card, bg=t["container"])
        body.pack(fill="both", expand=True, padx=20, pady=20)
        return card, body

    def _right_card(self, parent):
        t = self.t
        card = tk.Frame(parent, bg=t["container"], highlightbackground=t["split"],
                        highlightthickness=1)
        head = tk.Frame(card, bg=t["container"])
        head.pack(fill="x", padx=20, pady=(14, 0))
        self.tab_items = {}
        for name in ("传输记录", "文字互传"):
            col = tk.Frame(head, bg=t["container"])
            col.pack(side="left", padx=(0, 20))
            label = tk.Label(col, text=name, bg=t["container"], font=(FONT, 10), cursor="hand2")
            label.pack()
            bar = tk.Frame(col, bg=t["container"], height=2)
            bar.pack(fill="x", pady=(6, 0))
            label.bind("<Button-1>", lambda e, n=name: self.show_tab(n))
            self.tab_items[name] = (label, bar)
        self.stats_var = tk.StringVar(value="")
        self.stats_label = tk.Label(head, textvariable=self.stats_var, bg=t["container"],
                                    fg=t["text2"], font=(FONT, 9))
        self.stats_label.pack(side="right")

        tk.Frame(card, bg=t["split"], height=1).pack(fill="x", pady=(8, 0))
        holder = tk.Frame(card, bg=t["container"])
        holder.pack(fill="both", expand=True, padx=20, pady=16)
        log_page = tk.Frame(holder, bg=t["container"])
        text_page = tk.Frame(holder, bg=t["container"])
        self.pages = {"传输记录": log_page, "文字互传": text_page}
        self._build_text_page(text_page)
        self.show_tab("传输记录")
        return card, log_page, text_page

    def _build_text_page(self, page):
        t = self.t
        self.text_input = tk.Text(page, height=3, bg=t["field"], fg=t["text"], relief="flat", bd=0,
                                  highlightthickness=1, highlightbackground=t["border"],
                                  insertbackground=t["text"], padx=10, pady=8,
                                  font=(FONT, 10), wrap="char")
        self.text_input.pack(fill="x")
        self.text_input.bind("<Control-Return>", self._send_key)
        row = tk.Frame(page, bg=t["container"])
        row.pack(fill="x", pady=(8, 6))
        tk.Label(row, text="Ctrl + Enter 直接发送", bg=t["container"], fg=t["text3"],
                 font=(FONT, 9)).pack(side="left")
        self._button(row, "发送到手机", "primary", self.send_text).pack(side="right")

        row = tk.Frame(page, bg=t["container"])
        row.pack(fill="x", pady=(0, 10))
        tk.Label(row, text="收到后自动粘贴到当前窗口", bg=t["container"], fg=t["text2"],
                 font=(FONT, 9)).pack(side="left")
        self.autopaste_switch = Switch(row, self.autopaste_var, self.toggle_autopaste, t)
        self.autopaste_switch.pack(side="right")

        self.latest_var = tk.StringVar(value="手机发来的文字会自动复制到剪贴板")
        tk.Label(page, textvariable=self.latest_var, bg=t["container"], fg=t["text2"],
                 font=(FONT, 9), anchor="w", justify="left", wraplength=430).pack(
            fill="x", pady=(0, 12))

        self.text_list = ScrollList(page, t)
        self.text_list.pack(fill="both", expand=True)

    def show_tab(self, name):
        self.tab = name
        for key, page in self.pages.items():
            if key == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for key, (label, bar) in self.tab_items.items():
            active = key == name
            label.configure(fg=self.t["text"] if active else self.t["text2"],
                            font=(FONT, 10, "bold" if active else "normal"))
            bar.configure(bg=self.t["primary"] if active else self.t["container"])
        if name == "传输记录":
            self.stats_label.pack(side="right")
        else:
            self.stats_label.pack_forget()
            self.text_sig = None
            self.refresh_texts()

    def _mini(self, parent, text, color, command):
        label = tk.Label(parent, text=text, bg=self.t["fill"], fg=color, font=(FONT, 9),
                         cursor="hand2", padx=6, pady=2)
        label.bind("<Button-1>", lambda e: command())
        return label

    def refresh_texts(self):
        t = self.t
        items = [x for x in self.texts.listing() if x.get("kind") != "voice"]
        self.text_sig = tuple(x["id"] for x in items)
        self.text_list.clear()
        if not items:
            tk.Label(self.text_list.inner, text="还没有文字，手机或电脑发一条试试",
                     bg=t["container"], fg=t["text3"], font=(FONT, 9)).pack(pady=20)
            return
        for item in items:
            card = tk.Frame(self.text_list.inner, bg=t["fill"], highlightbackground=t["split"],
                            highlightthickness=1, padx=12, pady=10)
            card.pack(fill="x", pady=(0, 10))
            body = item["text"]
            shown = body if len(body) <= 300 else body[:300] + " …"
            label = tk.Label(card, text=shown, bg=t["fill"], fg=t["text"], font=(FONT, 10),
                             justify="left", anchor="w", wraplength=380)
            label.pack(fill="x")
            card.bind("<Configure>",
                      lambda e, lb=label: lb.configure(wraplength=max(e.width - 28, 160)))
            meta = tk.Frame(card, bg=t["fill"])
            meta.pack(fill="x", pady=(8, 0))
            stamp = time.strftime("%m-%d %H:%M", time.localtime(item.get("time", 0)))
            tk.Label(meta, text=f"{stamp} · {len(body)} 字", bg=t["fill"], fg=t["text3"],
                     font=(FONT, 9)).pack(side="left")
            self._mini(meta, "删", t["danger"], lambda i=item: self.delete_text(i["id"])).pack(side="right")
            self._mini(meta, "复制", t["primary"],
                       lambda i=item: self.copy_text(i["text"])).pack(side="right", padx=(0, 10))

    def send_text(self):
        content = self.text_input.get("1.0", "end").strip()
        if not content:
            return
        try:
            item = self.texts.add(content, "电脑")
        except ValueError as exc:
            messagebox.showwarning("发不出去", str(exc))
            return
        self.last_text_id = item["id"]
        self.text_input.delete("1.0", "end")
        self.refresh_texts()
        self.log(f"已发送文字 {len(content)} 字，手机刷新就能看到。")

    def _poll_texts(self):
        if SETTINGS.autopaste != self.autopaste_var.get():
            self.autopaste_var.set(SETTINGS.autopaste)
            self.autopaste_switch.draw()
            self.log(f"手机那边把自动粘贴改成了{'开' if SETTINGS.autopaste else '关'}。")
        items = self.texts.listing()
        newest = items[0] if items else None
        if newest and newest["id"] != self.last_text_id:
            self.last_text_id = newest["id"]
            if newest.get("from") != "电脑":
                self._on_incoming(newest)
        if self.tab == "文字互传" and tuple(x["id"] for x in items) != self.text_sig:
            self.refresh_texts()

    def _on_incoming(self, item):
        text = item["text"]
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        brief = " ".join(text.split())
        brief = brief[:38] + ("…" if len(brief) > 38 else "")
        if item.get("kind") == "voice":
            self.latest_var.set(f"最新语音：{brief}　已粘贴到当前窗口")
        else:
            self.latest_var.set(f"最新收到：{brief}　已复制，Ctrl+V 粘贴")
        try:
            self.win.bell()
        except tk.TclError:
            pass
        if SETTINGS.autopaste:
            self.win.after(260, self._do_paste)
        self.log(f"收到{'语音' if item.get('kind') == 'voice' else '文字'} "
                 f"{len(text)} 字，已放进剪贴板。")

    def _do_paste(self):
        if foreground_pid() == os.getpid():
            self.log("当前焦点在本程序上，这次没自动粘贴。")
            return
        simulate_paste()
        self.log("已自动粘贴到当前窗口。")

    def _send_key(self, _event):
        self.send_text()
        return "break"

    def copy_text(self, text):
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self.log(f"已复制 {len(text)} 字到剪贴板。")

    def delete_text(self, item_id):
        if self.texts.delete(item_id):
            self.refresh_texts()

    def _field(self, parent, text):
        tk.Label(parent, text=text, bg=self.t["container"], fg=self.t["text"],
                 font=(FONT, 10)).pack(anchor="w")

    def _button(self, parent, text, kind="default", command=None):
        t = self.t
        if kind == "primary":
            bg, fg, edge = t["primary"], t["on_primary"], t["primary"]
        elif kind == "link":
            bg, fg, edge = t["container"], t["primary"], t["container"]
        else:
            bg, fg, edge = t["container"], t["text"], t["border"]
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=bg, activeforeground=fg,
                         disabledforeground=t["text3"], relief="flat", bd=0, cursor="hand2",
                         highlightthickness=1, highlightbackground=edge,
                         font=(FONT, 10), padx=15, pady=5)

    def _repaint(self):
        running = bool(self.server)
        self.stop_btn.configure(state="normal" if running else "disabled")
        self._paint_tag()
        if running:
            self.addr_var.set(self.url)
            self._show_qr()
        else:
            self.addr_var.set("未启动")
            self.qr_image = None
            self.qr_box.configure(image="", text="")

    # ---------- 动作 ----------
    def set_theme(self, name):
        if name == self.theme_name:
            return
        self.theme_name = name
        write_config(theme=name)
        self._build()

    def toggle_autopaste(self):
        SETTINGS.set("autopaste", bool(self.autopaste_var.get()))

    def toggle_autostart(self):
        want = self.autostart_var.get()
        try:
            set_autostart(want)
        except OSError as exc:
            self.autostart_var.set(not want)
            messagebox.showwarning("改不了开机自启", str(exc))
            self.log(f"开机自启设置失败：{exc}")
            return
        self.log("已开启开机自启，下次开机自动运行。" if want else "已关闭开机自启。")

    def _ensure_folder(self):
        folder = Path(self.folder_var.get().strip() or DEFAULT_SHARE)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.folder_var.set(str(folder))
        except OSError as exc:
            messagebox.showerror("文件夹打不开", str(exc))

    def pick_folder(self):
        chosen = filedialog.askdirectory(title="选择共享文件夹",
                                         initialdir=self.folder_var.get() or str(Path.home()))
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        self.folder_var.set(chosen)
        write_config(folder=chosen)
        if self.server:
            self.server.root_path = Path(chosen)
            self.log(f"共享目录已切换为 {chosen}")

    def _auto_start(self):
        if not self.server:
            self.start()

    def start(self):
        if self.server:
            self.stop()
        folder = Path(self.folder_var.get().strip())
        if not folder.is_dir():
            messagebox.showerror("文件夹不存在", f"目录不可用：\n{folder}")
            return
        try:
            port = int(self.port_var.get().strip())
            assert 1 <= port <= 65535
        except ValueError:
            messagebox.showerror("端口不对", "端口要填 1-65535 的数字")
            return
        ip = lan_ip()
        self._cert = ensure_cert(ip)
        try:
            self.server = Server(("0.0.0.0", port), folder, self.log)
        except OSError as exc:
            messagebox.showerror("启动失败", f"端口 {port} 打不开：\n{exc}")
            self.server = None
            return

        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="lan-http", daemon=True)
        self.thread.start()
        if not getattr(self, "_idle_thread", None) or not self._idle_thread.is_alive():
            self._idle_thread = threading.Thread(target=asr_idle_watch, args=(self.log,),
                                                 name="asr-idle", daemon=True)
            self._idle_thread.start()
        self.url = f"http://{ip}:{port}"
        write_config(folder=folder, port=port)
        self.addr_var.set(self.url)
        self._show_qr()
        self._paint_tag()
        self.log(f"已启动 {self.url}   目录 {folder}")

        cert, key = self._cert
        if cert:
            try:
                secure = Server(("0.0.0.0", port + 1), folder, self.log)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(str(cert), str(key))
                secure.socket = ctx.wrap_socket(secure.socket, server_side=True)
                self.https_server = secure
                self.https_thread = threading.Thread(target=secure.serve_forever,
                                                     name="lan-https", daemon=True)
                self.https_thread.start()
                self.url = f"https://{ip}:{port + 1}"
                self.addr_var.set(self.url)
                self._show_qr()
                self._paint_tag()
                self.log(f"默认用安全地址 {self.url}（能网页录音）；"
                         f"http://{ip}:{port} 仍然可用")
            except Exception as exc:
                self.log(f"HTTPS 没起来（不影响传文件和文字）：{exc}")
                self.https_server = None
        else:
            self.log("没找到 openssl，语音只能用系统录音机那条路。")

    def stop(self):
        if not self.server:
            return
        server, thread = self.server, self.thread
        self.server = None
        self.thread = None
        server.shutdown()
        server.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=2)
        secure = getattr(self, "https_server", None)
        if secure:
            self.https_server = None
            secure.shutdown()
            secure.server_close()
        self._repaint()
        self.log("服务已停止。")

    def copy_url(self):
        if not self.url:
            return
        self.win.clipboard_clear()
        self.win.clipboard_append(self.url)
        self.log("地址已复制到剪贴板。")

    def open_local(self):
        if self.url:
            webbrowser.open(self.url)

    def _show_qr(self):
        if not self.url:
            return
        if not QR_ENABLED:
            self.qr_box.configure(text="不显示二维码：pip install qrcode pillow")
            return
        try:
            data = qrcode.QRCode(border=1, box_size=6)
            data.add_data(self.url)
            data.make(fit=True)
            img = data.make_image(fill_color="#000000", back_color="#ffffff").get_image()
            img = img.resize((176, 176), Image.NEAREST)
            self.qr_image = ImageTk.PhotoImage(img)
            self.qr_box.configure(image=self.qr_image, text="")
        except Exception as exc:
            self.qr_box.configure(text=f"二维码生成失败：{exc}")

    def _paint_tag(self):
        if self.server:
            port = urllib.parse.urlsplit(self.url).port if self.url else None
            port = port or self.server.server_address[1]
            self.tag.configure(text=f"运行中 · 端口 {port}", bg=self.t["tag_bg"],
                               fg=self.t["tag_ink"])
            self.stats_var.set(f"共 {self._files} 个文件    "
                               f"已上传 {human(self.server.bytes_up)}    "
                               f"已下载 {human(self.server.bytes_down)}")
        else:
            self.tag.configure(text="未运行", bg=self.t["fill"], fg=self.t["text3"])
            self.stats_var.set("")

    # ---------- 日志 ----------
    def log(self, message):
        self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] {message}")

    def _append(self, line):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _pump(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_lines.append(line)
                del self.log_lines[:-400]
                self._append(line)
        except Empty:
            pass
        self._tick += 1
        if self.server:
            if self._tick % 6 == 1:
                try:
                    self._files = sum(1 for e in os.scandir(self.server.root_path) if e.is_file())
                except OSError:
                    self._files = 0
            self._paint_tag()
        if self._tick % 3 == 0:
            self._poll_texts()
        if self._tick % 12 == 0:
            actual = autostart_on()
            if actual != self.autostart_var.get():
                self.autostart_var.set(actual)
                self.autostart_switch.draw()
                self.log(f"开机自启被改成了{'开' if actual else '关'}。")
        self.win.after(400, self._pump)

    def on_close(self):
        write_config(folder=self.folder_var.get(), port=self.port_var.get())
        self.stop()
        self.win.destroy()

    def run(self):
        self.win.mainloop()


if __name__ == "__main__":
    App().run()

