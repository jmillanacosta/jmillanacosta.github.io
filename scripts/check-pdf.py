"""PDF text, reading order, fonts and page margins are checked with Poppler."""
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PDF_NAME = next(f['url'] for f in yaml.safe_load((ROOT / '_data/formats.yml').read_text(encoding='utf-8')) if f.get('mode') == 'cv')
PDF = f'output/cv/{PDF_NAME}'
NAME = yaml.safe_load((ROOT / '_data/cv.yml').read_text(encoding='utf-8'))['person']['name']

def run(*args):
    return subprocess.check_output(args, text=True)

def normalize(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))

class Content(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = []
        self.blocks = []
        self.headings = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if self.skip_depth or 'screen-only' in (dict(attrs).get('class') or '').split():
            if tag not in ('input', 'br', 'hr', 'img', 'meta', 'link'):
                self.skip_depth += 1
            return
        if tag in ('h1', 'h2', 'h3', 'p', 'li'):
            self.active.append([tag, []])

    def handle_startendtag(self, tag, attrs):
        # Self-closing void tags (<br />) have no content and must not end a skipped block.
        pass

    def handle_data(self, data):
        if self.skip_depth:
            return
        for _, parts in self.active:
            parts.append(data)

    def handle_endtag(self, tag):
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if self.active and self.active[-1][0] == tag:
            _, parts = self.active.pop()
            text = ''.join(parts).strip()
            self.blocks.append(text)
            if tag in ('h1', 'h2', 'h3'):
                self.headings.append(text)

content = Content()
content.feed(Path('_site/cv/index.html').read_text())
text = normalize(run('pdftotext', '-raw', PDF, '-'))
for block in content.blocks:
    assert normalize(block).casefold() in text.casefold(), f'Missing or reordered text: {block[:100]}'
position = 0
for heading in content.headings:
    position = text.casefold().index(normalize(heading).casefold(), position) + len(normalize(heading))

info = run('pdfinfo', PDF)
assert re.search(r'Tagged:\s+yes', info), 'PDF must preserve semantic tags'
pages = int(re.search(r'Pages:\s+(\d+)', info).group(1))
assert pages > 0, 'PDF is empty'
fonts = run('pdffonts', PDF).splitlines()[2:]
assert fonts and all(re.search(r'yes\s+yes\s+yes\s+\d+\s+\d+\s*$', f) for f in fonts), 'Fonts must be embedded with Unicode mappings'

root = ET.fromstring(run('pdftotext', '-bbox', PDF, '-'))
ns = {'x': 'http://www.w3.org/1999/xhtml'}
for number, page in enumerate(root.findall('.//x:page', ns), 1):
    words = page.findall('x:word', ns)
    footer_size = len(NAME.split()) + 10
    body, footer = words[:-footer_size], words[-footer_size:]
    footer_text = ' '.join(w.text or '' for w in footer)
    assert re.fullmatch(rf'{re.escape(NAME)} · CV · Updated [A-Z][a-z]+ \d{{1,2}}, \d{{4}} {number} / {pages}', footer_text), footer_text
    bottom = max(float(w.attrib['yMax']) for w in body)
    clearance = min(float(w.attrib['yMin']) for w in footer) - bottom
    assert clearance >= 24, f'Page {number}: content crowds footer ({clearance:.1f}pt)'
    for word in body:
        assert 40 <= float(word.attrib['xMin']) < float(word.attrib['xMax']) <= float(page.attrib['width']) - 40
    print(f'Page {number}: {clearance:.1f}pt footer clearance')
print(f'All {len(content.blocks)} content blocks extract correctly; heading order, tags and embedded Unicode fonts verified.')
