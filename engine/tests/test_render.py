import pymupdf

from zhiyi import render
from zhiyi.model import Glyph


def _glyph(c, x0, x1, oy=100, size=10):
    return Glyph(c, x0, oy - 8, x1, oy + 2, oy, size, "Times-Roman", 4, 0)


def test_strips_leave_a_tall_neighbour_alone():
    page_doc = pymupdf.open()
    page = page_doc.new_page()
    page.insert_text((72, 100), "abcdefghij", fontsize=10)
    page.insert_text((90, 112), "(", fontsize=30)          # a tall bracket reaching up into the line
    raw = [(c["c"], c["bbox"], c["origin"][1], s["size"])
           for b in page.get_text("rawdict")["blocks"] for l in b["lines"] for s in l["spans"] for c in s["chars"]]
    line = [Glyph(c, *box[:2], *box[2:], oy, size, "Helvetica", 0, 0) for c, box, oy, size in raw if size == 10]
    bracket = [box for c, box, oy, size in raw if size == 30]
    plain, _ = render._strips(line + [None])
    assert any(render._hits(rect, bracket[0]) for rect in plain)      # the naive strip would remove it
    rects, stuck = render._strips(line + [None], bracket)
    assert not any(render._hits(rect, bracket[0]) for rect in rects)
    render._redact(page, rects)
    left = "".join(c["c"] for b in page.get_text("rawdict")["blocks"] for l in b["lines"]
                   for s in l["spans"] for c in s["chars"] if c["c"].strip())
    assert "(" in left
    assert len(left.replace("(", "")) == stuck                        # everything else is gone


def test_subtract_cuts_around_blockers():
    pieces = render._subtract((0, 0, 100, 1), [(40, -5, 50, 5), (200, 0, 210, 1)])
    assert pieces == [(0, 0, 40 - 0.03, 1), (50 + 0.03, 0, 100, 1)]
