"""Layout regressions: line ownership, invisible gutters, and paint order."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from pdf2rea_engine import structured_text as st
from babeldoc.format.pdf.document_il.midend import paragraph_finder as mod
from babeldoc.format.pdf.document_il import PdfCharacter,PdfStyle,Box,VisualBbox
finder=object.__new__(mod.ParagraphFinder)
order=0

def row(text,x,y):
 global order
 result=[]
 for ch in text:
  width=4
  if ch=='\t':ch=' ';width=60
  box=Box(x,y,x+width,y+10);order+=1
  result.append(PdfCharacter(pdf_character_id=ord(ch),char_unicode=ch,box=box,visual_bbox=VisualBbox(box=box),pdf_style=PdfStyle(font_size=10),xobj_id=0,render_order=order));x+=width
 return result

def paragraph(rows):
 comps=[]
 for cs in rows:
  line=mod.PdfLine(pdf_character=cs);finder.update_line_data(line);comps.append(mod.PdfParagraphComposition(pdf_line=line))
 p=mod.PdfParagraph(debug_id='test',layout_label='plain text',pdf_paragraph_composition=comps,xobj_id=0)
 finder.update_paragraph_data(p,update_unicode=True);return p

def run(p):
 before=Counter(id(c) for c in st.chars(p));page=NS(pdf_paragraph=[p]);st.repair(finder,NS(page=[page]),mod)
 assert Counter(id(c) for p in page.pdf_paragraph for c in st.chars(p))==before
 if len(page.pdf_paragraph)>1:assert len({p.render_order for p in page.pdf_paragraph})==len(page.pdf_paragraph)
 return page.pdf_paragraph

ps=run(paragraph([row('1 Department of Food',40,200),row('University of Science',48,188),row('2 Department of Health',40,176),row('3 School of Nutrition',40,164),row('* Correspondence: a@b.edu',40,152)]))
assert len(ps)==4 and 'University' in ps[0].unicode,[p.unicode for p in ps]
rs=[]
for i,(key,value) in enumerate([('AI','Artificial Intelligence'),('MCI','Mild Cognitive Impairment'),('LDL','Low Density Lipoprotein'),('K-MMSE-2 SV','Korean Version Of Mental Test')]):
 cs=row(key,40,200-i*14);last=cs[-1].box.x2
 # A single space with a huge advance is a real PDF encoding pattern.
 cs.append(PdfCharacter(char_unicode=' ',box=Box(last,200-i*14,110,210-i*14),visual_bbox=VisualBbox(box=Box(last,200-i*14,110,210-i*14)),pdf_style=PdfStyle(font_size=10),render_order=cs[-1].render_order+.1))
 cs+=row(value,110,200-i*14);rs.append(cs)
ps=run(paragraph(rs));assert len(ps)==8,[p.unicode for p in ps]
assert [p.layout_label for p in ps]==['abbreviation_key','table_cell']*4
assert len(run(paragraph([row('This is ordinary body text.',40,100),row('It continues on a new line.',40,86)])))==1

# Superscript list numbers and visual descenders must share the body baseline.
rs=[row('1 Department of Food',40,200),row('2 Department of Health',40,186),row('3 School of Nutrition',40,172)]
for cs in rs:
 cs[0].box.y += 3;cs[0].box.y2 -= 1;cs[0].pdf_style.font_size=6
 for c in cs[2:]:
  if c.char_unicode in 'pgjy':c.visual_bbox.box.y-=3
assert len(run(paragraph(rs)))==3

print('Structured text: numbered wrapped items, two-column definitions, wide spaces, long acronym, glyph conservation, paint order, normal prose: passed')
