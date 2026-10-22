"""Translate a joined paragraph once, then distribute its continuous translation.

Only layout is split locally. The model sees one input item, without the source
page boundary. Protected tokens are namespaced so page-local formula/style IDs
cannot collide. No partial output is accepted when tokens are lost or reordered.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import re
from typing import Callable

PREFIX = "PDF2REA_P"
PROTECTED = re.compile(
    r"</?style\b[^>]*>|\{\{[^{}\n]+\}\}|\{[^{}\n]+\}|\[\[[^\]\n]+\]\]|%%[^%\n]+%%|%(?:\d+\$)?[sdif]"
)
OPAQUE = re.compile(r"\{\{PDF2REA_P([01])_T\d+\}\}")
DIRECTIVE = """## PDF2REA Whole-Paragraph Translation
The single input item below is ONE logical paragraph reconstructed across a page
or column break. Translate the ENTIRE paragraph as one coherent passage. Resolve
syntax, word order, references and terminology using the complete paragraph.
Return ONE JSON item with its original id and one continuous translated output.
The PDF2REA tokens stand for protected formatting/formulas, not separate sentences.
Preserve each token exactly once and in its original order. Do not add headings,
page labels, artificial sentence breaks or explanations. Layout will be allocated
locally AFTER the whole paragraph has been translated.
"""


class JointResponseError(ValueError):
    """A complete, safe layout mapping could not be established."""


_WORDS = None


def _known(word):
    global _WORDS
    if _WORDS is None:
        try:
            from pathlib import Path
            _WORDS = set(Path("/usr/share/dict/words").read_text().lower().splitlines())
        except OSError:
            _WORDS = set()
    word = word.lower()
    return word in _WORDS or any(word.endswith(s) and word[:-len(s)] + stem in _WORDS
                                for s, stem in [("s", ""), ("es", ""), ("ies", "y"), ("ing", ""), ("ed", "")])


def join_text(left: str, right: str) -> str:
    left, right = left.rstrip(), right.lstrip()
    if left.endswith("\u00ad"):
        return left[:-1] + right
    if left.endswith("-") and right[:1].islower():
        a = re.search(r"([A-Za-z]+)-$", left)
        b = re.match(r"([a-z]+)", right)
        if a and b:
            prefix, suffix = a.group(1), b.group(1)
            # A genuine compound remains hyphenated; join only lexical evidence.
            if _known(prefix + suffix) and not (_known(prefix) and _known(suffix)):
                return left[:-1] + right
            # PDFs sometimes omit the gap after a split word: portunitiesto.
            for ending in ("with", "that", "to", "of", "in", "for", "and"):
                if suffix.endswith(ending) and _known(prefix + suffix[:-len(ending)]):
                    return left[:-1] + suffix[:-len(ending)] + " " + ending + right[len(suffix):]
        return left + right
    return left + " " + right


def clean_json(text: str):
    text = text.strip()
    if text.startswith("<json>") and text.endswith("</json>"):
        text = text[6:-7].strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


@dataclass
class JointRequest:
    item: dict
    originals: list[dict]
    token_maps: list[dict[str, str]]
    count_tokens: Callable[[str], int]

    def restore(self, raw: str) -> str:
        try:
            parsed = clean_json(raw)
        except (ValueError, TypeError, AttributeError) as error:
            raise JointResponseError("invalid JSON") from error
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], dict):
            raise JointResponseError("expected one paragraph result")
        item = parsed[0]
        if str(item.get("id")) != str(self.item["id"]) or not isinstance(item.get("output"), str):
            raise JointResponseError("invalid paragraph id/output")
        text = item["output"].strip()
        expected = [token for mapping in self.token_maps for token in mapping]
        actual = [m.group() for m in OPAQUE.finditer(text)]
        if actual != expected or Counter(actual) != Counter(expected):
            raise JointResponseError("protected tokens missing, duplicated or reordered")
        if any(m.group() not in expected for m in PROTECTED.finditer(text)):
            raise JointResponseError("unexpected protected token")
        if not text:
            raise JointResponseError("empty joined translation")

        # Formula/style tokens must stay in the page that owns their data.
        lower = max((text.index(t) + len(t) for t in self.token_maps[0]), default=1)
        upper = min((text.index(t) for t in self.token_maps[1]), default=len(text)-1)
        source_sizes = [max(1, self.count_tokens(x["input"])) for x in self.originals]
        visible = [PROTECTED.sub("", x["input"]) for x in self.originals]
        weight = max(1, len(visible[0])) / max(2, len(visible[0]) + len(visible[1]))
        desired = len(text) * weight

        def safe_cut(i):
            if not lower <= i <= upper or i <= 0 or i >= len(text):
                return False
            if any(m.start() < i < m.end() for m in re.finditer(r"\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?%?", text)):
                return False
            if any(m.start() < i < m.end() for m in OPAQUE.finditer(text)):
                return False
            # Never break a Latin word/number or a Unicode combining sequence.
            if text[i-1].isascii() and text[i].isascii() and text[i-1].isalnum() and text[i].isalnum():
                return False
            import unicodedata
            return not unicodedata.combining(text[i])

        def rank(i):
            boundary = text[i-1]
            bonus = .18 * len(text) if boundary in "。！？.!?" else .06 * len(text) if boundary in "；;，," else .02 * len(text) if boundary.isspace() else 0
            return abs(i-desired)-bonus

        candidates = sorted((i for i in range(1, len(text)) if safe_cut(i)), key=rank)
        for cut in candidates[:64]:
            pieces = [text[:cut].strip(), text[cut:].strip()]
            restored = []
            for piece, mapping in zip(pieces, self.token_maps):
                for token, original in mapping.items():
                    piece = piece.replace(token, original)
                restored.append(piece)
            if not all(restored):
                continue
            # Match the real engine's per-box sanity bounds so it does not undo a
            # valid joined translation by retranslating a page fragment alone.
            ratios = [self.count_tokens(s)/n for s,n in zip(restored, source_sizes)]
            if not all(.3 < ratio < 3 for ratio in ratios):
                continue
            if "".join("".join(pieces).split()) != "".join(text.split()):
                raise JointResponseError("layout split changed translated content")
            return json.dumps([{"id":x["id"],"output":s} for x,s in zip(self.originals,restored)],ensure_ascii=False)
        raise JointResponseError("no safe layout split within engine limits")


def prepare(items: list[dict], count_tokens: Callable[[str],int] | None = None) -> JointRequest:
    if len(items) != 2 or any(not isinstance(x.get("input"),str) or not x["input"].strip() for x in items):
        raise JointResponseError("expected two nonempty source fragments")
    if len({str(x.get("id")) for x in items}) != 2:
        raise JointResponseError("duplicate source ids")
    if any(PREFIX in x["input"] for x in items):
        raise JointResponseError("reserved token prefix in source")
    token_maps, texts, hints = [], [], {}
    for page,item in enumerate(items):
        mapping = {}
        def protect(match):
            token = "{{"+f"PDF2REA_P{page}_T{len(mapping)}"+"}}"
            mapping[token] = match.group()
            original_hints = item.get("formula_placeholders_hint", {})
            if isinstance(original_hints,dict) and match.group() in original_hints:
                hints[token] = original_hints[match.group()]
            return token
        texts.append(PROTECTED.sub(protect,item["input"]))
        token_maps.append(mapping)
    merged = {"id":items[0]["id"],"input":join_text(*texts),"layout_label":"text"}
    if hints:
        merged["formula_placeholders_hint"] = hints
    return JointRequest(merged,items,token_maps,count_tokens or (lambda s:len(s)))
