"""Local HTTP service used by the Zotero plugin.

Endpoints (same shape the plugin has always used):

    GET  /health                    liveness and version
    POST /translate                 submit a job  → {"status": "accepted", "taskId": …}
    GET  /api/tasks                 running and queued jobs
    GET  /api/history               finished jobs
    GET  /translatedFile/<name>     download a result
    POST /api/test                  check a translation service configuration

The service binds to loopback only, accepts JSON bodies only (so a web page
cannot drive it with a "simple" cross-origin request) and rejects requests
whose Host header is not local.
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import re
import shutil
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__, assets
from .config import Config

log = logging.getLogger(__name__)

MAX_BODY = 400 * 1024 * 1024
KEEP_DAYS = 7
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
_STAGES = {"analyze": "分析版面", "translate": "翻译", "typeset": "排版", "render": "写入 PDF"}
# Services the old plugin could select; every LLM one speaks the OpenAI protocol.
_DEFAULT_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "silicon": "https://api.siliconflow.cn/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "aliyundashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "modelscope": "https://api-inference.modelscope.cn/v1",
    "ollama": "http://localhost:11434/v1",
}


def _truthy(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "yes", "on")


def config_from_request(body: dict) -> Config:
    """Build a Config from the plugin's request (new or legacy field names)."""
    if isinstance(body.get("config"), dict):
        return Config.from_dict(body["config"])
    data: dict = {}
    if body.get("sourceLang"):
        data["lang_in"] = str(body["sourceLang"])
    if body.get("targetLang"):
        data["lang_out"] = str(body["targetLang"])
    service = str(body.get("next_service") or body.get("service") or "").lower()
    api = body.get("llm_api") if isinstance(body.get("llm_api"), dict) else None
    if service in ("google", "bing"):
        data["service"] = "google"
    elif service == "siliconflowfree":
        raise ValueError("上游的免费翻译通道只对 pdf2zh 官方流程开放，知译 PDF 2 无法使用。"
                         "请在“翻译服务”中改用自己的 API（如 DeepSeek），或选择“谷歌翻译（免密钥）”。")
    else:
        data["service"] = "openai"
        if api:
            data["api_key"] = str(api.get("apiKey") or "")
            data["model"] = str(api.get("model") or "") or None
            data["base_url"] = str(api.get("apiUrl") or "") or _DEFAULT_URLS.get(service)
        elif service in _DEFAULT_URLS:
            data["base_url"] = _DEFAULT_URLS[service]
    for source, target in (("mono", "mono"), ("dual", "dual")):
        if source in body:
            data[target] = _truthy(body[source])
    if body.get("dualMode") in ("LR", "TB"):
        data["dual_mode"] = body["dualMode"]
    if "translateTableText" in body:
        data["translate_tables"] = _truthy(body["translateTableText"])
    for source, target, cast in (("threadNum", "threads", int), ("qps", "qps", float)):
        try:
            if body.get(source) not in (None, ""):
                data[target] = cast(body[source])
        except (TypeError, ValueError):
            pass
    if body.get("glossaryPath"):
        data["glossary_path"] = str(body["glossaryPath"])
    return Config.from_dict(data)


class Task:
    def __init__(self, file_name: str, source: Path, out_dir: Path, config: Config):
        self.id = uuid.uuid4().hex[:12]
        self.file_name, self.source, self.out_dir, self.config = file_name, source, out_dir, config
        self.status = "queued"
        self.message = "排队中"
        self.percent = 0
        self.error = ""
        self.files: list[Path] = []
        self.report: dict | None = None
        self.created = time.time()
        self.finished_at = 0.0

    def view(self) -> dict:
        return {
            "taskId": self.id, "fileName": self.file_name, "status": self.status, "message": self.message,
            "percent": self.percent, "finished": self.status in ("success", "failed"), "error": self.error,
            "fileList": [f.name for f in self.files], "filePaths": [str(f) for f in self.files],
            "outputDir": str(self.out_dir), "created": self.created, "finishedAt": self.finished_at,
            "summary": _summary(self.report),
        }


def _summary(report: dict | None) -> dict | None:
    if not report:
        return None
    layout = report["layout"]
    return {
        "units": report["analysis"]["units"], "chained": report["analysis"]["chained"],
        "formulas": report["analysis"]["inline_formulas"], "shrunk": layout["shrunk"] + layout["shrunk_hard"],
        "keptOriginal": layout["kept_original"], "failed": report["translation"]["failed"],
        "seconds": report["seconds"]["total"],
    }


class Service:
    def __init__(self):
        self.root = assets.home() / "translated"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks: dict[str, Task] = {}
        self.order: list[str] = []
        self.queue: "queue.Queue[Task]" = queue.Queue()
        self.lock = threading.Lock()
        self._purge()
        threading.Thread(target=self._work, daemon=True, name="zhiyi-worker").start()

    def _purge(self) -> None:
        cutoff = time.time() - KEEP_DAYS * 86400
        for child in self.root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass

    def submit(self, body: dict) -> Task:
        name = Path(str(body.get("fileName") or "document.pdf")).name
        name = re.sub(r"[\x00-\x1f/\\:]", "_", name) or "document.pdf"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        try:
            content = base64.b64decode(body.get("fileContent") or "", validate=False)
        except (ValueError, TypeError) as error:
            raise ValueError("文件内容不是有效的 base64") from error
        if b"%PDF-" not in content[:1024]:
            raise ValueError("上传的文件不是 PDF")
        config = config_from_request(body)
        if not (config.mono or config.dual):
            raise ValueError("未选择输出：请至少勾选“纯译文”或“双语对照”")
        task_dir = self.root / uuid.uuid4().hex[:12]
        task_dir.mkdir(parents=True)
        source = task_dir / name
        source.write_bytes(content)
        task = Task(name, source, task_dir, config)
        with self.lock:
            self.tasks[task.id] = task
            self.order.append(task.id)
            for old in self.order[:-60]:                 # bounded history
                self.tasks.pop(old, None)
            self.order = self.order[-60:]
        self.queue.put(task)
        return task

    def _work(self) -> None:
        from .pipeline import translate_pdf

        while True:
            task = self.queue.get()
            task.status, task.message = "running", "准备中"
            spans = {"analyze": (0, 20), "translate": (20, 85), "typeset": (85, 90), "render": (90, 100)}

            def progress(stage: str, done: int, total: int, task=task) -> None:
                low, high = spans.get(stage, (0, 100))
                task.percent = int(low + (high - low) * done / max(1, total))
                task.message = f"{_STAGES.get(stage, stage)} {done}/{total}"

            try:
                report = translate_pdf(task.source, task.out_dir, task.config, progress)
                task.report = report
                task.files = [Path(report["outputs"][kind]) for kind in ("mono", "dual") if kind in report["outputs"]]
                task.status, task.percent = "success", 100
                shrunk = report["layout"]["shrunk"] + report["layout"]["shrunk_hard"]
                task.message = f"完成：{report['analysis']['units']} 段，用时 {report['seconds']['total']} 秒" + (
                    f"，{shrunk} 段单独缩小了字号" if shrunk else "")
            except Exception as error:                   # report, never crash the worker
                log.exception("task %s failed", task.id)
                task.status, task.error = "failed", str(error) or error.__class__.__name__
                task.message = f"失败：{task.error}"
            task.finished_at = time.time()

    def views(self, finished: bool) -> list[dict]:
        with self.lock:
            tasks = [self.tasks[i] for i in self.order if i in self.tasks]
        return [t.view() for t in tasks if (t.status in ("success", "failed")) == finished]

    def result_file(self, name: str) -> Path | None:
        with self.lock:
            tasks = [self.tasks[i] for i in reversed(self.order) if i in self.tasks]
        for task in tasks:
            for path in task.files:
                if path.name == name and path.is_file():
                    return path
        return None


def test_service(body: dict) -> dict:
    """Translate one short sentence with the given configuration."""
    from .translate import make_backend
    from .translate.session import build_prompt, parse_reply
    from .model import Unit

    config = config_from_request(body)
    backend = make_backend(config)
    try:
        sample = "Knowledge graphs organise entities and the relations between them."
        if backend.llm:
            unit = Unit(id=1, blocks=[], role="body", source=sample)
            reply = parse_reply(backend.complete(build_prompt([unit], config, {}, "")), 1).get(1, "")
        else:
            reply = backend.translate(sample, config.lang_in, config.lang_out)
    finally:
        backend.close()
    if not reply.strip():
        raise ValueError("翻译服务返回了空结果")
    return {"status": "success", "service": config.service, "model": config.model if backend.llm else "",
            "sample": sample, "translation": reply}


class Handler(BaseHTTPRequestHandler):
    server_version = f"ZhiYi/{__version__}"
    service: Service = None                               # set by serve()

    def log_message(self, format, *args):                 # noqa: A002 - stdlib signature
        log.info("%s %s", self.address_string(), format % args)

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        return host in _LOCAL_HOSTS

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        if "application/json" not in (self.headers.get("Content-Type") or "").lower():
            raise ValueError("请求必须是 application/json")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            raise ValueError("请求体为空或过大")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return body

    def do_GET(self):                                     # noqa: N802 - stdlib name
        if not self._local():
            return self._send(403, {"status": "error", "message": "forbidden"})
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"status": "ok", "name": "zhiyi", "version": __version__,
                                    "home": str(assets.home())})
        if path == "/api/tasks":
            return self._send(200, {"tasks": self.service.views(finished=False)})
        if path == "/api/history":
            return self._send(200, {"history": self.service.views(finished=True)})
        if path.startswith("/translatedFile/"):
            name = Path(unquote(path[len("/translatedFile/"):])).name
            found = self.service.result_file(name)
            if found is None:
                return self._send(404, {"status": "error", "message": "文件不存在或已清理"})
            data = found.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        return self._send(404, {"status": "error", "message": "not found"})

    def do_POST(self):                                    # noqa: N802 - stdlib name
        if not self._local():
            return self._send(403, {"status": "error", "message": "forbidden"})
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/translate":
                task = self.service.submit(body)
                return self._send(200, {"status": "accepted", "taskId": task.id})
            if path == "/api/test":
                return self._send(200, test_service(body))
            if path in ("/crop", "/compare", "/crop-compare"):
                raise ValueError("知译 PDF 2 已把双语对照并入“翻译 PDF”（勾选“双语对照”即可），不再提供单独的裁剪/对照任务")
            return self._send(404, {"status": "error", "message": "not found"})
        except (ValueError, json.JSONDecodeError) as error:
            return self._send(400, {"status": "error", "message": str(error)})
        except Exception as error:                        # surface as a clean message
            log.exception("request failed")
            return self._send(500, {"status": "error", "message": str(error) or error.__class__.__name__})


def make_server(host: str = "127.0.0.1", port: int = 8893) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("出于安全考虑，服务只能监听本机回环地址")
    Handler.service = Service()
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve(host: str = "127.0.0.1", port: int = 8893) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(host, port)
    print(f"知译 PDF {__version__} 服务已启动：http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
