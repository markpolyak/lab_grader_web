#!/usr/bin/env python3
"""Check repository-local Markdown/HTML links without making network requests."""
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and key in ('href', 'src'):
                self.targets.append(value)
            elif value and key == 'srcset':
                self.targets.extend(part.strip().split()[0] for part in value.split(','))


def targets(source):
    # Examples inside fenced blocks are not clickable navigation.
    source = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', source, flags=re.M | re.S)
    source = re.sub(r'`[^`\n]*`', '', source)
    parser = Links()
    parser.feed(source)
    markdown = [m[0] or m[1] for m in re.findall(r'\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+"[^"]*")?\s*\)', source)]
    return parser.targets + markdown


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    documents = [ROOT / name for name in names if name.endswith('.md')]
    errors = []
    checked = 0
    for document in documents:
        for raw in targets(document.read_text()):
            url = urlsplit(unescape(raw))
            if url.scheme or url.netloc or not url.path:
                continue
            checked += 1
            target = document.parent / unquote(url.path)
            if not target.exists():
                errors.append(f'{document.relative_to(ROOT)}: {raw}')
    if errors:
        print('\n'.join(errors))
        print(f'FAIL: {len(errors)} missing local targets')
        return 1
    print(f'PASS: {len(documents)} Markdown files, {checked} local link/image targets. External URLs and anchors are not fetched.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
