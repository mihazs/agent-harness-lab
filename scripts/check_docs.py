#!/usr/bin/env python3
"""Check simple inline file links in tracked Markdown without network access."""
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote


LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s()]+))(?:\s+"[^"\n]*")?\s*\)')
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')


def destinations(text):
    """Yield line numbers and inline destinations outside code spans/fences."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = FENCE.match(line)
        if fence:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence)
                    and not marker[2].strip()):
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        # shortcut: simple same-line code spans only; use a parser if richer syntax is needed.
        line = re.sub(r'(`+).*?\1(?!`)', '', line)
        for match in LINK.finditer(line):
            yield number, match[1] or match[2]


def check(root):
    root = Path(root).resolve()
    tracked = subprocess.check_output(
        ['git', '-C', str(root), 'ls-files', '-z', '--', '*.md'],
        timeout=10,
    ).decode().split('\0')
    failures = []
    for name in filter(None, tracked):
        source = root / name
        if not source.resolve().is_relative_to(root):
            failures.append(f'{name}: Markdown source points outside the repository')
            continue
        try:
            text = source.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            failures.append(f'{name}: cannot read Markdown ({error.__class__.__name__})')
            continue
        for line, link in destinations(text):
            if link.startswith(('#', '//')) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', link):
                continue
            path = unquote(re.split(r'[?#]', link, maxsplit=1)[0])
            if not path:
                continue
            try:
                target = (source.parent / path).resolve()
            except (OSError, ValueError, RuntimeError):
                failures.append(f'{name}:{line}: {link}: invalid destination')
                continue
            if not target.is_relative_to(root):
                failures.append(f'{name}:{line}: {link}: outside the repository')
            elif not target.exists():
                failures.append(f'{name}:{line}: {link}: missing destination')
    return failures


def main():
    try:
        failures = check(Path(__file__).resolve().parents[1])
    except (OSError, subprocess.SubprocessError) as error:
        print(f'Cannot list tracked Markdown: {error}')
        return 1
    if failures:
        print('\n'.join(failures))
        return 1
    print('Tracked Markdown file links are valid.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
