import conftest
import pymupdf

from zhiyi.analyze import analyze
from zhiyi import chain


def test_paragraphs_are_chained_across_column_and_page(paper):
    analysis = analyze(pymupdf.open(str(paper)))
    chained = [u for u in analysis.units if len(u.blocks) > 1]
    assert len(chained) == 2
    column, page = chained
    # each is one continuous sentence again, exactly as written
    assert column.source == conftest.P2_HEAD + " " + conftest.P2_TAIL
    assert column.blocks[0].page == column.blocks[1].page == 0
    assert column.blocks[1].x0 > column.blocks[0].x1                   # next column
    assert page.source == conftest.P4_HEAD + " " + conftest.P4_TAIL
    assert [b.page for b in page.blocks] == [0, 1]                     # next page
    assert all(b.link_kind == "flow" for u in chained for b in u.blocks[1:])


def test_reading_order_goes_down_each_column(paper):
    analysis = analyze(pymupdf.open(str(paper)))
    starts = [u.source[:12] for u in analysis.units if u.page == 0]
    assert starts == ["A Synthetic ", "1 Introducti", "Knowledge gr", "Older adults", "Personalised", "The remainde"]


def test_complete_paragraphs_stay_separate(paper):
    analysis = analyze(pymupdf.open(str(paper)))
    sources = [u.source for u in analysis.units]
    assert any(s.startswith("Knowledge graphs") and s.endswith("constraints.") for s in sources)
    assert any(s.startswith("Personalised recommendation") for s in sources)


def test_roles_headers_and_references(paper):
    analysis = analyze(pymupdf.open(str(paper)))
    roles = {u.source: u.role for u in analysis.units}
    assert roles["A Synthetic Paper About Layout"] == "title"
    assert roles["1 Introduction"] == "heading"
    assert not any("Synthetic Journal" in s for s in roles)            # running footer untouched
    assert not any("Smith J" in s for s in roles)                      # bibliography kept as is
    assert analysis.stats["references_kept"] == 1
    assert analysis.body_size == 10.0


def test_numbers_stay_in_the_sentence(paper):
    analysis = analyze(pymupdf.open(str(paper)))
    unit = next(u for u in analysis.units if u.source.startswith("Personalised"))
    assert "96 participants" in unit.source and "35%" in unit.source
    assert not unit.inlines                                            # nothing mistaken for a formula


def test_open_and_closed_endings():
    assert chain.ends_open("barriers, collectively")
    assert chain.ends_open("as shown by Smith et al.")
    assert chain.ends_open("a well-con-")
    assert not chain.ends_open("ends here.")
    assert not chain.ends_open("ends here [12].")
    assert chain.is_references_heading("7. References")
    assert not chain.is_references_heading("Related work")
