"""Shared fixtures: a synthetic two-column paper and a stub for the layout model.

The stub keeps the suite independent of the 75 MB ONNX model: regions are
derived from the rectangles the fixture itself drew.
"""

from __future__ import annotations

import pymupdf
import pytest

from zhiyi.model import Region

LEFT = (60, 0, 290, 0)
RIGHT = (310, 0, 540, 0)

P1 = ("Knowledge graphs organise entities and the relations between them, and they have become a "
      "common backbone for recommendation systems that must respect explicit domain constraints.")
P2_HEAD = ("Older adults living in the community often face reduced physical function and altered "
           "sensory perception, which change their eating habits and nutritional intake, and these "
           "issues, along with cultural and literacy barriers, collectively")
P2_TAIL = ("reduce their adherence to a diverse and high-quality diet. Several strides have been made "
           "in promoting healthy eating among this population.")
P3 = ("Personalised recommendation therefore has to balance preferences with nutritional needs. "
      "We evaluate the approach on a cohort of 96 participants and report a gain of 35% in diversity.")
P4_HEAD = ("The remainder of this paper is organised as follows, and the final part of this very long "
           "sentence deliberately runs past the bottom of the right-hand column so that it must")
P4_TAIL = "continue at the top of the following page before it finally comes to an end."


WIDTH, PITCH = 230, 12


def _wrap(text, size=10, font="tiro", width=WIDTH):
    """Greedy line breaking, as a real typesetter would do it."""
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and pymupdf.get_text_length(trial, font, size) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + [current]


def _draw(page, x, y, lines, size=10, font="tiro"):
    """Draw lines with the first baseline at y; returns their bounding box."""
    for index, line in enumerate(lines):
        page.insert_text((x, y + index * PITCH), line, fontsize=size, fontname=font)
    right = x + max(pymupdf.get_text_length(line, font, size) for line in lines)
    return (x, y - 0.8 * size, right, y + (len(lines) - 1) * PITCH + 0.25 * size)


def _centred(page, y, text, size, font):
    width = pymupdf.get_text_length(text, font, size)
    x = 300 - width / 2
    page.insert_text((x, y), text, fontsize=size, fontname=font)
    return (x, y - 0.8 * size, x + width, y + 0.25 * size)


def build_paper(path) -> dict:
    """Write the fixture PDF; returns {page number: [Region, …]}."""
    doc = pymupdf.open()
    regions: dict[int, list[Region]] = {0: [], 1: []}
    bottom = 740                                           # last baseline of both columns

    page = doc.new_page(width=600, height=800)
    regions[0].append(Region("title", _centred(page, 80, "A Synthetic Paper About Layout", 18, "hebo"), 0.9))
    regions[0].append(Region("title", _draw(page, 60, 120, ["1 Introduction"], 11, "tibo"), 0.9))
    regions[0].append(Region("plain text", _draw(page, 60, 140, _wrap(P1)), 0.9))

    # One paragraph that starts at the bottom of the left column and runs on
    # at the top of the right one, split at a line end like real layout does.
    second = _wrap(P2_HEAD + " " + P2_TAIL)
    head, tail = second[:5], second[5:]
    regions[0].append(Region("plain text", _draw(page, 60, bottom - (len(head) - 1) * PITCH, head), 0.9))
    box = _draw(page, 310, 120, tail)
    regions[0].append(Region("plain text", box, 0.9))
    regions[0].append(Region("plain text", _draw(page, 310, box[3] + 20, _wrap(P3)), 0.9))

    # A paragraph that runs on to the next page.
    fourth = _wrap(P4_HEAD + " " + P4_TAIL)
    head, tail = fourth[:3], fourth[3:]
    regions[0].append(Region("plain text", _draw(page, 310, bottom - (len(head) - 1) * PITCH, head), 0.9))
    page.insert_text((60, 780), "Synthetic Journal 2026", fontsize=7, fontname="helv")
    regions[0].append(Region("abandon", (58, 772, 160, 784), 0.9))

    page = doc.new_page(width=600, height=800)
    box = _draw(page, 60, 70, tail)
    regions[1].append(Region("plain text", box, 0.9))
    regions[1].append(Region("title", _draw(page, 60, box[3] + 30, ["References"], 11, "tibo"), 0.9))
    reference = _wrap("[1] Smith J, Lee K. A study of things. Journal of Stuff. 2020;1:1-10.")
    regions[1].append(Region("plain text", _draw(page, 60, box[3] + 50, reference), 0.9))
    page.insert_text((60, 780), "Synthetic Journal 2026", fontsize=7, fontname="helv")
    regions[1].append(Region("abandon", (58, 772, 160, 784), 0.9))
    doc.save(str(path))
    doc.close()
    return regions


@pytest.fixture()
def paper(tmp_path, monkeypatch):
    """Path of the fixture PDF, with the layout model stubbed out."""
    path = tmp_path / "paper.pdf"
    regions = build_paper(path)
    monkeypatch.setattr("zhiyi.regions.detect", lambda page: list(regions[page.number]))
    monkeypatch.setenv("ZHIYI_HOME", str(tmp_path / "home"))
    return path
