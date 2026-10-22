"""Fix 2 (part b) -- numeric style consistency.

The complaint "又是 2 又是 二" is a *style drift* problem, not a mistranslation:
each LLM request in babeldoc is an independent batch of at most five paragraphs /
200 tokens (``il_translator_llm_only.py:592``), so nothing carries the numeric style
chosen in batch 3 into batch 17.  Prompt rules alone do not fix it, because the model
sees each batch cold.

This module therefore does two things:

1. ``NumericLedger`` accumulates, across the whole document run, how often a numeral
   appears in Arabic form vs Chinese form **in the same syntactic slot** (directly
   beside a unit, a percent sign, or an ASCII token).  Once the slot has enough
   evidence, the majority style becomes the document style.
2. ``normalize()`` rewrites only the minority occurrences, and only in slots that are
   unambiguous.  It never touches:
   - ordinals in front of a name/era (第二章, 三中全会, 一氧化氮),
   - numerals inside placeholders ``{v1}``, tags ``<style id='2'>``, or URLs,
   - chemical formulas, years written as digits, list markers, version numbers,
   - anything already matching the chosen style.

Policy values: ``keep`` (prompt guidance only), ``auto`` (document majority, default),
``arabic`` (force Arabic digits in numeric slots), ``chinese`` (force Chinese
numerals in bare counting slots).
"""

from __future__ import annotations

import re
import threading
from collections import Counter

CN_DIGITS = "一二三四五六七八九〇零"
CN_NUM_CHARS = CN_DIGITS + "十百千万亿两"

# A Chinese numeral may only be rewritten when it is a standalone quantity token:
# never when it is the tail of a longer numeral ("二十三" inside "一百二十三"),
# never an ordinal ("第二章"), and never a compound morpheme ("一种方法", "一次性").
NOT_INSIDE_WORD = r"(?<![\d第〇零一二三四五六七八九十百千万亿两])"

# Measurement units that make a numeral unambiguously numeric.
MEASURE_UNITS = (
    r"(?:%|‰|℃|°[CFK])"
    r"|(?:毫米|微米|纳米|厘米|分米|米|公里|千米|毫克|微克|千克|克|吨"
    r"|毫秒|微秒|秒|分钟|小时|天|年|月|日|倍|赫兹|千赫|兆赫"
    r"|焦耳|千焦|瓦|千瓦|安培|安|伏特|伏|欧姆|帕|千帕|兆帕|牛顿|摩尔"
    r"|元|美元|欧元|英镑|日元|韩元)"
)
COUNTER_UNITS = r"(?:次|组|例|项|台|批|轮|步|层|维|倍)"
LATIN_UNIT = r"[a-zA-ZµΩ°℃%]{1,8}"

# Regions we must never rewrite.
PROTECTED = re.compile(
    rf"""
      进一步|一次性|一步到位          # lexical phrases, not quantities
    | <style[^>]*>|</style>            # babeldoc rich-text tags
    | \{{[A-Za-z0-9_]+\}}              # {{v1}} style placeholders
    | \[\[[^\]]*\]\]                   # citation brackets
    | https?://\S+                     # urls
    | v\d+(?:\.\d+)*                   # version markers
    | 第\s*[{CN_NUM_CHARS}\d]+\s*(?:章|节|条|款|项|部分|页|图|表|式|版|卷|编)
    | [{CN_NUM_CHARS}\d]+\s*(?:中全会|届|进制|元数据)
    | [一二三四五六七八九十]+\s*(?:氧化[碳硫氮氯]|硫化|碳酸|氨|烷|烯|炔)
    """,
    re.VERBOSE,
)

# Arabic number sitting directly in front of a measurement unit.
ARABIC_WITH_UNIT = re.compile(
    rf"(?<![\d.,])({NOT_INSIDE_WORD}\d+(?:[.,]\d+)?)\s*"
    rf"(?=[%‰℃]|\s?(?:{MEASURE_UNITS}|{LATIN_UNIT})\b)"
)
CN_WITH_UNIT = re.compile(
    rf"({NOT_INSIDE_WORD}[{CN_NUM_CHARS}]+)\s*"
    rf"(?=[%‰℃]|\s?(?:{MEASURE_UNITS}|{LATIN_UNIT})\b)"
)

# Bare counting slot: "进行了三 次实验" / "进行了 3 次实验"
ARABIC_COUNTER = re.compile(rf"(?<![\d.,])({NOT_INSIDE_WORD}\d+)\s*(?=\s?(?:{COUNTER_UNITS}))")
CN_COUNTER = re.compile(rf"({NOT_INSIDE_WORD}[{CN_NUM_CHARS}]+)\s*(?=\s?(?:{COUNTER_UNITS}))")

_MIN_EVIDENCE = 3

_CN_TO_ARABIC = {
    "〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def chinese_to_number(text: str) -> int | None:
    """Convert a simple Chinese numeral (<= 99999) to an int, else None.

    Deliberately strict: anything with mixed or ambiguous writing returns None so we
    never rewrite a phrase we do not fully understand.
    """
    cleaned = text.strip()
    if not cleaned or any(ch not in CN_NUM_CHARS for ch in cleaned):
        return None
    # A bare multiplier is an indefinite amount ("万年", "亿万"), not a quantity.
    if cleaned in ("万", "亿"):
        return None
    if len(cleaned) == 1:
        if cleaned == "十":
            return 10
        return _CN_TO_ARABIC.get(cleaned)
    total = current = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for ch in cleaned:
        if ch in _CN_TO_ARABIC:
            current = _CN_TO_ARABIC[ch]
        elif ch in units:
            total += (current or 1) * units[ch]
            current = 0
        elif ch == "万":
            total = (total + current) * 10000 if (total or current) else 10000
            current = 0
        else:
            return None
    value = total + current
    return value if 0 <= value <= 100000 else None


def number_to_chinese(value: int) -> str | None:
    """Convert 0-99 to Chinese numerals; returns None outside that safe range."""
    if not isinstance(value, int) or not 0 <= value <= 99:
        return None
    digits = "〇一二三四五六七八九"
    if value < 10:
        return digits[value]
    tens, ones = divmod(value, 10)
    text = ("" if tens == 1 else digits[tens]) + "十"
    if ones:
        text += digits[ones]
    return text


class _Protected:
    """Mask protected spans so replacements cannot touch them."""

    def __init__(self, text: str):
        self.placeholders: dict[str, str] = {}
        self._counter = 0

    def _reserve(self, match: re.Match) -> str:
        token = f"\x00P{self._counter}\x00"
        self.placeholders[token] = match.group(0)
        self._counter += 1
        return token

    def mask(self, text: str) -> str:
        return PROTECTED.sub(self._reserve, text)

    def unmask(self, text: str) -> str:
        for token, original in self.placeholders.items():
            text = text.replace(token, original)
        return text


class NumericLedger:
    """Document-wide count of Arabic vs Chinese numeral usage per slot type."""

    def __init__(self):
        self._lock = threading.Lock()
        self.counts: dict[str, Counter] = {
            "unit": Counter(),
            "counter": Counter(),
        }
        self.policy = "auto"
        self.min_evidence = _MIN_EVIDENCE

    def observe(self, text: str) -> None:
        with self._lock:
            for group in ARABIC_WITH_UNIT.findall(text):
                self.counts["unit"]["arabic"] += 1
            for group in CN_WITH_UNIT.findall(text):
                if chinese_to_number(group) is not None:
                    self.counts["unit"]["chinese"] += 1
            for _group in ARABIC_COUNTER.findall(text):
                self.counts["counter"]["arabic"] += 1
            for group in CN_COUNTER.findall(text):
                if chinese_to_number(group) is not None:
                    self.counts["counter"]["chinese"] += 1

    def preferred(self, slot: str) -> str | None:
        """Return 'arabic'/'chinese' once the document has decided, else None."""
        with self._lock:
            if self.policy == "arabic":
                return "arabic"
            if self.policy == "chinese":
                return "chinese"
            if self.policy == "keep":
                return None
            counter = self.counts[slot]
            total = sum(counter.values())
            if total < self.min_evidence:
                return None
            arabic, chinese = counter["arabic"], counter["chinese"]
            if arabic == chinese:
                return None
            return "arabic" if arabic > chinese else "chinese"

    def snapshot(self) -> dict:
        with self._lock:
            return {slot: dict(counter) for slot, counter in self.counts.items()}


def _with_spacing(match: "re.Match[str]", replacement: str) -> str:
    """Replace only the numeral, keeping every surrounding space untouched.

    ``五 mm`` -> ``5 mm`` (not ``5mm``): dropping the separator would change the
    text outside the numeric slot and can break unit spacing in the PDF.
    """
    text = match.group(0)
    # span(1) is an offset into the whole subject, group(0) is a slice of it:
    # rebasing by match.start() is what makes this replace instead of insert.
    start = match.start(1) - match.start()
    end = match.end(1) - match.start()
    return text[:start] + replacement + text[end:]


def normalize(text: str, ledger: NumericLedger, enforce_units: bool = True) -> str:
    """Rewrite minority numeric styles in unambiguous slots. Idempotent."""
    if not text:
        return text
    if ledger.policy == "keep":
        ledger.observe(text)
        return text

    protector = _Protected(text)
    masked = protector.mask(text)
    before = masked

    unit_style = ledger.preferred("unit")
    if unit_style == "arabic":
        def _cn_to_arabic(match: re.Match) -> str:
            value = chinese_to_number(match.group(1))
            if value is None:
                return match.group(0)
            return _with_spacing(match, str(value))

        masked = CN_WITH_UNIT.sub(_cn_to_arabic, masked)
    elif unit_style == "chinese":
        def _arabic_to_cn(match: re.Match) -> str:
            raw = match.group(1)
            if "." in raw or "," in raw:  # never touch decimals
                return match.group(0)
            try:
                value = int(raw)
            except ValueError:
                return match.group(0)
            converted = number_to_chinese(value)
            return match.group(0) if converted is None else _with_spacing(match, converted)

        masked = ARABIC_WITH_UNIT.sub(_arabic_to_cn, masked)

    if enforce_units:
        counter_style = ledger.preferred("counter")
        if counter_style == "arabic":
            def _cn_counter_to_arabic(match: re.Match) -> str:
                value = chinese_to_number(match.group(1))
                return match.group(0) if value is None else _with_spacing(match, str(value))

            masked = CN_COUNTER.sub(_cn_counter_to_arabic, masked)
        elif counter_style == "chinese":
            def _counter_to_cn(match: re.Match) -> str:
                converted = number_to_chinese(int(match.group(1)))
                return match.group(0) if converted is None else _with_spacing(match, converted)

            masked = ARABIC_COUNTER.sub(_counter_to_cn, masked)

    result = protector.unmask(masked)
    # Observe the *original* form so the ledger keeps tracking what the model emits.
    ledger.observe(text)
    if result == before:
        return text
    return result
