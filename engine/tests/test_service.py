import json
import socket

from zhiyi import cli, service
from zhiyi.config import Config


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_start_status_stop_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIYI_HOME", str(tmp_path))
    port = _free_port()
    assert service.status(port) == {"ok": True, "running": False, "port": port}
    started = service.start(port)
    try:
        assert started["ok"] and started["started"], started
        again = service.start(port)
        assert again["ok"] and again["started"] is False             # idempotent
        assert service.status(port)["current"] is True
    finally:
        assert service.stop(port)["stopped"] is True
    assert service.status(port)["running"] is False


def test_foreign_port_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIYI_HOME", str(tmp_path))
    monkeypatch.setattr(service, "_get", lambda port, path, timeout=2.0: {"status": "ok"})
    result = service.status(1234)
    assert not result["ok"] and "占用" in result["message"]


def test_cli_dispatches_service_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZHIYI_HOME", str(tmp_path))
    assert cli.main(["service", "status", "--port", str(_free_port())]) == 0
    assert json.loads(capsys.readouterr().out)["running"] is False


def test_extra_terms_text_and_limits():
    config = Config.from_dict({"extra_terms": "knowledge graph = 知识图谱\nFoodKG => 食物知识图谱\nnoise",
                               "threads": 99, "font_scale": 3})
    assert config.extra_terms == {"knowledge graph": "知识图谱", "FoodKG": "食物知识图谱"}
    assert config.threads == 16 and config.font_scale == 1.2
