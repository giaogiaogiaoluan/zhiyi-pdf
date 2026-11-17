from zhiyi import markup
from zhiyi.model import Block, Inline, Style, Unit
from zhiyi.translate.session import from_mt, to_mt


def _unit(inlines=("{v1}",), styles=("a", "sup"), formula_links=0):
    blocks = [Block(page=0, lines=[])] + [Block(page=0, lines=[], link_kind="formula") for _ in range(formula_links)]
    unit = Unit(id=1, blocks=blocks, role="body")
    for key in inlines:
        unit.inlines[key] = Inline(key, 0, (0, 0, 10, 10), 8, 10)
    for name in styles:
        unit.styles[name] = Style(color=255) if name == "a" else Style(script=1)
    return unit


def test_repair_normalises_marker_variants():
    text, notes = markup.repair(_unit(), "对于节点 ｛ v1 ｝，见 < a >12< /a >。")
    assert text == "对于节点 {v1}，见 <a>12</a>。" and not notes


def test_repair_restores_a_lost_formula_and_drops_duplicates():
    text, notes = markup.repair(_unit(inlines=("{v1}", "{v2}")), "设 {v1} 且 {v1}。")
    assert text.count("{v1}") == 1 and text.endswith("{v2}")
    assert notes and "{v2}" in notes[0]


def test_repair_strips_broken_tags_only():
    text, notes = markup.repair(_unit(), "见 <a>12 和 {v1}。")
    assert "<a>" not in text and "{v1}" in text and notes


def test_repair_drops_break_markers_when_one_is_missing():
    text, notes = markup.repair(_unit(inlines=(), formula_links=2), "前半 {eq1} 后半")
    assert "{eq" not in text and any("续排" in n for n in notes)


def test_parse_resolves_styles_and_inlines():
    unit = _unit()
    base = Block(page=0, lines=[], color=0)
    runs = markup.parse(unit, "见 <a>12</a> 与 {v1}<sup>2</sup>", base)
    kinds = [("inline" if r.inline else "text", r.text, r.color, r.script) for r in runs]
    assert kinds == [("text", "见 ", 0, 0), ("text", "12", 255, 0), ("text", " 与 ", 0, 0),
                     ("inline", "", 0, 0), ("text", "2", 0, 1)]


def test_machine_translation_round_trip_keeps_marks():
    source = "Zidu Xu<sup>1,2</sup>, see [<a>14-16</a>] and <b>bold</b> {v1}"
    flat, held, links = to_mt(source)
    assert "<" not in flat and "{s1}" in flat and "{v1}" in flat
    restored = from_mt("徐子都{ s1 }，参见 [14-16] 和粗体 {v1}", held, links)
    assert restored == "徐子都<sup>1,2</sup>，参见 [<a>14-16</a>] 和粗体 {v1}"
