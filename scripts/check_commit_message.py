#!/usr/bin/env python3
"""Require Conventional Commits 1.0.0 subjects for commits and pull request titles.

Accepted subject: type(scope)!: description
  type         feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert
  (scope)      optional, lowercase letters, digits and . _ / -
  !            optional breaking-change marker
  description  non-empty, not ending with a period
The subject after any autosquash prefix is at most 100 characters. Git autosquash prefixes (fixup! , squash! , amend! ) are accepted only before a
conventional subject. Default merge and revert subjects are rejected.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

TYPES = ('feat', 'fix', 'docs', 'style', 'refactor', 'perf', 'test', 'build', 'ci', 'chore', 'revert')
MAX_LENGTH = 100
SCISSORS = re.compile(r'#\s*-+\s*>8\s*-+\s*')
AUTOSQUASH = re.compile(r'(?:fixup|squash|amend)! ')
SUBJECT = re.compile(r'(?P<type>[^(!:\s]+)(?:\((?P<scope>[^()]*)\))?(?P<breaking>!)?: (?P<description>.*)')
SCOPE = re.compile(r'[a-z0-9._/-]+')
SPEC = 'https://www.conventionalcommits.org/en/v1.0.0/'


def subject_from_message(text):
    """Return git's effective first line: skip comments, stop at the scissors line."""
    for line in text.splitlines():
        if SCISSORS.fullmatch(line):
            break
        if line.startswith('#') or not line.strip():
            continue
        return line.rstrip()
    return ''


def violation(subject):
    """Return None for a conventional subject, otherwise a human-readable reason."""
    text = subject.strip()
    if not text:
        return 'empty commit subject'
    if text.startswith('Merge '):
        return 'default merge subject; write a conventional merge subject such as "chore: merge pull request #123"'
    if text.startswith('Revert "'):
        return 'default revert subject; write "revert: <original description>"'
    body = text
    while AUTOSQUASH.match(body):
        body = body[AUTOSQUASH.match(body).end():]
    if not body:
        return 'autosquash prefix without a conventional subject'
    if len(body) > MAX_LENGTH:
        return f'subject is {len(body)} characters; the limit is {MAX_LENGTH}'
    match = SUBJECT.fullmatch(body)
    if not match and re.fullmatch(r'[^(!:\s]+(?:\([^()]*\))?!?:', body):
        return 'description must be non-empty text after ": "'
    if not match:
        return 'expected "type(scope)!: description" with one space after the colon'
    if match['type'] not in TYPES:
        return f'unknown type "{match["type"]}"; use one of {", ".join(TYPES)}'
    if match['scope'] is not None and not SCOPE.fullmatch(match['scope']):
        return f'invalid scope "{match["scope"]}"; use lowercase letters, digits and . _ / -'
    description = match['description']
    if not description.strip() or description != description.lstrip():
        return 'description must be non-empty text after ": "'
    if description.endswith('.'):
        return 'description must not end with a period'
    return None


def range_subjects(revision_range, cwd=None):
    run = subprocess.run(['git', 'log', '--format=%h%x09%s', revision_range, '--'],
                         cwd=cwd, capture_output=True, text=True)
    if run.returncode:
        raise ValueError(f'could not read commit range {revision_range!r}: {run.stderr.strip()}')
    return [line.split('\t', 1) for line in run.stdout.splitlines() if line]


def report(label, subject):
    reason = violation(subject)
    if reason:
        print(f'{label}: {reason}\n  subject: {subject}', file=sys.stderr)
    return reason is None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Exit status: 0 conventional, 1 violation found, 2 usage or Git error.\n'
        'Examples: check_commit_message.py .git/COMMIT_EDITMSG\n'
        '          check_commit_message.py --subject "feat(cli): add a flag"\n'
        '          check_commit_message.py --range origin/main..HEAD')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('message_file', nargs='?', type=Path, help='commit message file, as passed to commit-msg hooks')
    group.add_argument('--subject', help='check one subject, such as a pull request title')
    group.add_argument('--range', dest='revision_range', metavar='BASE..HEAD', help='check every commit subject in a Git range')
    args = parser.parse_args(argv)
    try:
        if args.revision_range is not None:
            if '..' not in args.revision_range:
                parser.error('--range expects BASE..HEAD')
            commits = range_subjects(args.revision_range)
            bad = [commit for commit, subject in commits if not report(f'commit {commit}', subject)]
            if bad:
                print(f'{len(bad)} of {len(commits)} commits violate Conventional Commits ({SPEC})', file=sys.stderr)
            return 1 if bad else 0
        if args.subject is not None:
            return 0 if report('subject', args.subject) else 1
        text = args.message_file.read_text(encoding='utf-8', errors='replace')
    except (OSError, ValueError) as exc:
        print(f'commit message check: {exc}', file=sys.stderr)
        return 2
    if report('commit message', subject_from_message(text)):
        return 0
    print(f'See {SPEC}; your message is kept in {args.message_file}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
