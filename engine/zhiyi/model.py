"""Shared data structures.

Coordinates are PyMuPDF page coordinates: origin top-left, y grows downward,
units are PDF points.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

Box = tuple[float, float, float, float]

_BOLD_NAME = re.compile(r"bold|black|heavy|semibold|demi|medi(?!c)|[-_.,]b(?:d|i)?$|cmbx|cmssbx", re.I)
_ITALIC_NAME = re.compile(r"ital|obli|slant|[-_.,](?:b?i|it)$|cmti|cmsl|cmmi", re.I)


@dataclass(slots=True)
class Glyph:
    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    oy: float          # baseline
    size: float
    font: str
    flags: int         # MuPDF span flags: 1 super, 2 italic, 4 serif, 8 mono, 16 bold
    color: int
    math: int = 0      # 0 text, 1 soft (symbol that may stay as text), 2 hard
    script: int = 0    # +1 superscript, -1 subscript

    @property
    def bold(self) -> bool:
        return bool(self.flags & 16) or bool(_BOLD_NAME.search(self.font))

    @property
    def italic(self) -> bool:
        return bool(self.flags & 2) or bool(_ITALIC_NAME.search(self.font))

    @property
    def serif(self) -> bool:
        return bool(self.flags & 4)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(slots=True)
class Line:
    page: int
    glyphs: list[Glyph]
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    label: str = ""        # layout label of the region that owns the line
    region: int = -1       # index into the page's region list, -1 when unowned

    @property
    def text(self) -> str:
        return "".join(g.c for g in self.glyphs)

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass(slots=True)
class Region:
    """A layout-model detection."""

    label: str
    box: Box
    score: float


@dataclass(slots=True)
class Inline:
    """An inline object re-used verbatim from the source page (formula clip)."""

    key: str               # placeholder as it appears in the markup, e.g. "{v1}"
    page: int
    box: Box               # source clip rectangle
    baseline: float        # source baseline of the surrounding line
    size: float            # source font size of the surrounding line
    text: str = ""         # best-effort unicode, used as a hint for the translator


@dataclass(slots=True)
class Style:
    """A deviation from the paragraph's base style, carried by a markup tag."""

    bold: bool = False
    italic: bool = False
    script: int = 0
    color: int | None = None


@dataclass
class Block:
    """One physical paragraph fragment: consecutive lines in one column."""

    page: int
    lines: list[Line]
    role: str = "body"     # body | heading | title | caption | footnote | table | list
    label: str = ""
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    size: float = 10.0
    pitch: float = 0.0     # source baseline distance (0 when single line)
    align: str = "left"    # left | justify | center | right
    indent: float = 0.0    # first-line indent relative to the body left edge
    hanging: float = 0.0   # body left edge minus first-line left edge (lists)
    bold: bool = False
    italic: bool = False
    serif: bool = True
    color: int = 0
    frame_x0: float = 0.0  # horizontal extent the fragment may occupy
    frame_x1: float = 0.0
    limit_y: float = 0.0   # lowest y the fragment may grow to
    order: int = 0
    table: int = -1
    link_kind: str = ""    # how this block continues ``prev``: "flow" | "formula"
    reference: bool = False
    next: "Block | None" = field(default=None, repr=False)
    prev: "Block | None" = field(default=None, repr=False)

    @property
    def box(self) -> Box:
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def text(self) -> str:
        return " ".join(line.text.strip() for line in self.lines)


@dataclass
class Unit:
    """A logical paragraph: one or more chained blocks, translated as a whole."""

    id: int
    blocks: list[Block]
    role: str
    source: str = ""                       # markup sent to the translator
    inlines: dict[str, Inline] = field(default_factory=dict)
    styles: dict[str, Style] = field(default_factory=dict)
    translation: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def page(self) -> int:
        return self.blocks[0].page


@dataclass
class PageInfo:
    number: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    kept: list[Line] = field(default_factory=list)   # lines left untouched
