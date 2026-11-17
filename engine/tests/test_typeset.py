import pytest

from zhiyi import fonts, typeset
from zhiyi.markup import Run

FONTS = fonts.fontset(True)
TEXT = "随着全球老年人口持续增长，中国是全球老年人口比例最高的国家（预计到 2040 年达到 28%）。营养在健康老龄化中发挥着关键作用。"


def _lines(text, width, size=10):
    pieces = typeset.build_segments([Run(text)], FONTS, size)[0]
    breaker, lines = typeset.Breaker(pieces), []
    while not breaker.done:
        lines.append(breaker.next_line(width))
    return lines


def test_lines_fit_and_respect_punctuation_rules():
    lines = _lines(TEXT, 200)
    assert len(lines) > 2
    for line, natural, _ in lines:
        assert natural - line[-1].hang <= 200.01
        assert line[0].marks[0].text not in "，。）％%"          # never opens a line
        assert line[-1].marks[-1].text not in "（“"              # never closes one


def test_latin_words_and_numbers_are_not_split():
    for line, _, _ in _lines("这是 FoodKG 系统在 2040 年的 recommendation 结果，" * 6, 120):
        text = "".join(m.text for p in line for m in p.marks)
        for word in ("FoodKG", "2040", "recommendation"):
            assert not any(text.endswith(word[:k]) for k in range(1, len(word)))


def test_an_overlong_word_is_broken_instead_of_overflowing():
    lines = _lines("https://example.org/" + "a" * 120, 100)
    assert len(lines) > 3 and all(natural <= 100.5 for _, natural, _ in lines)


def test_justified_lines_end_at_the_right_edge():
    pieces = typeset.build_segments([Run(TEXT)], FONTS, 10)[0]
    breaker = typeset.Breaker(pieces)
    line, natural, last = breaker.next_line(200)
    ops = []
    typeset._place_line(line, natural, 50, 200, 100, "left", True, 10, ops)
    end = ops[-1].x + ops[-1].mark.width - line[-1].hang
    assert not last and end == pytest.approx(250, abs=0.05)


def test_break_markers_split_into_segments_without_leading_punctuation():
    runs = [Run("节点的感染压力为"), Run(brk=True), Run("，其中参数控制强度。")]
    first, second = typeset.build_segments(runs, FONTS, 10)
    assert second[0].marks[0].text == "其"
