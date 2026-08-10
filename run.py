from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

from src.electrical_calc.launcher import (
    DEFAULT_HOST,
    DEFAULT_PAGE_PATH,
    DEFAULT_PORT,
    application_is_running,
    build_url,
    read_pid_file,
    remove_pid_file,
    tcp_port_is_open,
    wait_for_application_and_open,
    write_pid_file,
)


PROJECT_DIR = Path(__file__).resolve().parent


def pid_file_for_port(port: int) -> Path:
    return PROJECT_DIR / ".codex-tmp" / f"electrical-calc-{port}.pid"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动电气工程计算自动化平台")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop", action="store_true", help="停止由本启动器运行的应用")
    return parser.parse_args(argv)


def stop_application(host: str, port: int) -> int:
    pid_file = pid_file_for_port(port)
    pid = read_pid_file(pid_file)
    if pid is None:
        if application_is_running(host, port):
            print("应用正在运行，但没有找到本启动器的进程记录；为避免误停其他程序，请在启动它的终端按 Ctrl+C。")
            return 2
        print("应用当前没有运行。")
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        remove_pid_file(pid_file, pid)
        print("进程记录已经失效，已清理；应用当前没有运行。")
        return 0
    except OSError as exc:
        print(f"无法停止应用进程 {pid}：{exc}")
        return 2

    for _ in range(30):
        if not application_is_running(host, port, timeout=0.2):
            remove_pid_file(pid_file, pid)
            print("应用已停止。")
            return 0
        time.sleep(0.1)

    print(f"已向进程 {pid} 发送停止请求，但端口仍在响应，请稍后再试。")
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    page_url = build_url(args.host, args.port, DEFAULT_PAGE_PATH)

    if args.stop:
        return stop_application(args.host, args.port)

    if application_is_running(args.host, args.port):
        print(f"应用已经在运行：{page_url}")
        if not args.no_browser:
            import webbrowser

            webbrowser.open(page_url)
        return 0

    if tcp_port_is_open(args.host, args.port):
        print(
            f"无法启动：端口 {args.port} 已被其他程序占用。\n"
            f"请关闭占用程序，或执行：python run.py --port {args.port + 1}"
        )
        return 2

    try:
        import uvicorn
    except ModuleNotFoundError:
        print(
            "缺少运行依赖。请在项目根目录执行：\n"
            r".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        )
        return 2

    if not args.no_browser:
        browser_thread = threading.Thread(
            target=wait_for_application_and_open,
            args=(args.host, args.port, DEFAULT_PAGE_PATH),
            daemon=True,
        )
        browser_thread.start()

    current_pid = os.getpid()
    pid_file = pid_file_for_port(args.port)
    write_pid_file(pid_file, current_pid)
    print(f"正在启动电气计算平台：{page_url}")
    print("保持本窗口打开；停止服务请按 Ctrl+C，或运行 python run.py --stop。")

    try:
        uvicorn.run(
            "src.electrical_calc.web:app",
            host=args.host,
            port=args.port,
            reload=False,
        )
    except OSError as exc:
        print(f"服务启动失败：{exc}")
        return 2
    finally:
        remove_pid_file(pid_file, current_pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
