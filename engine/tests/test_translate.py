import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from zhiyi.config import Config
from zhiyi.model import Block, Inline, Style, Unit
from zhiyi.translate import TranslationError, translate_units
from zhiyi.translate.backends import Backend, OpenAIBackend
from zhiyi.translate.session import build_prompt, make_batches, parse_glossary, parse_reply


def _unit(number, source, role="body", inlines=(), styles=()):
    unit = Unit(id=number, blocks=[Block(page=0, lines=[])], role=role, source=source)
    for key in inlines:
        unit.inlines[key] = Inline(key, 0, (0, 0, 1, 1), 1, 10)
    for name in styles:
        unit.styles[name] = Style(color=255)
    return unit


class FakeLLM(Backend):
    """Echoes every item as 译<n>, optionally skipping some on the first pass."""

    name, llm = "fake", True

    def __init__(self, skip=()):
        self.skip, self.prompts = set(skip), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        if prompt.startswith("Below is the beginning"):
            return "knowledge graph => 知识图谱\nFoodKG => FoodKG\n"
        body = prompt.split("\nItems (", 1)[1]
        items = [chunk.split("\n", 1) for chunk in body.split("[[")[1:]]
        out = []
        for head, text in items:
            number = head.split("]]")[0]
            text = text.strip()
            if text in self.skip:
                self.skip.discard(text)
                continue
            out.append(f"[[{number}]]\n译：{text}")
        return "```\n" + "\n".join(out) + "\n```"


def test_parse_reply_is_tolerant():
    reply = "Sure!\n[[1]]\n第一段\n第二行\n[[2]]\n\n第二段 {v1}\n[[9]]\n多余"
    assert parse_reply(reply, 2) == {1: "第一段 第二行", 2: "第二段 {v1}"}
    assert parse_reply("只有一段没有编号", 1) == {1: "只有一段没有编号"}
    assert parse_reply("没有编号", 3) == {}


def test_batches_respect_size_limits():
    units = [_unit(n, "x" * 1500) for n in range(1, 8)]
    batches = make_batches(units)
    assert [len(b) for b in batches] == [2, 2, 2, 1]
    assert [u.id for b in batches for u in b] == list(range(1, 8))


def test_prompt_carries_glossary_context_and_kinds():
    units = [_unit(1, "A knowledge graph {v1} helps.", inlines=("{v1}",)), _unit(2, "Results", role="heading")]
    prompt = build_prompt(units, Config(), {"knowledge graph": "知识图谱", "unused": "无关"}, "previous text")
    assert "knowledge graph => 知识图谱" in prompt and "unused" not in prompt
    assert "previous text" in prompt and "1: paragraph, 2: section heading" in prompt
    assert "[[1]]\nA knowledge graph {v1} helps." in prompt and "Simplified Chinese" in prompt


def test_llm_session_translates_retries_and_repairs(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIYI_HOME", str(tmp_path))
    units = [_unit(1, "First {v1} paragraph.", inlines=("{v1}",)), _unit(2, "Second paragraph."),
             _unit(3, "See [<a>3</a>].", styles=("a",))]
    backend = FakeLLM(skip={"Second paragraph."})
    stats = translate_units(units, backend, Config(auto_glossary=False, cache=False, threads=1))
    assert [u.translation for u in units] == ["译：First {v1} paragraph.", "译：Second paragraph.", "译：See [<a>3</a>]."]
    assert stats["retried"] == 1 and stats["failed"] == 0
    assert len(backend.prompts) == 2                     # one batch, one single-item retry


def test_auto_glossary_feeds_later_prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIYI_HOME", str(tmp_path))
    units = [_unit(n, "A knowledge graph links entities. " * 14) for n in (1, 2)]
    backend = FakeLLM()
    stats = translate_units(units, backend, Config(cache=False, threads=1))
    assert stats["glossary"] == 1                        # the untranslated "FoodKG" entry is dropped
    assert any("knowledge graph => 知识图谱" in p for p in backend.prompts[1:])
    assert parse_glossary("- term one => 术语一\nnoise line\nSame => same") == {"term one": "术语一"}


def test_cache_avoids_a_second_request(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIYI_HOME", str(tmp_path))
    config = Config(auto_glossary=False, threads=1)
    first = FakeLLM()
    translate_units([_unit(1, "Cached paragraph.")], first, config)
    second = FakeLLM()
    units = [_unit(1, "Cached paragraph.")]
    stats = translate_units(units, second, config)
    assert stats["cached"] == 1 and not second.prompts and units[0].translation == "译：Cached paragraph."


class _MockOpenAI(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append((self.path, self.headers.get("Authorization"), body))
        if len(type(self).calls) == 1:                   # first call: rate limited
            self.send_response(429)
            self.end_headers()
            return
        data = json.dumps({"choices": [{"message": {"content": "<think>hmm</think>[[1]]\n你好"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_openai_backend_protocol_and_retry(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockOpenAI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = Config(base_url=f"http://127.0.0.1:{server.server_address[1]}/v1/", api_key="test-key", model="m1", qps=0)
        backend = OpenAIBackend(config)
        assert backend.complete("hello") == "[[1]]\n你好"
        path, auth, body = _MockOpenAI.calls[-1]
        assert path == "/v1/chat/completions" and auth == "Bearer test-key"
        assert body["model"] == "m1" and body["messages"] == [{"role": "user", "content": "hello"}]
        assert len(_MockOpenAI.calls) == 2
    finally:
        server.shutdown()


def test_missing_api_key_is_a_clear_error():
    with pytest.raises(TranslationError, match="API Key"):
        OpenAIBackend(Config(api_key=""))


def test_untranslated_clauses_are_detected():
    from zhiyi.translate.session import untranslated
    source = "PAGP integrates inputs. Historical CGM, physical activity, and medication records are treated as series."
    assert untranslated(source, "PAGP 集成了输入。Historical CGM, physical activity, and medication records are treated", "zh-CN")
    assert not untranslated(source, "PAGP 集成了输入。历史 CGM、身体活动和用药记录被视为时间序列。", "zh-CN")
    assert not untranslated(source, source, "de")              # only checked for CJK targets


class EchoOnce(Backend):
    name = "echo"

    def __init__(self):
        self.calls = 0

    def translate(self, text, lang_in, lang_out):
        self.calls += 1
        return text if self.calls == 1 else "已翻译的句子。"


def test_machine_translation_retries_an_echoed_result(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIYI_HOME", str(tmp_path))
    units = [_unit(1, "This whole sentence came back from the service without any translation at all.")]
    backend = EchoOnce()
    stats = translate_units(units, backend, Config(threads=1))
    assert units[0].translation == "已翻译的句子。" and backend.calls == 2 and stats["retried"] == 1
