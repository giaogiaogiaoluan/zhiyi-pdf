"""Translation backends.

``llm`` backends take a full prompt; machine-translation backends take one
paragraph at a time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time

import httpx

from ..config import Config

log = logging.getLogger(__name__)


class TranslationError(RuntimeError):
    pass


class _RateLimiter:
    def __init__(self, qps: float):
        self.interval = 1.0 / qps if qps > 0 else 0.0
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)


class Backend:
    name = "base"
    llm = False

    def identity(self) -> str:
        """Part of the cache key: results from different models never mix."""
        return self.name

    def complete(self, prompt: str) -> str:              # llm backends
        raise NotImplementedError

    def translate(self, text: str, lang_in: str, lang_out: str) -> str:   # MT backends
        raise NotImplementedError

    def close(self) -> None:
        pass


class OpenAIBackend(Backend):
    """Any OpenAI-compatible chat completions endpoint (DeepSeek, Ark, SiliconFlow, …)."""

    name = "openai"
    llm = True

    def __init__(self, config: Config):
        if not config.api_key:
            raise TranslationError("未配置 API Key：请在设置的“翻译服务”中填写，或改用谷歌翻译")
        self.model = config.model
        self.temperature = config.temperature
        self.url = config.base_url.rstrip("/") + "/chat/completions"
        self.client = httpx.Client(timeout=httpx.Timeout(180, connect=20),
                                   headers={"Authorization": f"Bearer {config.api_key}"})
        self.limiter = _RateLimiter(config.qps)

    def identity(self) -> str:
        return f"openai:{self.url}:{self.model}"

    def complete(self, prompt: str) -> str:
        body = {"model": self.model, "temperature": self.temperature,
                "messages": [{"role": "user", "content": prompt}]}
        last = ""
        for attempt in range(6):
            self.limiter.wait()
            try:
                response = self.client.post(self.url, json=body)
            except httpx.HTTPError as error:
                last = f"网络错误：{error!r}"
            else:
                if response.status_code == 200:
                    try:
                        content = response.json()["choices"][0]["message"]["content"] or ""
                    except (KeyError, IndexError, ValueError) as error:
                        raise TranslationError(f"翻译服务返回了无法解析的内容：{response.text[:200]}") from error
                    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
                last = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code in (400, 401, 403, 404):
                    raise TranslationError(f"翻译服务拒绝了请求（{last}）")
            time.sleep(min(60, 2 ** attempt + random.random()))
        raise TranslationError(f"翻译服务多次重试仍失败（{last}）")

    def close(self) -> None:
        self.client.close()


class GoogleBackend(Backend):
    """Keyless machine translation through Google's public web endpoint.

    Unofficial and rate-limited by Google; meant as a zero-configuration
    fallback. Quality is below an LLM, and style tags are not preserved.
    """

    name = "google"
    URL = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, config: Config):
        self.client = httpx.Client(timeout=30)
        self.limiter = _RateLimiter(min(config.qps, 4))

    def translate(self, text: str, lang_in: str, lang_out: str) -> str:
        pieces = _split_long(text, 1800)
        return " ".join(self._one(piece, lang_in, lang_out) for piece in pieces)

    def _one(self, text: str, lang_in: str, lang_out: str, retry: bool = True) -> str:
        last = ""
        for attempt in range(5):
            self.limiter.wait()
            try:
                response = self.client.post(self.URL, params={"client": "gtx", "sl": lang_in, "tl": lang_out, "dt": "t"},
                                            data={"q": text})
                if response.status_code == 200:
                    parts = [part for part in response.json()[0] if part and part[0]]
                    out = []
                    for part in parts:
                        echoed = (len(part) > 1 and isinstance(part[1], str) and part[0].strip() == part[1].strip()
                                  and len(re.findall(r"[A-Za-z]{3,}", part[0])) >= 4)
                        out.append(self._one(part[0], lang_in, lang_out, retry=False) if echoed and retry else part[0])
                    return "".join(out)
                last = f"HTTP {response.status_code}"
            except (httpx.HTTPError, ValueError, TypeError, IndexError) as error:
                last = repr(error)
            time.sleep(min(30, 2 ** attempt + random.random()))
        raise TranslationError(f"谷歌翻译请求失败（{last}）")

    def close(self) -> None:
        self.client.close()


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
        if current and len(current) + len(sentence) + 1 > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


class PseudoBackend(Backend):
    """Deterministic stand-in for tests: keeps markers, replaces words with CJK."""

    name = "pseudo"
    _POOL = "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经"

    def translate(self, text: str, lang_in: str, lang_out: str) -> str:
        from ..markup import MARKER

        out, position = [], 0

        def convert(chunk: str) -> str:
            def word(match: re.Match) -> str:
                token = match.group(0)
                if len(token) <= 2 or token.isupper():
                    return token
                digest = hashlib.md5(token.lower().encode()).digest()
                count = max(1, round(len(token) / 3.2))
                return "".join(self._POOL[digest[k % 16] % len(self._POOL)] for k in range(count))
            chunk = re.sub(r"[A-Za-z][A-Za-z'’-]*", word, chunk)
            chunk = re.sub(r"(?<=[一-鿿]) (?=[一-鿿])", "", chunk)
            return chunk.replace(". ", "。").replace(", ", "，").rstrip(".") + ("。" if chunk.rstrip().endswith(".") else "")

        for match in MARKER.finditer(text):
            out.append(convert(text[position:match.start()]))
            out.append(match.group(0))
            position = match.end()
        out.append(convert(text[position:]))
        return "".join(out)


class FileBackend(Backend):
    """Translations supplied in a JSON file: {"<source text>": "<translation>"}."""

    name = "file"

    def __init__(self, config: Config):
        with open(config.translations_file, encoding="utf-8") as handle:
            self.table = {" ".join(k.split()): v for k, v in json.load(handle).items()}

    def translate(self, text: str, lang_in: str, lang_out: str) -> str:
        return self.table.get(" ".join(text.split()), text)


def make_backend(config: Config) -> Backend:
    if config.service == "openai":
        return OpenAIBackend(config)
    if config.service == "google":
        return GoogleBackend(config)
    if config.service == "pseudo":
        return PseudoBackend()
    if config.service == "file":
        return FileBackend(config)
    raise TranslationError(f"未知的翻译服务：{config.service}")
