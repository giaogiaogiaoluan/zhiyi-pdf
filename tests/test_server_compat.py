"""Private server naming/startup regressions using synthetic files and processes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "plugin/content/runtime" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compat = load_module("zhiyi_test_server_compat", "server_compat.py")
control = load_module("zhiyi_test_service_control", "service_control.py")


class PathTests(unittest.TestCase):
    def test_hidden_directory_and_internal_pdf_preserved(self):
        directory = "/tmp/.pdf2rea/v1.pdf.documents/server/translated"
        for engine, separator in (("pdf2zh_next", "."), ("pdf2zh", "-")):
            for name in ("paper.pdf", "paper.v2.pdf", "paper.pdf.notes.pdf", "论文.2026.pdf", "shortcut.pdf"):
                for output in ("origin-cut", "mono-cut", "dual-cut", "crop-compare", "compare"):
                    with self.subTest(engine=engine, name=name, output=output):
                        source = directory + "/" + name
                        actual = compat.safe_output_path(source, output, engine)
                        suffix = "cut" if output == "origin-cut" else output
                        self.assertEqual(actual, directory + "/" + name[:-4] + separator + suffix + ".pdf")
                        self.assertEqual(os.path.dirname(actual), directory)
                        self.assertNotEqual(actual, source)

    def test_mono_dual_and_layout_terminal_suffixes(self):
        directory = "/tmp/.pdf2rea/server/translated"
        for engine, separator in (("pdf2zh_next", "."), ("pdf2zh", "-")):
            cases = {
                "paper.no_watermark.zh-CN.mono.pdf": "paper.no_watermark.zh-CN.mono-cut.pdf",
                "paper-mono.pdf": "paper-mono-cut.pdf",
                "paper.no_watermark.zh-CN.dual.pdf": "paper.no_watermark.zh-CN.mono-cut.pdf",
                "paper-dual.pdf": "paper-mono-cut.pdf",
                "paper.no_watermark.zh-CN.LR_dual.pdf": "paper.no_watermark.zh-CN" + separator + "mono-cut.pdf",
                "paper.no_watermark.zh-CN.TB_dual.pdf": "paper.no_watermark.zh-CN" + separator + "mono-cut.pdf",
            }
            for name, expected in cases.items():
                with self.subTest(engine=engine, name=name):
                    self.assertEqual(compat.safe_output_path(directory + "/" + name, "mono-cut", engine), directory + "/" + expected)

    def test_reported_compare_target_stays_in_original_directory(self):
        source = "/tmp/test-user/.pdf2rea/server/translated/paper.pdf"
        self.assertEqual(compat.safe_output_path(source, "compare", "pdf2zh_next"), "/tmp/test-user/.pdf2rea/server/translated/paper.compare.pdf")

    def test_uppercase_extension_and_relative_filename(self):
        self.assertEqual(compat.safe_output_path("paper.PDF", "compare", "pdf2zh_next"), "paper.compare.pdf")

    def test_reject_input_overwrite_and_unsafe_type(self):
        for source, output in (("paper.mono.pdf", "mono"), ("paper.pdf", "../escape"), ("paper.txt", "compare")):
            with self.subTest(source=source, output=output), self.assertRaises(ValueError):
                compat.safe_output_path(source, output, "pdf2zh_next")


class HookTests(unittest.TestCase):
    def test_health_payload_preserved_and_hook_idempotent(self):
        class Translator:
            def get_filename_after_process(self, *args):
                raise AssertionError("old method must not run")

            def health_check(self):
                return {"status": "ok", "version": "4.1.7", "outputDir": "/tmp/.pdf2rea/server/translated"}, 200

        compat.install_compat(Translator)
        first = Translator.health_check
        compat.install_compat(Translator)
        self.assertIs(Translator.health_check, first)
        payload, status = Translator().health_check()
        self.assertEqual(status, 200)
        self.assertEqual(payload["version"], "4.1.7")
        self.assertEqual(payload["zhiyiCompatVersion"], "1.1.2")
        self.assertEqual(payload["pid"], os.getpid())

    def test_flask_response_tuple_shape_and_status_preserved(self):
        class Response:
            def get_json(self):
                return {"status": "ok", "outputDir": "/tmp/translated"}

            def set_data(self, value):
                self.data = value

        response = Response()

        class Translator:
            def get_filename_after_process(self, *args):
                pass

            def health_check(self):
                return response, 200, {"X-Test": "retained"}

        compat.install_compat(Translator)
        actual = Translator().health_check()
        self.assertIs(actual[0], response)
        self.assertEqual(actual[1:], (200, {"X-Test": "retained"}))
        self.assertEqual(json.loads(response.data)["zhiyiCompatVersion"], "1.1.2")

    def test_ast_requires_unique_supported_class(self):
        for source in ("x=1", "class PDFTranslator: pass", "class PDFTranslator: pass\nclass PDFTranslator: pass"):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                compat.compatibility_tree(source)


SYNTHETIC_SERVER = '''import argparse,json,os,sys
class PDFTranslator:
    def get_filename_after_process(self,inpath,outtype,engine):
        return inpath.replace('.pdf','.'+outtype+'.pdf')
    def health_check(self):
        return {'status':'ok','version':'4.1.7','outputDir':os.path.join(os.path.dirname(__file__),'translated')},200
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int)
    parser.add_argument('--host')
    parser.add_argument('--check_update')
    parser.add_argument('--enable_venv')
    args=parser.parse_args()
    instance=PDFTranslator()
    print(json.dumps({'file':__file__,'name':__name__,'port':args.port,'host':args.host,'health':instance.health_check()[0],
        'output':instance.get_filename_after_process(os.path.join(os.path.dirname(__file__),'translated','paper.pdf'),'compare','pdf2zh_next'),
        'config':os.environ['PDF2REA_CONFIG'],'cache':os.environ['PDF2REA_CACHE_DIR'],'pythonpath':os.environ.get('PYTHONPATH')}))
'''


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zhiyi-compat-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / ".pdf2rea/server"
        self.root.mkdir(parents=True)
        (self.root / "pdf2rea-runtime.json").write_text(json.dumps({"owner": compat.OWNER, "server_port": 8892}))
        (self.root / "server.py").write_text(SYNTHETIC_SERVER)

    def run_compat(self, *arguments):
        return subprocess.run([sys.executable, "-B", str(ROOT / "plugin/content/runtime/server_compat.py"), "--runtime", str(self.root), *arguments], capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PDF2REA_DISABLE="1", PYTHONPATH="untrusted-test-path"))


class RuntimeTests(RuntimeFixture):
    def test_executes_original_main_and_arguments_without_changing_source(self):
        source = self.root / "server.py"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.run_compat("--port", "8892")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual((data["port"], data["host"], data["name"]), (8892, "127.0.0.1", "__main__"))
        self.assertEqual(data["file"], str(source))
        self.assertEqual(data["output"], str(self.root / "translated/paper.compare.pdf"))
        self.assertEqual(data["health"]["zhiyiCompatVersion"], "1.1.2")
        self.assertEqual(data["config"], str(self.root / "config/pdf2rea.json"))
        self.assertEqual(data["cache"], str(self.root / "cache/babeldoc"))
        self.assertIsNone(data["pythonpath"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertFalse((self.root / "__pycache__").exists())

    def test_rejects_explicit_port_or_host_override(self):
        for arguments in (("--port=8890",), ("--port", "8893"), ("--host", "0.0.0.0")):
            with self.subTest(arguments=arguments):
                result = self.run_compat(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_abbreviated_upstream_options_cannot_override_private_binding(self):
        result = self.run_compat("--po", "8890", "--ho", "0.0.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual((data["port"], data["host"]), (8892, "127.0.0.1"))

    def test_rejects_unmarked_runtime_and_symbolic_server(self):
        marker = self.root / "pdf2rea-runtime.json"
        marker.write_text(json.dumps({"owner": "original-pdf2zh", "server_port": 8892}))
        self.assertNotEqual(self.run_compat().returncode, 0)
        marker.write_text(json.dumps({"owner": compat.OWNER, "server_port": 8892}))
        server = self.root / "server.py"
        server.unlink()
        server.symlink_to(Path(self.temp.name) / "original-server.py")
        self.assertNotEqual(self.run_compat().returncode, 0)


class ServiceControlTests(RuntimeFixture):
    # Reuse the synthetic runtime fixture, with every network/process operation
    # mocked. These tests never inspect, stop or start the real 8891 service.
    def setUp(self):
        super().setUp()
        python = self.root / "zotero-pdf2zh-next-venv/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        (self.root / "server_compat.py").touch()
        self.old = {"ok": True, "status": "running", "outputDir": str(self.root / "translated"), "zhiyiCompatVersion": ""}

    def test_busy_service_is_not_stopped(self):
        with mock.patch.object(control, "health", return_value=self.old), mock.patch.object(control, "active_tasks", return_value=[{"active": True}]), mock.patch.object(control.os, "kill") as kill, mock.patch.object(control.subprocess, "Popen") as start:
            result = control.control(self.root, "start")
        self.assertEqual(result["status"], "busy")
        kill.assert_not_called()
        start.assert_not_called()

    def test_unknown_pid_requires_manual_restart(self):
        with mock.patch.object(control, "health", return_value=self.old), mock.patch.object(control, "active_tasks", return_value=[]), mock.patch.object(control.os, "kill") as kill, mock.patch.object(control.subprocess, "Popen") as start:
            result = control.control(self.root, "start")
        self.assertEqual(result["status"], "requires_restart")
        kill.assert_not_called()
        start.assert_not_called()

    def test_unverifiable_task_list_never_stops_service(self):
        with mock.patch.object(control, "active_tasks", side_effect=ValueError("unknown")), mock.patch.object(control.os, "kill") as kill:
            result = control.stop_for_upgrade(self.root, "http://127.0.0.1:8892", 8892, self.old)
        self.assertEqual(result["status"], "requires_restart")
        kill.assert_not_called()

    def test_owned_pid_checks_exact_script_uid_port_and_health_pid(self):
        (self.root / "pdf2rea-service.pid").write_text("424242")
        correct = f"{os.getuid()} python -B {self.root}/server.py --port 8892 --host 127.0.0.1"
        for command, reported_pid, expected in ((correct, None, 424242), (correct, 777, None), (correct.replace(str(self.root), "/original/server"), None, None), (correct.replace("--port 8892", "--port 8890"), None, None)):
            result = subprocess.CompletedProcess([], 0, command, "")
            with self.subTest(command=command, reported_pid=reported_pid), mock.patch.object(control.subprocess, "run", return_value=result):
                self.assertEqual(control.owned_pid(self.root, 8892, dict(self.old, pid=reported_pid)), expected)

    def test_only_verified_idle_process_gets_sigterm(self):
        with mock.patch.object(control, "active_tasks", return_value=[]), mock.patch.object(control, "owned_pid", return_value=424242), mock.patch.object(control, "pid_exists", return_value=False), mock.patch.object(control.os, "kill") as kill:
            result = control.stop_for_upgrade(self.root, "http://127.0.0.1:8892", 8892, self.old)
        self.assertIsNone(result)
        kill.assert_called_once_with(424242, signal.SIGTERM)

    def test_current_compatible_service_is_reused(self):
        current = dict(self.old, zhiyiCompatVersion="1.1.2")
        with mock.patch.object(control, "health", return_value=current), mock.patch.object(control, "stop_for_upgrade") as stop, mock.patch.object(control.subprocess, "Popen") as start:
            self.assertEqual(control.control(self.root, "start"), current)
        stop.assert_not_called()
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
