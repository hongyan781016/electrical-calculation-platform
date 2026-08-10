from __future__ import annotations

import json

from src.electrical_calc import launcher


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]):
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_build_url_uses_loopback_for_wildcard_bind_address():
    assert launcher.build_url("0.0.0.0", 8765) == "http://127.0.0.1:8765/quick"
    assert launcher.build_url("127.0.0.1", 9000, "health") == (
        "http://127.0.0.1:9000/health"
    )


def test_application_is_running_requires_platform_health_payload(monkeypatch):
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(200, {"status": "ok"}),
    )
    assert launcher.application_is_running("127.0.0.1", 8765) is True

    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(200, {"status": "other"}),
    )
    assert launcher.application_is_running("127.0.0.1", 8765) is False


def test_wait_for_application_opens_quick_page_after_health_is_ready():
    checks = iter([False, False, True])
    opened: list[str] = []

    assert launcher.wait_for_application_and_open(
        "127.0.0.1",
        8765,
        attempts=3,
        interval_seconds=0,
        health_checker=lambda *_args: next(checks),
        opener=lambda url: opened.append(url),
    ) is True
    assert opened == ["http://127.0.0.1:8765/quick"]


def test_pid_file_round_trip_and_expected_pid_guard(tmp_path):
    pid_file = tmp_path / "application.pid"
    launcher.write_pid_file(pid_file, 1234)
    assert launcher.read_pid_file(pid_file) == 1234

    launcher.remove_pid_file(pid_file, expected_pid=5678)
    assert launcher.read_pid_file(pid_file) == 1234

    launcher.remove_pid_file(pid_file, expected_pid=1234)
    assert launcher.read_pid_file(pid_file) is None
