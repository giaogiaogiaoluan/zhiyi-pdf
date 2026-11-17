"""Typesetting translated paragraphs back into their source boxes.

The guiding rule is consistency: font size and line pitch are solved once
per *style group* (all body text of one size, all captions, each table, …),
never per paragraph. Only the rare paragraph that still cannot fit is
adjusted on its own, in small recorded steps.

A chained paragraph is set as one stream of lines that fills its first box
and continues in the next, like linked text frames.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from . import markup
from .config import Config
from .fonts import FontSet, fontset, is_cjk
from .markup import Run
from .model import Block, Inline, Unit

SCRIPT_SIZE, SUP_RISE, SUB_DROP = 0.66, 0.36, 0.14
AUTOSPACE = 0.16                      # em between CJK and Latin/formula
MAX_STRETCH = 0.5                     # em per gap when justifying
_CLOSING = set("，。、；：？！）》」』】〕”’％,.;:?!)]}%")
_OPENING = set("（《「『【〔“‘([{")
_FULL_CLOSING = set("，。、；：？！）》」』】〕”’")
_FULL_OPENING = set("（《「『【〔“‘")


# --------------------------------------------------------------------------- #
# pieces: unbreakable clusters with glue between them
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Mark:
    """One drawable item: a character, or an inline formula clip."""

    text: str = ""
    font: str = ""
    size: float = 0.0
    rise: float = 0.0
    color: int = 0
    width: float = 0.0
    shift: float = 0.0                 # draw offset (compressed opening punctuation)
    inline: Inline | None = None
    scale: float = 1.0


@dataclass(slots=True)
class Piece:
    marks: list[Mark]
    width: float
    glue: float = 0.0                  # natural space before this piece
    stretch: float = 1.0               # relative share of justification
    hang: float = 0.0                  # width that may overhang at a line end


@dataclass(slots=True)
class Op:
    """A positioned drawing instruction; ``y`` is the baseline."""

    x: float
    y: float
    mark: Mark


@dataclass
class Placement:
    ops: dict[int, list[Op]] = field(default_factory=dict)      # page → ops
    size: float = 0.0
    leading: float = 0.0
    overflow: float = 0.0              # pt past the allowed bottom; <= 0 means it fits
    adjusted: str = ""                 # "", "tightened", "grown", "shrunk", "overflow"


def build_segments(runs: list[Run], fonts: FontSet, size: float) -> list[list[Piece]]:
    """Styled runs → pieces, split into segments at hard breaks ({eqN})."""
    segments: list[list[Piece]] = [[]]
    pending_space = False
    glued = False                      # the next element must stay with the current piece
    previous_kind = ""                 # "cjk" | "latin" | "inl"
    previous_char = ""

    after_break = False                # a box must not open with stray punctuation

    for run in runs:
        if run.brk:
            segments.append([])
            pending_space, glued, previous_kind, previous_char = False, False, "", ""
            after_break = True
            continue
        elements = [None] if run.inline is not None else run.text
        for char in elements:
            if char is not None and char.isspace():
                pending_space = True
                continue
            if after_break and char is not None and char in "，。；、,.;":
                continue
            after_break = False
            pieces = segments[-1]
            if char is None:
                inline = run.inline
                scale = size / inline.size if inline.size else 1.0
                mark = Mark(inline=inline, scale=scale, width=(inline.box[2] - inline.box[0]) * scale, size=size)
                kind, char = "inl", ""
            else:
                key = fonts.pick(char, run.bold, run.italic)
                if key is None:
                    char = "?"
                    key = fonts.pick(char, run.bold, run.italic)
                factor = SCRIPT_SIZE if run.script else 1.0
                rise = SUP_RISE * size if run.script > 0 else -SUB_DROP * size if run.script < 0 else 0.0
                mark = Mark(char, key, size * factor, rise, run.color, fonts.advance(key, char) * size * factor)
                kind = "cjk" if is_cjk(char) else "latin"

            attach = bool(pieces) and (
                glued
                or (not pending_space and kind == "latin" and previous_kind == "latin")
                or (not pending_space and char != "" and char in _CLOSING)
            )
            if attach:
                piece = pieces[-1]
                piece.marks.append(mark)
                piece.width += mark.width
            else:
                glue, stretch = 0.0, 1.0
                if pieces:
                    punct_edge = char in _FULL_OPENING or previous_char in _FULL_CLOSING
                    if (kind == "cjk" and previous_kind == "cjk") or punct_edge:
                        glue = 0.0
                    elif kind == "cjk" or previous_kind == "cjk":
                        glue, stretch = AUTOSPACE * size, 1.5
                    elif pending_space:
                        glue, stretch = 0.27 * size, 3.0
                    else:                                   # formula against Latin text
                        glue, stretch = AUTOSPACE * size, 3.0
                piece = Piece([mark], mark.width, glue, stretch)
                pieces.append(piece)
            piece.hang = 0.5 * size if char in _FULL_CLOSING else 0.0
            glued = char != "" and char in _OPENING
            pending_space = False
            previous_kind, previous_char = kind, char

    segments = [segment for segment in segments if segment] or [[]]
    for segment in segments:
        _compress_punctuation(segment)
    return segments


def _compress_punctuation(pieces: list[Piece]) -> None:
    """Squeeze the blank halves of adjacent full-width punctuation marks."""
    flat = [(piece, mark) for piece in pieces for mark in piece.marks]
    for (piece_a, a), (piece_b, b) in zip(flat, flat[1:]):
        if a.inline is not None or b.inline is not None:
            continue
        if a.text in _FULL_CLOSING and b.text in _FULL_CLOSING | _FULL_OPENING and a.width >= 0.9 * a.size:
            a.width -= 0.5 * a.size                 # the blank right half of a closing mark
            piece_a.width -= 0.5 * a.size
        elif a.text in _FULL_OPENING and b.text in _FULL_OPENING and b.width >= 0.9 * b.size:
            b.shift = -0.5 * b.size                 # the blank left half of an opening mark
            b.width -= 0.5 * b.size
            piece_b.width -= 0.5 * b.size


# --------------------------------------------------------------------------- #
# line breaking
# --------------------------------------------------------------------------- #
class Breaker:
    """Greedy line breaker handing out one line per call (widths may vary)."""

    def __init__(self, pieces: list[Piece]):
        self.pieces = list(pieces)
        self.at = 0

    @property
    def done(self) -> bool:
        return self.at >= len(self.pieces)

    def next_line(self, width: float) -> tuple[list[Piece], float, bool]:
        """Return (pieces, natural width, is the last line)."""
        pieces, start = self.pieces, self.at
        first = pieces[start]
        if first.width - first.hang > width + 0.5 and len(first.marks) > 1:
            head, rest = self._split(first, width)   # a word wider than the line (URL)
            pieces[start] = rest
            return [head], head.width, False
        line, natural, index = [first], first.width, start + 1
        while index < len(pieces):
            piece = pieces[index]
            if natural + piece.glue + piece.width - piece.hang > width + 0.01:
                break
            line.append(piece)
            natural += piece.glue + piece.width
            index += 1
        self.at = index
        return line, natural, index >= len(pieces)

    @staticmethod
    def _split(piece: Piece, width: float) -> tuple[Piece, Piece]:
        taken, used = [piece.marks[0]], piece.marks[0].width
        for mark in piece.marks[1:-1]:              # always leave something for the rest
            if used + mark.width > width:
                break
            taken.append(mark)
            used += mark.width
        rest = piece.marks[len(taken):]
        return (Piece(taken, used, piece.glue, piece.stretch),
                Piece(rest, sum(m.width for m in rest), 0.0, 1.0, piece.hang))


def _place_line(line: list[Piece], natural: float, x0: float, width: float, baseline: float,
                align: str, justify: bool, size: float, out: list[Op]) -> None:
    gaps = len(line) - 1
    extra = width - (natural - line[-1].hang)
    extra_per = [0.0] * len(line)
    x = x0
    if align == "center":
        x = x0 + max(0.0, (width - natural) / 2)
    elif align == "right":
        x = x0 + max(0.0, width - natural)
    elif gaps and (extra < 0 or justify):
        if extra < 0 or extra <= MAX_STRETCH * size * gaps:
            total = sum(piece.stretch for piece in line[1:])
            for k, piece in enumerate(line[1:], 1):
                extra_per[k] = extra * piece.stretch / total
    for k, piece in enumerate(line):
        if k:
            x += piece.glue + extra_per[k]
        for mark in piece.marks:
            out.append(Op(x + mark.shift, baseline - mark.rise, mark))
            x += mark.width


# --------------------------------------------------------------------------- #
# units
# --------------------------------------------------------------------------- #
@dataclass
class Plan:
    """Everything needed to typeset one unit, prepared once."""

    unit: Unit
    runs: list[Run]
    group: tuple
    base_size: float                   # source font size
    justify: bool
    align: str
    indent_em: float                   # first-line indent, in em of the output size
    hanging: float                     # pt, applied to every line but the first
    box_groups: list[list[Block]]      # blocks split at formula links
    weight: int                        # source line count
    fonts: FontSet = None              # serif or sans, following the source
    _segments: dict = field(default_factory=dict, repr=False)

    def segments(self, size: float) -> list[list[Piece]]:
        key = round(size, 3)
        if key not in self._segments:
            self._segments[key] = build_segments(self.runs, self.fonts, size)
        return self._segments[key]


def make_plan(unit: Unit, config: Config) -> Plan:
    first = unit.blocks[0]
    runs = markup.parse(unit, unit.translation or unit.source, first)
    role = unit.role
    if role == "table":
        group = ("table", first.page, first.table)
    elif role in ("heading", "title"):
        group = ("heading", round(first.size * 2) / 2, first.bold)
    elif role == "caption":
        group = ("caption", round(first.size * 2) / 2)
    else:
        group = ("text", round(first.size * 2) / 2)
    centered = first.align == "center"
    cjk_target = config.lang_out.lower().startswith(("zh", "ja", "ko"))
    indent_em = 0.0
    if role in ("body", "footnote") and first.indent > 0 and not centered:
        indent_em = 2.0 if cjk_target else first.indent / first.size
    box_groups: list[list[Block]] = [[first]]
    for block in unit.blocks[1:]:
        if block.link_kind == "formula":
            box_groups.append([block])
        else:
            box_groups[-1].append(block)
    return Plan(unit=unit, runs=runs, group=group, base_size=first.size,
                justify=role in ("body", "list", "footnote", "caption") and not centered,
                align="center" if centered else "left", indent_em=indent_em,
                hanging=first.hanging if role in ("body", "list", "footnote") else 0.0,
                box_groups=box_groups, weight=sum(len(b.lines) for b in unit.blocks),
                fonts=fontset(first.serif))


def _quotas(pieces: list[Piece], blocks: list[Block], capacities: list[int], plan: Plan, size: float,
            first_line: bool) -> list[int]:
    """How many lines each box of a flow takes.

    When the translation is shorter than the source, lines are shared out in
    proportion to the boxes' capacities, so the continuation box in the next
    column or page is never left empty.
    """
    breaker, total = Breaker(pieces), 0
    for index, block in enumerate(blocks):
        width = block.frame_x1 - block.frame_x0
        taken = 0
        while not breaker.done and (index == len(blocks) - 1 or taken < capacities[index]):
            indent = plan.indent_em * size if (first_line and total == 0) else plan.hanging
            breaker.next_line(max(width - indent, 2 * size))
            taken += 1
            total += 1
    room = sum(capacities)
    if total >= room or total < len(blocks):
        return capacities
    quotas, left = [], total
    for index, capacity in enumerate(capacities[:-1]):
        boxes_after = len(blocks) - index - 1
        share = max(1, round(total * capacity / room))
        share = min(share, capacity, left - boxes_after)
        quotas.append(max(1, share))
        left -= quotas[-1]
    return quotas + [capacities[-1]]


def set_unit(plan: Plan, size: float, leading: float, *, grow: bool, dry: bool = False) -> Placement:
    """Typeset a unit at one size/pitch. ``grow`` lets a box use the free space below it."""
    placement = Placement(size=size, leading=leading)
    segments = plan.segments(size)
    if len(segments) == len(plan.box_groups):
        streams = list(zip(segments, plan.box_groups))
    else:                                           # break markers lost: one continuous flow
        streams = [([p for segment in segments for p in segment], [b for g in plan.box_groups for b in g])]
    first_line = True
    for pieces, blocks in streams:
        if not pieces:
            continue
        tops = [block.lines[0].baseline for block in blocks]
        # Lowest baseline that still clears whatever sits below each box.
        floors = [block.limit_y - 0.3 * size for block in blocks]
        softs = [block.lines[-1].baseline + min(0.22 * leading, max(0.0, floor - block.lines[-1].baseline))
                 for block, floor in zip(blocks, floors)]
        capacities = [max(1, int((soft - top) // leading) + 1) for top, soft in zip(tops, softs)]
        quotas = _quotas(pieces, blocks, capacities, plan, size, first_line) if len(blocks) > 1 else capacities
        breaker = Breaker(pieces)
        for index, block in enumerate(blocks):
            if breaker.done:
                break
            last_block = index == len(blocks) - 1
            x0, width = block.frame_x0, block.frame_x1 - block.frame_x0
            top, soft = tops[index], softs[index]
            hard = max(soft, floors[index])
            lines = []
            while not breaker.done and (last_block or len(lines) < quotas[index]):
                indent = plan.indent_em * size if first_line else plan.hanging
                line, natural, final = breaker.next_line(max(width - indent, 2 * size))
                lines.append((line, natural, final, indent))
                first_line = False
            count = len(lines)
            pitch = leading
            if count >= 2:
                ideal = (block.lines[-1].baseline - top) / (count - 1)
                if leading <= ideal <= 1.03 * leading:
                    pitch = ideal                    # sit on the source baselines when nearly equal
            if last_block:
                bottom = hard if grow else soft
                placement.overflow = max(placement.overflow, top + (count - 1) * pitch - bottom)
            if dry:
                continue
            ops = placement.ops.setdefault(block.page, [])
            for k, (line, natural, final, indent) in enumerate(lines):
                _place_line(line, natural, x0 + indent, width - indent, top + k * pitch, plan.align,
                            plan.justify and not final, size, ops)
    return placement


# --------------------------------------------------------------------------- #
# document-level solving
# --------------------------------------------------------------------------- #
_TEXT_LEADINGS = [1.65, 1.6, 1.55, 1.5, 1.45, 1.4, 1.35, 1.3]
_OTHER_LEADINGS = [1.45, 1.35, 1.25]
_SCALES = [1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7]


def _fit_share(plans: list[Plan], scale: float, ratio: float, grow: bool) -> float:
    fitted = total = 0
    for plan in plans:
        size = plan.base_size * scale
        placement = set_unit(plan, size, ratio * size, grow=grow, dry=True)
        total += plan.weight
        if placement.overflow <= 0.01:
            fitted += plan.weight
    return fitted / total if total else 1.0


def solve_group(plans: list[Plan], config: Config, max_ratio: float | None = None) -> tuple[float, float, bool]:
    """Pick one (font scale, leading ratio) for a whole style group."""
    kind = plans[0].group[0]
    text = kind == "text"
    leadings = _TEXT_LEADINGS if text else _OTHER_LEADINGS
    if max_ratio is not None:
        leadings = [r for r in leadings if r <= max_ratio + 1e-9] or leadings[-1:]
    grow = not text
    target = 0.93 if text else 0.9
    scales = [s * config.font_scale for s in _SCALES]
    for scale in scales:
        share = 0.0
        for ratio in leadings:
            share = _fit_share(plans, scale, ratio, grow)
            if share >= target:
                return scale, ratio, grow
        if share >= (0.8 if text else 0.75):
            return scale, leadings[-1], grow        # stragglers are handled one by one
    return scales[-1], leadings[-1], grow


def _settle(plan: Plan, scale: float, ratio: float, grow: bool, config: Config) -> Placement:
    """Final placement for one unit, with individual fallbacks if it overflows.

    Order: slightly tighter lines → use the free space below → tighter still →
    and only then a smaller font, in 5 % steps.
    """
    size = plan.base_size * scale
    placement = set_unit(plan, size, ratio * size, grow=grow)
    if placement.overflow <= 0.01:
        return placement

    def attempt(at_size: float, at_ratio: float, label: str) -> Placement | None:
        candidate = set_unit(plan, at_size, at_ratio * at_size, grow=True if label != "tightened-in" else grow)
        if candidate.overflow <= 0.01:
            candidate.adjusted = label.split("-")[0]
            return candidate
        return None

    tighter = ratio - 0.05
    while tighter >= 1.3 - 1e-9:
        if found := attempt(size, tighter, "tightened-in"):
            return found
        tighter -= 0.05
    if not grow and (found := attempt(size, ratio, "grown")):
        return found
    tighter = ratio - 0.05
    while tighter >= 1.2 - 1e-9:
        if found := attempt(size, tighter, "tightened"):
            return found
        tighter -= 0.05
    # Last stop before touching the font: sit exactly on the source baselines.
    source_pitch = max((b.pitch for b in plan.unit.blocks), default=0.0)
    if 1.08 * size <= source_pitch < 1.2 * size and (found := attempt(size, source_pitch / size, "tightened")):
        return found
    floor = config.min_font_scale * scale
    step = scale - 0.05
    while step >= 0.5 - 1e-9:
        smaller = plan.base_size * step
        candidate = set_unit(plan, smaller, 1.2 * smaller, grow=True)
        if candidate.overflow <= 0.01:
            candidate.adjusted = "shrunk" if step >= floor - 1e-9 else "shrunk-hard"
            return candidate
        step -= 0.05
    placement = set_unit(plan, plan.base_size * 0.5, 0.6 * plan.base_size, grow=True)
    placement.adjusted = "overflow"
    return placement


def typeset(units: list[Unit], config: Config, progress=None) -> tuple[dict[int, Placement], dict]:
    """Typeset every translated unit. Returns (unit id → placement, report)."""
    plans = [make_plan(unit, config) for unit in units if unit.translation]
    groups: dict[tuple, list[Plan]] = defaultdict(list)
    for plan in plans:
        groups[plan.group].append(plan)
    placements: dict[int, Placement] = {}
    report = {"groups": [], "tightened": 0, "grown": 0, "shrunk": 0, "shrunk_hard": 0, "overflow": 0,
              "kept_original": 0}
    done = 0
    # The dominant text group goes first; smaller text groups (footnotes, an
    # abstract in another size) may not be set looser than the body.
    ordered = sorted(groups.items(), key=lambda kv: (kv[0][0] != "text", -sum(p.weight for p in kv[1])))
    body_ratio = None
    for group, members in ordered:
        # One size for the whole group: 9.96 pt and 10 pt source text come out identical.
        weights: dict[float, int] = defaultdict(int)
        for plan in members:
            weights[plan.base_size] += plan.weight
        common = max(weights, key=weights.get)
        for plan in members:
            plan.base_size = common
        scale, ratio, grow = solve_group(members, config, body_ratio if group[0] == "text" else None)
        if group[0] == "text" and body_ratio is None:
            body_ratio = ratio
        sizes = sorted({round(p.base_size * scale, 2) for p in members})
        report["groups"].append({"group": list(group), "units": len(members), "scale": round(scale, 3),
                                 "leading": ratio, "sizes": sizes})
        for plan in members:
            placement = _settle(plan, scale, ratio, grow, config)
            hopeless = placement.adjusted == "overflow" or (
                placement.adjusted == "shrunk-hard" and plan.unit.role == "table")
            if hopeless:
                # Tiny or spilling text helps nobody: the source stays in place.
                plan.unit.notes.append("译文放不下，此处保留原文")
                report["kept_original"] += 1
            else:
                placements[plan.unit.id] = placement
                key = placement.adjusted.replace("-", "_")
                if key in report:
                    report[key] += 1
                if placement.adjusted in ("shrunk", "shrunk-hard"):
                    plan.unit.notes.append({"shrunk": "个别段落放不下，已单独缩小字号",
                                            "shrunk-hard": "段落明显放不下，字号缩小较多"}[placement.adjusted])
            done += 1
            if progress:
                progress("typeset", done, len(plans))
    return placements, report
