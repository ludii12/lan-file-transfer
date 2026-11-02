# -*- coding: utf-8 -*-
"""自检：跑一遍真实 HTTP 接口（不需要 GUI）"""

import hashlib
import http.client
import importlib.util
import json
import os
import random
import shutil
import string
import sys
import tempfile
import threading
from pathlib import Path

APP = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lan", str(APP / "01_启动.py"))
lan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lan)

PORT = 8765
SHARE = Path(tempfile.mkdtemp(prefix="lantest-"))
logs = []
fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def req(method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    status = (resp.status, dict(resp.getheaders()), data)
    conn.close()
    return status


def main():
    (SHARE / "中文 文件.txt").write_bytes("你好，局域网\n".encode("utf-8"))
    lan._TEXT_STORE = lan.TextStore(Path(tempfile.mkdtemp(prefix="lantext-")) / "t.json")
    server = lan.Server(("127.0.0.1", PORT), SHARE, logs.append)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    st, hd, body = req("GET", "/")
    check("网页能打开", st == 200 and b"<html" in body.lower(), f"HTTP {st}, {len(body)} 字节")

    st, hd, body = req("GET", "/api/list")
    listing = json.loads(body)
    names = [f["name"] for f in listing["files"]]
    check("列表能看到文件", st == 200 and "中文 文件.txt" in names, str(names))

    st, hd, body = req("GET", "/f/%E4%B8%AD%E6%96%87%20%E6%96%87%E4%BB%B6.txt")
    check("中文名能下载", st == 200 and body.decode("utf-8") == "你好，局域网\n",
          hd.get("Content-Disposition", ""))

    blob = os.urandom(4 * 1024 * 1024)
    st, hd, body = req("PUT", "/u?name=%E5%A4%A7%E6%96%87%E4%BB%B6.bin", blob,
                       {"Content-Length": str(len(blob)), "Content-Type": "application/octet-stream"})
    saved = json.loads(body)
    ok = (st == 200 and saved["ok"] and (SHARE / "大文件.bin").is_file()
          and hashlib.md5((SHARE / "大文件.bin").read_bytes()).hexdigest() == hashlib.md5(blob).hexdigest())
    check("4MB 上传完整", ok, body[:80].decode("utf-8", "replace"))

    st, hd, body = req("PUT", "/u?name=%E5%A4%A7%E6%96%87%E4%BB%B6.bin", os.urandom(120))
    second = json.loads(body)["name"]
    check("重名自动改名", second == "大文件 (1).bin", second)

    st, hd, body = req("GET", "/f/..%2F..%2Fwindows%2Fwin.ini")
    check("路径穿越被拦", st in (400, 404), f"HTTP {st}")
    st, hd, body = req("POST", "/api/delete", json.dumps({"name": "../../etc/passwd"}),
                       {"Content-Type": "application/json"})
    check("删除接口拦穿越", st in (400, 404), f"HTTP {st}")

    st, hd, body = req("POST", "/api/delete", json.dumps({"name": "大文件 (1).bin"}),
                       {"Content-Type": "application/json"})
    check("能删除文件", st == 200 and not (SHARE / "大文件 (1).bin").exists())

    st, hd, body = req("GET", "/f/%E4%B8%8D%E5%AD%98%E5%9C%A8.bin")
    check("不存在的文件报 404", st == 404, f"HTTP {st}")

    check("日志有记录", any("上传" in x for x in logs), logs[-1] if logs else "空")

    st, hd, body = req("POST", "/api/text",
                       json.dumps({"text": "手机发来的一段话\n第二行"}),
                       {"Content-Type": "application/json"})
    item = json.loads(body)["item"]
    check("发文字成功", st == 200 and item["text"].startswith("手机发来"), f"HTTP {st}")

    st, hd, body = req("GET", "/api/text")
    items = json.loads(body)["items"]
    check("文字能读回来", st == 200 and items and items[0]["id"] == item["id"], f"{len(items)} 条")

    st, hd, body = req("POST", "/api/text", json.dumps({"text": "   "}),
                       {"Content-Type": "application/json"})
    check("空文字被拒", st == 400, f"HTTP {st}")

    st, hd, body = req("GET", "/api/settings")
    base = json.loads(body)["autopaste"]
    st, hd, body = req("POST", "/api/settings", json.dumps({"autopaste": False}),
                       {"Content-Type": "application/json"})
    check("手机能改自动粘贴", st == 200 and json.loads(body)["autopaste"] is False, f"HTTP {st}")
    st, hd, body = req("GET", "/api/settings")
    check("设置能读回来", json.loads(body)["autopaste"] is False, body[:40].decode())
    req("POST", "/api/settings", json.dumps({"autopaste": base}),
        {"Content-Type": "application/json"})

    st, hd, body = req("POST", "/api/text/delete", json.dumps({"id": item["id"]}),
                       {"Content-Type": "application/json"})
    st2, hd, body = req("GET", "/api/text")
    check("文字能删除", st == 200 and not json.loads(body)["items"], f"HTTP {st}")

    server.shutdown()
    server.server_close()
    shutil.rmtree(SHARE, ignore_errors=True)
    print("\n结果：" + ("全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
