"""Reading order, and the area each block is allowed to occupy."""

from __future__ import annotations

from .model import Block, PageInfo

OPAQUE = ("figure", "table", "isolate_formula")
MIN_GUTTER = 5.0


class Item:
    __slots__ = ("box", "block", "kind")

    def __init__(self, box, block=None, kind="block"):
        self.box, self.block, self.kind = box, block, kind


def _gutter(items: list[Item]):
    """The vertical gap that best separates columns, or None.

    Items lying across a candidate gap (a title, a full-width figure) do not
    rule it out: they become band separators in ``_order``. The gap crossed
    by the fewest items wins, then the widest.
    """
    edges = sorted({i.box[0] for i in items} | {i.box[2] for i in items})
    best, best_key = None, None
    for g0, g1 in zip(edges, edges[1:]):
        if g1 - g0 < MIN_GUTTER:
            continue
        left = sum(i.box[2] <= g0 + 0.01 for i in items)
        right = sum(i.box[0] >= g1 - 0.01 for i in items)
        if not left or not right:
            continue
        crossing = len(items) - left - right
        key = (-crossing, g1 - g0)
        if best_key is None or key > best_key:
            best, best_key = (g0, g1), key
    return best


def _order(items: list[Item]) -> list[Item]:
    if len(items) <= 1:
        return items
    gutter = _gutter(items)
    if gutter is None:
        return sorted(items, key=lambda i: (i.box[1], i.box[0]))
    g0, g1 = gutter
    spanners = sorted((i for i in items if i.box[0] < g0 - 1 and i.box[2] > g1 + 1), key=lambda i: i.box[1])
    if not spanners:
        left = [i for i in items if (i.box[0] + i.box[2]) / 2 < (g0 + g1) / 2]
        right = [i for i in items if (i.box[0] + i.box[2]) / 2 >= (g0 + g1) / 2]
        if not left or not right:
            return sorted(items, key=lambda i: (i.box[1], i.box[0]))
        return _order(left) + _order(right)
    rest = [i for i in items if not (i.box[0] < g0 - 1 and i.box[2] > g1 + 1)]
    result: list[Item] = []
    for spanner in spanners:
        middle = (spanner.box[1] + spanner.box[3]) / 2
        above = [i for i in rest if (i.box[1] + i.box[3]) / 2 < middle]
        rest = [i for i in rest if (i.box[1] + i.box[3]) / 2 >= middle]
        result += _order(above) + [spanner]
    return result + _order(rest)


def _contains(outer, inner, slack=2.0) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - slack <= cx <= outer[2] + slack and outer[1] - slack <= cy <= outer[3] + slack


def reading_order(page: PageInfo) -> list[Item]:
    """Order blocks and opaque regions; table cells follow their table row-major."""
    opaque = [Item(r.box, kind=r.label) for r in page.regions if r.label in OPAQUE]
    tables = [i for i in opaque if i.kind == "table"]
    cells: dict[int, list[Block]] = {}
    items: list[Item] = list(opaque)
    for block in page.blocks:
        owner = next((k for k, t in enumerate(tables) if block.role == "table" and _contains(t.box, block.box)), None)
        if owner is None:
            items.append(Item(block.box, block))
        else:
            cells.setdefault(owner, []).append(block)
    ordered: list[Item] = []
    for item in _order(items):
        ordered.append(item)
        if item.kind == "table":
            owner = tables.index(item)
            row: list[Block] = []
            for block in sorted(cells.get(owner, []), key=lambda b: b.y0):
                if row and block.y0 - row[0].y0 > 0.7 * block.size:
                    ordered += [Item(b.box, b) for b in sorted(row, key=lambda b: b.x0)]
                    row = []
                row.append(block)
            ordered += [Item(b.box, b) for b in sorted(row, key=lambda b: b.x0)]
    for index, item in enumerate(ordered):
        if item.block is not None:
            item.block.order = index
    return ordered


def assign_frames(page: PageInfo) -> None:
    """Decide how far each block may extend sideways and downwards."""
    lines = page.lines
    if not lines:
        return
    text_right = max(l.x1 for l in lines if l.label != "abandon") if any(l.label != "abandon" for l in lines) else page.width
    text_bottom = max((l.y1 for l in lines if l.label not in ("abandon",)), default=page.height)
    text_left = min((l.x0 for l in lines if l.label != "abandon"), default=0.0)
    columns = [(b.frame_x0, b.frame_x1, b) for b in page.blocks
               if b.role in ("body", "list") and len(b.lines) >= 3 and b.frame_x1 - b.frame_x0 > 12 * b.size]
    obstacles = [b.box for b in page.blocks]
    obstacles += [(l.x0, l.y0, l.x1, l.y1) for l in page.kept]
    obstacles += [r.box for r in page.regions if r.label in ("figure", "isolate_formula")]
    table_boxes = [r.box for r in page.regions if r.label == "table"]

    for block in page.blocks:
        own = block.box
        others = [o for o in obstacles if o != own]

        def right_limit(base: float) -> float:
            limit = base
            for o in others:
                if o[1] < block.y1 - 1 and o[3] > block.y0 + 1 and o[0] >= block.x1 - 1:
                    limit = min(limit, o[0] - 0.6 * block.size)
            return limit

        def left_limit(base: float) -> float:
            limit = base
            for o in others:
                if o[1] < block.y1 - 1 and o[3] > block.y0 + 1 and o[2] <= block.x0 + 1:
                    limit = max(limit, o[2] + 0.6 * block.size)
            return limit

        column = None
        if block.role == "table":
            table = next((t for t in table_boxes if _contains(t, own)), None)
            block.frame_x0 = block.x0
            block.frame_x1 = max(block.x1, right_limit(table[2] - 2 if table else text_right))
        else:
            center = (block.x0 + block.x1) / 2
            for c0, c1, owner in columns:
                if owner is not block and c0 - 2 <= block.x0 and block.x1 <= c1 + 2 and c0 <= center <= c1:
                    if column is None or c1 - c0 < column[1] - column[0]:
                        column = (c0, c1)
            if column is not None and column[1] - column[0] >= block.frame_x1 - block.frame_x0:
                block.frame_x0, block.frame_x1 = column
            else:
                block.frame_x1 = max(block.frame_x1, right_limit(text_right))
                if block.align == "center":
                    low = left_limit(min(l.x0 for l in lines))
                    half = min(center - low, block.frame_x1 - center)
                    block.frame_x0, block.frame_x1 = center - half, center + half
            # Never widen into something sitting beside the block.
            block.frame_x1 = max(block.x1, min(block.frame_x1, right_limit(block.frame_x1)))

        # Single lines: centred headings, titles and captions keep their centring.
        if len(block.lines) == 1 and block.align == "left" and block.role != "table":
            span0, span1 = left_limit(text_left), right_limit(text_right)
            if column is not None:
                span0, span1 = max(span0, column[0]), min(span1, column[1])
            middle = (block.x0 + block.x1) / 2
            if abs(middle - (span0 + span1) / 2) < 1.2 * block.size and block.x0 - span0 > 2.5 * block.size:
                half = min(middle - span0, span1 - middle)
                block.align = "center"
                block.frame_x0, block.frame_x1 = middle - half, middle + half

        limit = min(page.height - 18, max(text_bottom, block.y1) + block.size)
        for o in others + table_boxes:
            if o[1] >= block.y1 - 1 and o[0] < block.frame_x1 - 1 and o[2] > block.frame_x0 + 1:
                limit = min(limit, o[1] - 1.5)
        block.limit_y = max(block.y1, limit)
