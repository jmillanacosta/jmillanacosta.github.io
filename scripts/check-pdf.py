"""Check the built CV's text coverage, reading order, fonts and page clearance.

Run after build-pdf.mjs. Requires Poppler (pdftotext, pdfinfo, pdffonts).
The three-page limit is intentional; review pagination when adding content.
"""
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET

PDF = 'output/pdf/javier-millan-acosta-cv.pdf'

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
        if self.skip_depth or 'screen-only' in dict(attrs).get('class', '').split():
            if tag not in ('input', 'br', 'hr', 'img', 'meta', 'link'):
                self.skip_depth += 1
            return
        if tag in ('h1', 'h2', 'h3', 'p', 'li'):
            self.active.append([tag, []])

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
content.feed(Path('_site/index.html').read_text())
text = normalize(run('pdftotext', '-raw', PDF, '-'))
for block in content.blocks:
    assert normalize(block).casefold() in text.casefold(), f'Missing or reordered text: {block[:100]}'
position = 0
for heading in content.headings:
    position = text.casefold().index(normalize(heading).casefold(), position) + len(normalize(heading))

info = run('pdfinfo', PDF)
assert re.search(r'Tagged:\s+yes', info), 'PDF must preserve semantic tags'
assert re.search(r'Pages:\s+3\b', info), 'Review changed pagination'
fonts = run('pdffonts', PDF).splitlines()[2:]
assert fonts and all(re.search(r'yes\s+yes\s+yes\s+\d+\s+\d+\s*$', f) for f in fonts), 'Fonts must be embedded with Unicode mappings'

root = ET.fromstring(run('pdftotext', '-bbox', PDF, '-'))
ns = {'x': 'http://www.w3.org/1999/xhtml'}
for number, page in enumerate(root.findall('.//x:page', ns), 1):
    words = page.findall('x:word', ns)
    # The small running footer is the final eight words on every page.
    body, footer = words[:-8], words[-8:]
    assert ' '.join(w.text for w in footer) == f'Javier Millán Acosta · CV {number} / 3'
    bottom = max(float(w.attrib['yMax']) for w in body)
    clearance = min(float(w.attrib['yMin']) for w in footer) - bottom
    assert clearance >= 24, f'Page {number}: content crowds footer ({clearance:.1f}pt)'
    for word in body:
        assert 40 <= float(word.attrib['xMin']) < float(word.attrib['xMax']) <= float(page.attrib['width']) - 40
    print(f'Page {number}: {clearance:.1f}pt footer clearance')
print(f'All {len(content.blocks)} content blocks extract correctly; heading order, tags and embedded Unicode fonts verified.')
