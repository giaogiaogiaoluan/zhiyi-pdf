"""Translate logical paragraphs: batching, prompts, parsing, cache."""

from __future__ import annotations

import csv
import hashlib
import logging
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from .. import assets, markup
from ..config import Config
from ..model import Unit
from .backends import Backend, TranslationError

log = logging.getLogger(__name__)

PROMPT_VERSION = "2"
BATCH_CHARS = 3200
BATCH_UNITS = 14
_ID_LINE = re.compile(r"^\s*\[\[(\d+)\]\]\s*$", re.M)
_KIND = {"title": "paper title", "heading": "section heading", "caption": "figure/table caption",
         "table": "table cell", "footnote": "footnote", "list": "list item", "body": "paragraph"}

PROMPT = """You are a professional translator of academic papers. Translate the numbered items below from {lang_in} into {lang_out}.

Rules:
1. Reply with the translations only, in this exact layout, one block per item and in the same order:
[[1]]
<translation of item 1>
[[2]]
<translation of item 2>
2. Markers such as {{v1}} and {{eq1}} stand for formulas. Copy every marker exactly once into its translation, at the position where it belongs in the translated sentence. Never translate, drop or invent markers.
3. Tags such as <b>…</b>, <i>…</i>, <sup>…</sup>, <sub>…</sub>, <a>…</a> mark formatting. Keep each tag pair around the corresponding translated words.
4. Translate faithfully and completely, in fluent academic {lang_out}. An item is one whole paragraph: translate it as connected prose, not sentence by sentence.
5. Keep citation marks ([12], (Smith et al., 2020)), numbers, units, code, URLs, e-mail addresses and personal names unchanged. On first use, a domain abbreviation may keep its original form in parentheses.
6. Do not add explanations, notes or line breaks inside an item.
{glossary}{context}
Items ({kinds}):
{items}"""

GLOSSARY_PROMPT = """Below is the beginning of an academic paper. List up to {count} key domain terms (technical terms, method names, recurring noun phrases) with the {lang_out} translation that should be used consistently throughout the paper. Abbreviations and proper names that should stay in the original language must be omitted.

Reply with one term per line, exactly as:
source term => translation

Paper excerpt:
{text}"""


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #
class Cache:
    def __init__(self, enabled: bool):
        self.db = None
        self.lock = threading.Lock()
        if not enabled:
            return
        path = assets.home() / "cache" / "translations.db"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path), check_same_thread=False)
            self.db.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        except (OSError, sqlite3.Error) as error:
            log.warning("translation cache disabled: %s", error)
            self.db = None

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        if self.db is None:
            return None
        with self.lock:
            row = self.db.execute("SELECT v FROM t WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        if self.db is None:
            return
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO t VALUES (?, ?)", (key, value))
            self.db.commit()


# --------------------------------------------------------------------------- #
# glossary
# --------------------------------------------------------------------------- #
def load_glossary(config: Config) -> dict[str, str]:
    terms = dict(config.extra_terms)
    if config.glossary_path:
        try:
            with open(config.glossary_path, encoding="utf-8-sig", newline="") as handle:
                for row in csv.reader(handle):
                    if len(row) >= 2 and row[0].strip() and row[1].strip() and row[0].strip().lower() != "source":
                        terms[row[0].strip()] = row[1].strip()
        except OSError as error:
            log.warning("glossary not loaded: %s", error)
    return terms


def parse_glossary(reply: str) -> dict[str, str]:
    terms: dict[str, str] = {}
    for line in reply.splitlines():
        if "=>" not in line:
            continue
        source, target = (part.strip(" \t-*•`\"'") for part in line.split("=>", 1))
        if 2 <= len(source) <= 60 and 1 <= len(target) <= 40 and source.lower() != target.lower():
            terms[source] = target
    return terms


def auto_glossary(units: list[Unit], backend: Backend, config: Config, cache: Cache) -> dict[str, str]:
    text, total = [], 0
    for unit in units:
        if unit.role in ("title", "heading", "body"):
            plain = markup.plain_text(unit.source)
            text.append(plain)
            total += len(plain)
            if total > 7000:
                break
    excerpt = "\n".join(text)[:7000]
    if len(excerpt) < 400:
        return {}
    prompt = GLOSSARY_PROMPT.format(count=30, lang_out=config.lang_out_name, text=excerpt)
    key = cache.key("glossary", backend.identity(), config.lang_out, PROMPT_VERSION, excerpt)
    reply = cache.get(key)
    if reply is None:
        try:
            reply = backend.complete(prompt)
        except TranslationError as error:
            log.warning("auto glossary skipped: %s", error)
            return {}
        cache.put(key, reply)
    return parse_glossary(reply)


def relevant_terms(glossary: dict[str, str], text: str) -> list[tuple[str, str]]:
    lowered = text.lower()
    return [(s, t) for s, t in glossary.items() if s.lower() in lowered]


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #
def build_prompt(batch: list[Unit], config: Config, glossary: dict[str, str], context: str) -> str:
    items = "\n".join(f"[[{n}]]\n{unit.source}" for n, unit in enumerate(batch, 1))
    terms = relevant_terms(glossary, " ".join(u.source for u in batch))
    glossary_text = ""
    if terms:
        glossary_text = "\nGlossary (use these translations consistently):\n" + "\n".join(f"- {s} => {t}" for s, t in terms[:40]) + "\n"
    context_text = f"\nFor context only, the text preceding item 1 (do not translate it):\n{context}\n" if context else ""
    kinds = ", ".join(f"{n}: {_KIND.get(unit.role, 'paragraph')}" for n, unit in enumerate(batch, 1))
    return PROMPT.format(lang_in=config.lang_in_name, lang_out=config.lang_out_name, glossary=glossary_text,
                         context=context_text, kinds=kinds, items=items)


def parse_reply(reply: str, count: int) -> dict[int, str]:
    """Map item number → translation; tolerant of code fences and stray text."""
    reply = re.sub(r"^```[a-z]*\s*|\s*```$", "", reply.strip())
    result: dict[int, str] = {}
    matches = list(_ID_LINE.finditer(reply))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(reply)
        number = int(match.group(1))
        text = " ".join(reply[match.end():end].split())
        if 1 <= number <= count and text and number not in result:
            result[number] = text
    if not result and count == 1 and reply:
        result[1] = " ".join(reply.split())
    return result


def make_batches(units: list[Unit]) -> list[list[Unit]]:
    batches, current, size = [], [], 0
    for unit in units:
        length = len(unit.source)
        if current and (size + length > BATCH_CHARS or len(current) >= BATCH_UNITS):
            batches.append(current)
            current, size = [], 0
        current.append(unit)
        size += length
    if current:
        batches.append(current)
    return batches


# --------------------------------------------------------------------------- #
# machine-translation markup
# --------------------------------------------------------------------------- #
def to_mt(source: str) -> tuple[str, dict[str, str], list[tuple[str, str]]]:
    """Flatten markup for engines that would mangle tags.

    Superscripts/subscripts become ``{sN}`` placeholders (restored verbatim);
    coloured spans are remembered so identical text can be re-coloured.
    """
    held: dict[str, str] = {}

    def hold(match: re.Match) -> str:
        key = f"{{s{len(held) + 1}}}"
        held[key] = match.group(0)
        return key

    text = re.sub(r"<(sup|sub)>.*?</\1>", hold, source)
    links = [(m.group(1), m.group(2)) for m in re.finditer(r"<(a\d?)>(.*?)</\1>", text)]
    text = re.sub(r"</?(?:b|i|a\d?)>", "", text)
    return re.sub(r"\s+", " ", text).strip(), held, links


def from_mt(text: str, held: dict[str, str], links: list[tuple[str, str]]) -> str:
    text = re.sub(r"[{｛]\s*s\s*(\d+)\s*[}｝]", lambda m: held.pop(f"{{s{m.group(1)}}}", ""), text)
    text += "".join(held.values())                    # never lose a mark
    position = 0
    for tag, inner in links:                         # re-colour spans that survived verbatim
        inner = inner.strip()
        if not inner or not re.fullmatch(r"[\w\s,.\-–]+", inner):
            continue
        found = text.find(inner, position)
        if found < 0:
            continue
        wrapped = f"<{tag}>{inner}</{tag}>"
        text = text[:found] + wrapped + text[found + len(inner):]
        position = found + len(wrapped)
    return text


# --------------------------------------------------------------------------- #
# quality check
# --------------------------------------------------------------------------- #
_WORD_RUN = re.compile(r"(?:[A-Za-z][A-Za-z'’-]*[ ,;]+){5,}[A-Za-z][A-Za-z'’-]*")


def untranslated(source: str, translation: str, lang_out: str) -> bool:
    """True when a CJK translation still carries a whole English clause verbatim."""
    if not lang_out.lower().startswith(("zh", "ja", "ko")):
        return False
    plain_source = " ".join(markup.plain_text(source).split())
    for match in _WORD_RUN.finditer(markup.plain_text(translation)):
        clause = " ".join(match.group(0).split())
        if len(clause) >= 30 and clause in plain_source:
            return True
    return False


# --------------------------------------------------------------------------- #
# session
# --------------------------------------------------------------------------- #
def translate_units(units: list[Unit], backend: Backend, config: Config, progress=None) -> dict:
    """Fill ``unit.translation`` for every unit. Returns statistics."""
    cache = Cache(config.cache)
    stats = {"cached": 0, "requests": 0, "retried": 0, "failed": 0, "glossary": 0}
    done = [0]
    lock = threading.Lock()

    def tick(count: int = 1) -> None:
        with lock:
            done[0] += count
            if progress:
                progress("translate", done[0], len(units))

    def accept(unit: Unit, text: str, key: str | None) -> None:
        unit.translation, notes = markup.repair(unit, text)
        unit.notes.extend(notes)
        if untranslated(unit.source, text, config.lang_out):
            unit.notes.append("译文中仍有整句英文未翻译")
        elif key:
            cache.put(key, text)                     # only clean results are remembered

    if not backend.llm:
        def one(unit: Unit) -> None:
            source, held, links = to_mt(unit.source)
            cacheable = backend.name not in ("pseudo", "file")
            key = cache.key(backend.identity(), config.lang_in, config.lang_out, source)
            hit = cache.get(key) if cacheable else None
            try:
                if hit is not None and untranslated(source, hit, config.lang_out):
                    hit = None                           # a stale half-translated result
                if hit is None:
                    for attempt in range(2):
                        with lock:
                            stats["requests"] += 1
                            stats["retried"] += attempt
                        hit = backend.translate(source, config.lang_in, config.lang_out)
                        if not untranslated(source, hit, config.lang_out):
                            if cacheable:
                                cache.put(key, hit)
                            break
                else:
                    with lock:
                        stats["cached"] += 1
                accept(unit, from_mt(hit, held, links), None)
            except TranslationError as error:
                unit.notes.append(f"翻译失败，保留原文：{error}")
                stats["failed"] += 1
            tick()

        with ThreadPoolExecutor(max_workers=max(1, config.threads)) as pool:
            list(pool.map(one, units))
        return stats

    glossary = load_glossary(config)
    if config.auto_glossary:
        found = auto_glossary(units, backend, config, cache)
        stats["glossary"] = len(found)
        glossary = {**found, **glossary}             # the user's terms win

    def unit_key(unit: Unit) -> str:
        terms = "|".join(f"{s}={t}" for s, t in relevant_terms(glossary, unit.source))
        return cache.key(backend.identity(), config.lang_in, config.lang_out, PROMPT_VERSION, terms, unit.source)

    pending: list[Unit] = []
    keys: dict[int, str] = {}
    for unit in units:
        keys[unit.id] = unit_key(unit)
        hit = cache.get(keys[unit.id])
        if hit is not None and not untranslated(unit.source, hit, config.lang_out):
            stats["cached"] += 1
            accept(unit, hit, None)
            tick()
        else:
            pending.append(unit)

    position = {unit.id: index for index, unit in enumerate(units)}

    def context_for(unit: Unit) -> str:
        index = position[unit.id]
        if index == 0 or units[index - 1].role not in ("body", "list"):
            return ""
        return markup.plain_text(units[index - 1].source)[-300:].strip()

    def run_batch(batch: list[Unit]) -> None:
        missing = batch
        try:
            with lock:
                stats["requests"] += 1
            reply = backend.complete(build_prompt(batch, config, glossary, context_for(batch[0])))
            parsed = parse_reply(reply, len(batch))
            missing = []
            for number, unit in enumerate(batch, 1):
                if number in parsed:
                    accept(unit, parsed[number], keys[unit.id])
                else:
                    missing.append(unit)
        except TranslationError as error:
            log.warning("batch failed: %s", error)
        for unit in missing:                          # retry stragglers one by one
            if len(batch) == 1 and missing is batch:
                unit.notes.append("翻译失败，保留原文")
                with lock:
                    stats["failed"] += 1
                continue
            try:
                with lock:
                    stats["requests"] += 1
                    stats["retried"] += 1
                reply = backend.complete(build_prompt([unit], config, glossary, context_for(unit)))
                parsed = parse_reply(reply, 1)
                if 1 in parsed:
                    accept(unit, parsed[1], keys[unit.id])
                    continue
            except TranslationError as error:
                log.warning("unit %s failed: %s", unit.id, error)
            unit.notes.append("翻译失败，保留原文")
            with lock:
                stats["failed"] += 1
        tick(len(batch))

    with ThreadPoolExecutor(max_workers=max(1, config.threads)) as pool:
        list(pool.map(run_batch, make_batches(pending)))
    return stats
