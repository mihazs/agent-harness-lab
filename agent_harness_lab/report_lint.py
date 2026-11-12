"""Fail a final report that states a number its receipt does not carry (#696).

Every number is written as ``VALUE [receipt: dotted.path]`` and the path must resolve in the
receipt to that value; ``unknown`` needs no source. Version strings, ``#N`` references, list
ordinals, dates, hex digests and code spans are not report numbers. Usage:
``python -m agent_harness_lab.report_lint REPORT.md workflow.json``.
"""
import json
from pathlib import Path
import re
import sys

NUMBER = re.compile(r'(?<![\w#./:-])(\d+(?:\.\d+)?)(%|ms|s)?(?![\w.:/-]*[\w])(?!\.\d)')
CITE = re.compile(r'\s*\[receipt:\s*([^\]\s]+)\s*\]')
IGNORED = re.compile(r'```.*?```|`[^`\n]*`|\b\d+\.\d+\.\d+\S*|\b\d{4}-\d{2}-\d{2}\S*|\b[0-9a-f]{12,}\b|^\s*\d+[.)]\s', re.S | re.M)


def resolve(receipt, path):
    for part in path.split('.'):
        try:
            receipt = receipt[int(part)] if isinstance(receipt, list) else receipt[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return receipt


def lint(report, receipt):
    """Return one message per number without a matching receipt source, in report order."""
    masked = IGNORED.sub(lambda m: ' '*len(m.group()), report)
    problems = []
    for match in NUMBER.finditer(masked):
        cited = CITE.match(report, match.end())
        line = report.count('\n', 0, match.start()) + 1
        if cited is None:
            problems.append(f'line {line}: {match.group()} has no [receipt: path] source')
            continue
        value = resolve(receipt, cited.group(1))
        number = float(match.group(1))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or number not in (value, value*100):
            problems.append(f'line {line}: {match.group()} does not match receipt field {cited.group(1)}')
    return problems


if __name__ == '__main__':
    found = lint(Path(sys.argv[1]).read_text(), json.loads(Path(sys.argv[2]).read_text()))
    print('\n'.join(found))
    raise SystemExit(1 if found else 0)
