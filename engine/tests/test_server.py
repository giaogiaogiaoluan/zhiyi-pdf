import base64
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from zhiyi import server as server_module


@pytest.fixture()
def service(paper, monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    httpd = server_module.make_server("127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", paper
    httpd.shutdown()


def _call(url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if response.headers.get_content_type() == "application/json" else raw)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _wait(base, task_id):
    for _ in range(300):
        _, history = _call(f"{base}/api/history")
        done = [t for t in history["history"] if t["taskId"] == task_id]
        if done:
            return done[0]
        time.sleep(0.1)
    raise AssertionError("task did not finish")


def test_job_round_trip(service):
    base, paper = service
    status, health = _call(f"{base}/health")
    assert status == 200 and health["status"] == "ok"
    body = {"fileName": "My Paper.pdf", "fileContent": base64.b64encode(paper.read_bytes()).decode(),
            "config": {"service": "pseudo", "dual_mode": "LR"}}
    status, accepted = _call(f"{base}/translate", body)
    assert status == 200 and accepted["status"] == "accepted"
    task = _wait(base, accepted["taskId"])
    assert task["status"] == "success" and task["finished"] is True
    assert task["fileList"] == ["My Paper.zh-CN.mono.pdf", "My Paper.zh-CN.dual.pdf"]
    assert task["summary"]["chained"] == 2 and task["summary"]["shrunk"] == 0
    status, pdf = _call(f"{base}/translatedFile/My%20Paper.zh-CN.mono.pdf")
    assert status == 200 and pdf[:5] == b"%PDF-"
    assert _call(f"{base}/translatedFile/..%2F..%2Fetc%2Fpasswd")[0] == 404


def test_legacy_plugin_fields_are_understood(service):
    base, paper = service
    body = {"fileName": "old.pdf", "fileContent": base64.b64encode(paper.read_bytes()).decode(),
            "next_service": "google", "sourceLang": "en", "targetLang": "zh-CN", "mono": "true", "dual": "false",
            "dualMode": "TB", "threadNum": "2", "qps": "3", "translateTableText": "false"}
    config = server_module.config_from_request(body)
    assert (config.service, config.mono, config.dual, config.dual_mode) == ("google", True, False, "TB")
    assert (config.threads, config.qps, config.translate_tables) == (2, 3.0, False)
    body.update(next_service="openailiked", llm_api={"service": "openailiked", "apiKey": "k", "model": "m",
                                                     "apiUrl": "https://example.test/v1"})
    config = server_module.config_from_request(body)
    assert (config.service, config.api_key, config.model, config.base_url) == ("openai", "k", "m", "https://example.test/v1")
    body.update(next_service="deepseek", llm_api={"service": "deepseek", "apiKey": "k", "model": "", "apiUrl": ""})
    config = server_module.config_from_request(body)
    assert config.base_url == "https://api.deepseek.com/v1" and config.model == "deepseek-chat"


def test_bad_requests_are_rejected_cleanly(service):
    base, paper = service
    encoded = base64.b64encode(paper.read_bytes()).decode()
    status, reply = _call(f"{base}/translate", {"fileName": "a.pdf", "fileContent": encoded, "next_service": "siliconflowfree"})
    assert status == 400 and "免费翻译通道" in reply["message"]
    status, reply = _call(f"{base}/translate", {"fileName": "a.pdf", "fileContent": base64.b64encode(b"not a pdf").decode(),
                                                "config": {"service": "pseudo"}})
    assert status == 400 and "不是 PDF" in reply["message"]
    status, reply = _call(f"{base}/translate", {"fileName": "a.pdf", "fileContent": encoded,
                                                "config": {"service": "pseudo", "mono": False, "dual": False}})
    assert status == 400 and "未选择输出" in reply["message"]
    assert _call(f"{base}/translate", {"x": 1}, {"Content-Type": "text/plain"})[0] == 400      # no "simple" CORS posts
    assert _call(f"{base}/health", headers={"Host": "evil.example"})[0] == 403                  # DNS rebinding
    assert _call(f"{base}/crop", {"fileName": "a.pdf"})[0] == 400


def test_failed_job_reports_the_reason(service):
    base, paper = service
    body = {"fileName": "a.pdf", "fileContent": base64.b64encode(paper.read_bytes()).decode(),
            "config": {"service": "openai", "api_key": ""}}
    _, accepted = _call(f"{base}/translate", body)
    task = _wait(base, accepted["taskId"])
    assert task["status"] == "failed" and "API Key" in task["error"]


def test_service_check_endpoint(service):
    base, _ = service
    status, reply = _call(f"{base}/api/test", {"config": {"service": "pseudo"}})
    assert status == 200 and reply["status"] == "success" and reply["translation"]
