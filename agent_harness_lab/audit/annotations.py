"""Turn a static audit JSON report into GitHub workflow-command annotations.

Every value in the report can come from a pull request (file names above all), so each one is escaped
before it reaches a workflow command. Findings are convention checks, never performance evidence.
"""
import json
import re
import sys

LIMIT = 50  # GitHub shows few annotations per step; the cap also bounds the output of a hostile tree
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
NOTICE = 'Convention check; not evidence of harness performance.'


def _data(value):
    # "::" is spaced apart so no hostile text can contain a workflow-command delimiter
    return re.sub(r':(?=:)', ': ', _CONTROL.sub('', str(value))).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def _property(value):
    return _data(value).replace(':', '%3A').replace(',', '%2C')


def _line(level, message, title=None, file=None):
    properties = ','.join(f'{name}={_property(value)}' for name, value in (('file', file), ('title', title)) if value)
    properties = ' ' + properties if properties else ''
    return f'::{level}{properties}::{_data(message)}'


def lines(report, changed=None):
    """Annotation lines for a completed report; `changed` (a set of paths) limits file findings."""
    if report.get('status') != 'completed':
        return [_line('error', 'The static audit did not complete; no findings are reported. ' + NOTICE, 'ahl audit')]
    rows = [row for row in report.get('findings', []) if isinstance(row, dict)
            and (changed is None or row.get('path') in changed or row.get('path') in (None, '.'))]
    out = []
    for row in rows[:LIMIT]:
        level = 'warning' if row.get('status') in ('weak', 'missing') else 'notice'
        path = row.get('path')
        message = f"{row.get('reason')}: {row.get('remediation') or 'no remediation proposed'}. {NOTICE}"
        out.append(_line(level, message, f"ahl audit {row.get('dimension')}",
                         path if isinstance(path, str) and path != '.' else None))
    if len(rows) > LIMIT:
        out.append(_line('notice', f'{len(rows) - LIMIT} further findings omitted from annotations.', 'ahl audit'))
    return out


def main(argv=None):
    """Read an audit report on stdin; the optional argument is a file of NUL-separated changed paths."""
    argv = sys.argv[1:] if argv is None else argv
    changed = None
    if argv:
        with open(argv[0], encoding='utf-8', errors='replace') as handle:
            changed = {name for name in handle.read().split('\0') if name}
    try:
        report = json.load(sys.stdin)
        if not isinstance(report, dict): raise ValueError
    except ValueError:
        print(_line('error', 'The static audit produced no readable report.', 'ahl audit'))
        return 1
    result = lines(report, changed)
    print('\n'.join(result))
    return 1 if report.get('status') != 'completed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
