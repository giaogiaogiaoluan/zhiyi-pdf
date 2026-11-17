import json

import pymupdf

from zhiyi.config import Config
from zhiyi.pipeline import translate_pdf


def _cjk_sizes(page, clip=None):
    sizes = set()
    for block in page.get_text("dict", clip=clip)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if any("一" <= c <= "鿿" for c in span["text"]):
                    sizes.add(round(span["size"], 2))
    return sizes


def test_end_to_end_with_uniform_body_font(paper, tmp_path):
    out = tmp_path / "out"
    report = translate_pdf(paper, out, Config(service="pseudo"))
    assert report["analysis"]["chained"] == 2
    assert report["layout"]["shrunk"] == report["layout"]["shrunk_hard"] == report["layout"]["kept_original"] == 0

    mono = pymupdf.open(report["outputs"]["mono"])
    first = mono[0]
    text = first.get_text()
    assert "Knowledge graphs" not in text and "collectively" not in text    # source body removed
    assert "Synthetic Journal 2026" in text                                # footer untouched
    assert "Smith J" in mono[1].get_text()                                 # references untouched
    body = pymupdf.Rect(55, 126, 545, 745)
    assert _cjk_sizes(first, body) == {10.0}                               # one size for all body text
    assert _cjk_sizes(mono[1], pymupdf.Rect(55, 55, 295, 95)) == {10.0}

    # the chained paragraph really continues in the next column and on the next page
    right_top = first.get_text(clip=pymupdf.Rect(305, 100, 545, 175))
    assert any("一" <= c <= "鿿" for c in right_top)
    assert any("一" <= c <= "鿿" for c in mono[1].get_text(clip=pymupdf.Rect(55, 55, 295, 95)))

    dual = pymupdf.open(report["outputs"]["dual"])
    assert len(dual) == 2 and dual[0].rect.width == 1200
    assert "Knowledge graphs" in dual[0].get_text()
    assert json.loads(open(report["outputs"]["report"], encoding="utf-8").read())["engine"]


def test_alternating_pages_and_page_selection(paper, tmp_path):
    report = translate_pdf(paper, tmp_path / "out", Config(service="pseudo", dual_mode="TB", mono=False, pages=[0]))
    assert "mono" not in report["outputs"]
    dual = pymupdf.open(report["outputs"]["dual"])
    assert len(dual) == 4 and dual[0].rect.width == 600
    assert "continue at the top" in dual[3].get_text()                     # page 2 was not selected
