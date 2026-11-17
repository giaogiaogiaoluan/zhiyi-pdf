"""Output fonts: one Latin family plus one CJK family, picked per character."""

from __future__ import annotations

import pymupdf

from . import assets

_FAMILIES = {
    True: {  # serif
        "lr": "NotoSerif-Regular.ttf", "lb": "NotoSerif-Bold.ttf",
        "li": "NotoSerif-Italic.ttf", "lbi": "NotoSerif-BoldItalic.ttf",
        "cr": "SourceHanSerifCN-Regular.ttf", "cb": "SourceHanSerifCN-Bold.ttf",
    },
    False: {  # sans
        "lr": "NotoSans-Regular.ttf", "lb": "NotoSans-Bold.ttf",
        "li": "NotoSans-Italic.ttf", "lbi": "NotoSans-BoldItalic.ttf",
        "cr": "SourceHanSansCN-Regular.ttf", "cb": "SourceHanSansCN-Bold.ttf",
    },
}
# Punctuation that must come from the CJK font so it gets CJK spacing.
_CJK_PUNCT = set("“”‘’…—·")


def is_cjk(char: str) -> bool:
    code = ord(char)
    return (0x2E80 <= code <= 0x9FFF or 0x3000 <= code <= 0x30FF or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFFEF or 0xAC00 <= code <= 0xD7AF or 0x20000 <= code <= 0x2FA1F
            or char in _CJK_PUNCT)


class FontSet:
    """Fonts for one typeface class (serif or sans), loaded lazily."""

    def __init__(self, serif: bool = True):
        self.serif = serif
        self.prefix = "S:" if serif else "N:"
        self._fonts: dict[str, pymupdf.Font] = {}
        self._advance: dict[tuple[str, str], float] = {}
        self._pick: dict[tuple[str, bool, bool], str | None] = {}

    def font(self, key: str) -> pymupdf.Font:
        key = key.split(":")[-1]
        font = self._fonts.get(key)
        if font is None:
            name = _FAMILIES[self.serif][key]
            path = assets.font_path(name)
            if path is None:
                raise RuntimeError(f"字体不可用：{name}（下载失败，且本机没有缓存）")
            font = self._fonts[key] = pymupdf.Font(fontfile=str(path))
        return font

    def pick(self, char: str, bold: bool = False, italic: bool = False) -> str | None:
        """Font key able to draw ``char``, or None when no font has the glyph."""
        cache_key = (char, bold, italic)
        if cache_key in self._pick:
            return self._pick[cache_key]
        latin = "l" + ("b" if bold else "") + ("i" if italic else "") if (bold or italic) else "lr"
        cjk = "cb" if bold else "cr"
        order = (cjk, latin) if is_cjk(char) else (latin, cjk)
        chosen = next((self.prefix + key for key in order if self.font(key).has_glyph(ord(char))), None)
        self._pick[cache_key] = chosen
        return chosen

    def advance(self, key: str, char: str) -> float:
        """Advance width of ``char`` for font size 1."""
        cache_key = (key, char)
        value = self._advance.get(cache_key)
        if value is None:
            value = self._advance[cache_key] = self.font(key).glyph_advance(ord(char))
        return value

    def can_render(self, char: str) -> bool:
        return self.pick(char) is not None


_sets: dict[bool, FontSet] = {}


def fontset(serif: bool = True) -> FontSet:
    if serif not in _sets:
        _sets[serif] = FontSet(serif)
    return _sets[serif]


def font_by_key(key: str) -> pymupdf.Font:
    """Resolve a key produced by ``FontSet.pick`` ("S:lr", "N:cb", …)."""
    return fontset(key.startswith("S:")).font(key)
