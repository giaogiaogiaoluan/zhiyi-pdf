#!/usr/bin/env python3
"""Build a 3-page fixture PDF that reproduces all four reported defects.

Run with the engine venv (it owns pymupdf)::

    ~/zotero-pdf2zh/server/zotero-pdf2zh-next-venv/bin/python \\
        tests/make_fixture.py /tmp/pdf2rea_fixture.pdf

Deliberately engineered so that each defect is measurable from the output text:

  page 1 ends mid-sentence  -> cross-page split      (defect 1)
  page 2 begins the clause  -> cross-page split      (defect 1)
  a bordered table          -> table text dropped    (defect 3)
  superscript citations     -> formula blank gaps    (defect 4)
  repeated terms/numerals   -> style drift           (defect 2)
"""

from __future__ import annotations

import sys

import pymupdf

PAGE = (595, 842)
MARGIN = 60
BODY_SIZE = 10.5
LINE = 15.0

# Sentence deliberately cut in half at the page break.
PART_A = (
    "In the following section we describe the training protocol in detail, "
    "including the optimiser, the schedule, and the early stopping rule, and we "
    "show that the proposed method converges whenever the learning rate is"
)
PART_B = (
    "set below one times ten to the minus fourth in every one of our experiments. "
    "This result holds for three dimensional inputs and for two dimensional "
    "inputs alike, as reported in Table 1."
)

PARA_CITATIONS = (
    "Recent progress in dense prediction has been driven by transformer "
    "architectures [12], by self-supervised pre-training [3, 4], and by better "
    "regularisation [7]. Smith showed a dagger effect in the same regime[5]. "
    "We therefore reuse the standard protocol of prior work [12] with only two "
    "changes, and report the mean of five runs."
)

PARA_TERMS = (
    "The word recall is used consistently: recall improves when the batch is "
    "larger. We measure recall on 30 images, then recall on 40 images, then "
    "recall again on 50 images. Three dimensional tensors beat two dimensional "
    "tensors by 5 mm of margin in the ablation study."
)

TABLE = [
    ["Method", "Recall", "Latency"],
    ["Baseline", "0.71", "12 ms"],
    ["Ours", "0.83", "9 ms"],
    ["Ours plus", "0.91", "11 ms"],
]


def text(fitz, page, x, y, content, size=BODY_SIZE, font="helv"):
    page.insert_text((x, y), content, fontsize=size, fontname=font, color=(0, 0, 0))


def wrap(content, width_chars=92):
    words, lines, current = content.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def paragraph(fitz, page, x, y, content, size=BODY_SIZE):
    for line in wrap(content):
        text(fitz, page, x, y, line, size)
        y += LINE
    return y


def draw_table(fitz, page, x, y):
    col_widths = [110, 80, 80]
    row_height = 20
    width = sum(col_widths)
    height = row_height * len(TABLE)
    for r in range(len(TABLE) + 1):
        page.draw_line((x, y - r * row_height), (x + width, y - r * row_height),
                       color=(0, 0, 0), width=0.7)
    c = 0
    for cw in col_widths + [0]:
        if cw:
            c += cw
        page.draw_line((x + c, y), (x + c, y - height), color=(0, 0, 0), width=0.7)
    cursor = 0
    for r, row in enumerate(TABLE):
        cursor = 0
        for index, cell in enumerate(row):
            text(fitz, page, x + cursor + 5, y - r * row_height - 13, cell, 9.5)
            cursor += col_widths[index]
    return y - height


def main(path: str) -> int:
    doc = pymupdf.open()
    # ---- page 1: first half of the split sentence, then a citation paragraph
    page = doc.new_page(width=PAGE[0], height=PAGE[1])
    y = MARGIN + 20
    text(doc, page, MARGIN, y, "PDF2REA Regression Fixture", 16)
    y += 30
    y = paragraph(doc, page, MARGIN, y, "Abstract. " + PARA_CITATIONS)
    y += LINE
    y = paragraph(doc, page, MARGIN, y, PARA_TERMS)
    y += LINE
    # Push the tail of the sentence to the very bottom so the break is real.
    filler = ("The remainder of this page is filled with auxiliary discussion so "
              "that the page break lands inside the sentence below, which is the "
              "condition this fixture is designed to reproduce. ")
    while y < PAGE[1] - 120:
        y = paragraph(doc, page, MARGIN, y, filler, 9.0) + LINE
    y = paragraph(doc, page, MARGIN, y, PART_A)

    # ---- page 2: continuation at the top, plus the table
    page2 = doc.new_page(width=PAGE[0], height=PAGE[1])
    y = MARGIN + 20
    y = paragraph(doc, page2, MARGIN, y, PART_B)
    y += 2 * LINE
    text(doc, page2, MARGIN, y, "Table 1: Recall and latency of each method.", 10)
    y += 24
    y = draw_table(doc, page2, MARGIN, y)
    y += 3 * LINE
    y = paragraph(doc, page2, MARGIN, y,
                  "Note the superscript markers again: prior work [8] and later "
                  "analysis [9, 10] agree, although two studies disagree[11].")

    # ---- page 3: terminology stress
    page3 = doc.new_page(width=PAGE[0], height=PAGE[1])
    y = MARGIN + 20
    for _ in range(3):
        y = paragraph(doc, page3, MARGIN, y, PARA_TERMS) + LINE
    doc.save(path, garbage=3, deflate=True)
    print(f"wrote {path}: {len(doc)} pages")
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pdf2rea_fixture.pdf"))
