from __future__ import annotations

import webbrowser

import run


def test_main_reuses_running_application_and_opens_quick_page(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(run, "application_is_running", lambda *_args: True)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    assert run.main([]) == 0
    assert opened == ["http://127.0.0.1:8765/quick"]


def test_main_reports_port_owned_by_another_application(monkeypatch, capsys):
    monkeypatch.setattr(run, "application_is_running", lambda *_args: False)
    monkeypatch.setattr(run, "tcp_port_is_open", lambda *_args: True)

    assert run.main(["--no-browser"]) == 2
    output = capsys.readouterr().out
    assert "端口 8765 已被其他程序占用" in output
    assert "--port 8766" in output


def test_stop_without_pid_is_safe_when_application_is_not_running(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(run, "pid_file_for_port", lambda _port: tmp_path / "missing.pid")
    monkeypatch.setattr(run, "application_is_running", lambda *_args: False)

    assert run.stop_application("127.0.0.1", 8765) == 0
    assert "应用当前没有运行" in capsys.readouterr().out
