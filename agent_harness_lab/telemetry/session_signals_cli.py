"""Export content-free session summaries from one explicitly selected native capture."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from . import claude_native, codex_rollout
from .session_signals import summarize


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl score-sessions', description='Export advisory session signals from a native capture, offline.')
    parser.add_argument('project', type=Path, help='Project directory associated with the capture')
    parser.add_argument('--capture', type=Path, required=True, help='Existing native JSONL capture')
    parser.add_argument('--format', choices=('claude', 'codex'), required=True, help='Pinned native capture format')
    parser.add_argument('--output', type=Path, required=True, help='New private JSON file; existing files are never replaced')
    args = parser.parse_args(argv)
    try:
        reader = claude_native if args.format == 'claude' else codex_rollout
        rows, receipt, _ = reader.read(args.capture, args.project.resolve(), include_session_signals=True)
        incomplete = receipt['partial_tail'] or bool(receipt['unsupported_records'])
        payload = json.dumps(summarize(rows, incomplete=incomplete), indent=2, allow_nan=False) + '\n'
        descriptor, temporary = tempfile.mkstemp(prefix='.ahl-sessions-', dir=args.output.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write(payload)
            os.link(temporary, args.output)
        finally:
            os.unlink(temporary)
    except (OSError, ValueError, NotImplementedError):
        print('ahl score-sessions: capture could not be read or output could not be created', file=sys.stderr)
        return 2
    return 0
