from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PAGE_PATH = "/quick"
HEALTH_PATH = "/health"


def browser_host(host: str) -> str:
    if host in {"0.0.0.0", "::", "::0"}:
        return "127.0.0.1"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def build_url(host: str, port: int, path: str = DEFAULT_PAGE_PATH) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"http://{browser_host(host)}:{port}{normalized_path}"


def application_is_running(host: str, port: int, timeout: float = 0.6) -> bool:
    health_url = build_url(host, port, HEALTH_PATH)
    try:
        with urllib.request.urlopen(health_url, timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("status") == "ok"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def tcp_port_is_open(host: str, port: int, timeout: float = 0.3) -> bool:
    connect_host = browser_host(host).strip("[]")
    try:
        with socket.create_connection((connect_host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_application_and_open(
    host: str,
    port: int,
    page_path: str = DEFAULT_PAGE_PATH,
    *,
    attempts: int = 50,
    interval_seconds: float = 0.1,
    health_checker: Callable[[str, int], bool] = application_is_running,
    opener: Callable[[str], object] = webbrowser.open,
) -> bool:
    for _ in range(attempts):
        if health_checker(host, port):
            opener(build_url(host, port, page_path))
            return True
        time.sleep(interval_seconds)
    return False


def write_pid_file(pid_file: Path, pid: int | None = None) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid or os.getpid()), encoding="ascii")


def read_pid_file(pid_file: Path) -> int | None:
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return pid if pid > 0 else None


def remove_pid_file(pid_file: Path, expected_pid: int | None = None) -> None:
    if expected_pid is not None and read_pid_file(pid_file) != expected_pid:
        return
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass
