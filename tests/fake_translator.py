#!/usr/bin/env python3
"""Deterministic offline stand-in translator for the e2e harness.

Reads the text on stdin (the contract of pdf2zh_next's ``CLITranslator``,
see ``pdf2zh_next/translator/translator_impl/clitranslator.py:24``: "Input text is
always provided via stdin") and writes a pseudo-translation to stdout.

Every ASCII word is replaced by 词 repeated proportionally, so:
  * the output is unambiguously "translated" (CJK present),
  * the length ratio stays inside babeldoc's sanity window
    (``0.3 < output_tokens / input_tokens < 3``),
  * any English word that survives into the produced PDF proves that text never
    reached a translator -- which is exactly how defects 3 and 4 show up.
"""

from __future__ import annotations

import re
import sys

WORD = re.compile(r"[A-Za-z][A-Za-z\-']*")


def pseudo_translate(text: str) -> str:
    def replace(match: re.Match) -> str:
        return "词" * max(1, round(len(match.group(0)) / 4))

    return WORD.sub(replace, text)


def main() -> int:
    data = sys.stdin.read()
    if not data:
        return 0
    sys.stdout.write(pseudo_translate(data))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
