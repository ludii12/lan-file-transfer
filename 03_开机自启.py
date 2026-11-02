# -*- coding: utf-8 -*-
"""开机自启命令行（图形界面里也有同一个开关，两边读写的是同一个注册表项）

  python 03_开机自启.py            查看状态
  python 03_开机自启.py on         开启
  python 03_开机自启.py off        关闭
  python 03_开机自启.py test       按自启方式立刻跑一次，验证能不能用
  python 03_开机自启.py 防火墙     放行防火墙（手机打不开时用，会弹管理员授权）

注意：这台机器装了 360 安全卫士，它会拦启动项改动。
      别用「启动」文件夹放快捷方式——它会被 360 悄悄删掉，所以走注册表。
"""

import http.client
import importlib.util
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lan", str(APP_DIR / "01_启动.py"))
lan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lan)


def saved_port():
    try:
        return int(json.loads((APP_DIR / "config.json").read_text("utf-8")).get("port") or 8000)
    except Exception:
        return 8000


def status():
    if lan.autostart_on():
        print("[已开启] 登录 Windows 后自动运行")
        print(f"        {lan.autostart_command()}")
    else:
        print("[未开启] 注册表启动项里没有这个程序")


def turn_on():
    try:
        lan.set_autostart(True)
    except OSError as exc:
        print(f"[失败] {exc}")
        return
    status()
    print("        360 弹窗问你的时候记得选「允许」。")


def turn_off():
    try:
        lan.set_autostart(False)
        print("[已关闭] 启动项已删除")
    except OSError as exc:
        print(f"[失败] {exc}")


def launch_now():
    """用和开机自启一模一样的方式拉起来，验证真能用。"""
    subprocess.run(lan.autostart_command(), shell=True)
    port = saved_port()
    for _ in range(15):
        time.sleep(1)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            conn.request("GET", "/api/list")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode("utf-8"))
            ip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ip.connect(("8.8.8.8", 80))
            url = f"http://{ip.getsockname()[0]}:{port}"
            print(f"[成功] 服务已起来：{url}")
            print(f"        目录：{data['root']}")
            print(f"        文件：{[f['name'] for f in data['files']]}")
            return
        except Exception:
            continue
    print("[失败] 15 秒内没听到服务，可能端口被占或程序报错。")


def open_firewall():
    port = saved_port()
    script = APP_DIR / "_防火墙临时.ps1"
    script.write_text(
        'netsh advfirewall firewall delete rule name="局域网文件传输" | Out-Null\n'
        f'netsh advfirewall firewall add rule name="局域网文件传输" dir=in action=allow '
        f'protocol=TCP localport={port} profile=private enable=yes\n',
        encoding="utf-8-sig")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "Start-Process powershell -Verb RunAs -Wait -ArgumentList "
             f"'-NoProfile','-ExecutionPolicy','Bypass','-File','\"{script}\"'"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(f"[完成] 已在「专用网络」放行 TCP {port} 入站")
    finally:
        script.unlink(missing_ok=True)


ACTIONS = {"on": turn_on, "开": turn_on, "开启": turn_on, "enable": turn_on,
           "off": turn_off, "关": turn_off, "关闭": turn_off, "disable": turn_off,
           "test": launch_now, "试": launch_now,
           "防火墙": open_firewall, "firewall": open_firewall}


def main():
    action = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    (ACTIONS.get(action) or status)()
    if sys.stdin and sys.stdin.isatty():
        try:
            input("\n按回车退出…")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
