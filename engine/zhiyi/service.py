"""Start, stop and inspect the background service.

``python -m zhiyi service start|stop|status [--port N]`` prints one JSON
object, which the Zotero plugin parses. The service runs detached from the
caller, so it survives the plugin's helper process.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

from . import __version__, assets

DEFAULT_PORT = 8893
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via a proxy


def _get(port: int, path: str, timeout: float = 2.0) -> dict | None:
    try:
        with _opener.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def _pid_file(port: int):
    return assets.home() / f"service-{port}.pid"


def _our_process(pid: int) -> bool:
    try:
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                                 timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "zhiyi" in command and "--serve" in command


def status(port: int = DEFAULT_PORT) -> dict:
    health = _get(port, "/health")
    if health is None:
        return {"ok": True, "running": False, "port": port}
    if health.get("name") != "zhiyi":
        return {"ok": False, "running": False, "port": port,
                "message": f"端口 {port} 已被其他程序占用（可能是旧版翻译服务），请在设置中换一个端口"}
    tasks = (_get(port, "/api/tasks") or {}).get("tasks", [])
    return {"ok": True, "running": True, "port": port, "version": health.get("version"),
            "current": health.get("version") == __version__, "busy": bool(tasks)}


def stop(port: int = DEFAULT_PORT) -> dict:
    pid_file = _pid_file(port)
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return {"ok": True, "stopped": False, "message": "没有记录在案的服务进程"}
    if not _our_process(pid):
        pid_file.unlink(missing_ok=True)
        return {"ok": True, "stopped": False, "message": "记录的进程已不存在"}
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not _our_process(pid):
            break
        time.sleep(0.1)
    pid_file.unlink(missing_ok=True)
    return {"ok": True, "stopped": True}


def start(port: int = DEFAULT_PORT, wait: float = 30.0) -> dict:
    current = status(port)
    if not current["ok"]:
        return current
    if current["running"]:
        if current["current"]:
            return {**current, "started": False}
        if current["busy"]:
            return {**current, "ok": False, "message": "旧版本服务仍有任务在运行，请稍后再试"}
        stop(port)                                    # upgrade in place
    home = assets.home()
    home.mkdir(parents=True, exist_ok=True)
    log = open(home / "service.log", "ab")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen([sys.executable, "-m", "zhiyi", "--serve", "--port", str(port)],
                               stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                               start_new_session=True, close_fds=True)
    _pid_file(port).write_text(str(process.pid))
    deadline = time.time() + wait
    while time.time() < deadline:
        if process.poll() is not None:
            return {"ok": False, "running": False, "port": port,
                    "message": f"服务启动失败，详见日志 {home / 'service.log'}"}
        health = _get(port, "/health", 1.0)
        if health and health.get("name") == "zhiyi":
            return {"ok": True, "running": True, "started": True, "port": port, "version": __version__,
                    "pid": process.pid}
        time.sleep(0.25)
    return {"ok": False, "running": False, "port": port, "message": "服务启动超时"}


def prepare() -> dict:
    """Download the layout model and fonts ahead of the first translation."""
    from .fonts import _FAMILIES

    missing = []
    if assets.model_path() is None:
        missing.append(assets.MODEL_NAME)
    for family in _FAMILIES.values():
        for name in family.values():
            if assets.font_path(name) is None:
                missing.append(name)
    return {"ok": not missing, "missing": missing, "home": str(assets.home())}


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="zhiyi service")
    parser.add_argument("action", choices=["start", "stop", "status", "prepare"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        result = prepare()
    else:
        result = {"start": start, "stop": stop, "status": status}[args.action](args.port)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1
