"""Protect established settings contracts while the presentation evolves."""
from pathlib import Path
import json,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
X='http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul';H='http://www.w3.org/1999/xhtml'
s=(ROOT/'plugin/content/preferences.xhtml').read_text();d=ET.fromstring(f'<root xmlns="{X}" xmlns:html="{H}">{s}</root>');baseline=json.loads((ROOT/'tests/fixtures/preferences-1.0.5-contract.json').read_text());nodes=list(d.iter())
assert {n.get('preference') for n in nodes if n.get('preference')}==set(baseline['preference_keys']), 'existing setting removed or renamed'
ids=[n.get('id') for n in nodes if n.get('id')]
assert len(ids)==len(set(ids)), 'duplicate DOM IDs'
assert set(baseline['required_ids']) <= set(ids), 'existing handler target removed'
for binding,values in baseline['select_values'].items():
 n=next(n for n in nodes if n.get('preference')==binding)
 assert [o.get('value') for o in n]==values, f'protocol option values changed: {binding}'
assert len([n for n in nodes if n.get('data-pdf2rea-tab')])==6
assert len([n for n in nodes if n.get('data-pdf2rea-panel')])==6
root=next(n for n in nodes if n.get('id')=='pdf2rea-pref-root')
assert 'onPrefsEvent' in root.get('onload',''), 'native initialization disconnected'
assert '四项修复' not in s and '修复开关' not in s
print('Preferences compatibility: all 52 settings, handler IDs, option values, native initialization and six sections preserved')
