"""Offline export of an explicitly selected AHL ledger to private OTLP logs."""
import argparse
import hashlib
import json
from pathlib import Path

from agent_harness_lab.evaluation.integrity import invalid_constant, read_regular, unique_object
from agent_harness_lab.private_journal import encoded
from . import otel
from .cli import EVENTS, OTEL_LOGS, REPORT, publish
from .mapping import Rejected


def export_trace(source, output):
    source, output = Path(source).absolute(), Path(output).absolute()
    if '..' in source.parts or '..' in output.parts or source == output or source in output.parents or output in source.parents:
        raise Rejected('unsafe_output', 'output')
    try:
        raw = read_regular(source / EVENTS, otel.MAX_TOTAL_BYTES)
        records = (json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant)
                   for line in raw.splitlines())
        document, report = otel.convert(records)
        report['source_digest'] = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError, RecursionError):
        raise Rejected('invalid_structure', 'input') from None
    publish(output, {OTEL_LOGS: encoded(document), REPORT: encoded(report)})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl trace-export', description='Export an explicitly named AHL ledger to private local OTLP JSON logs. No network is used; task outcome and lineage stay unknown.')
    parser.add_argument('--format', choices=['otel-json'], required=True, help='Local OTLP JSON log encoding')
    parser.add_argument('--input', type=Path, required=True, help='Directory containing events.v2.jsonl')
    parser.add_argument('--output', type=Path, required=True, help='New non-overlapping private directory')
    args = parser.parse_args(argv)
    try:
        report = export_trace(args.input, args.output)
    except Rejected as error:
        parser.exit(2, 'trace-export rejected: ' + str(error) + '\n')
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0
