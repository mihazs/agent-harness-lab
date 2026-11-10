"""Bounded private CLI orchestration for lexical constraint references."""
import time

from agent_harness_lab.audit import constraint_references
from agent_harness_lab.sources.git_view import Folder

PROFILE = 'ahl-constraint-scan-v1'
MAX_SOURCES = 128
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_REFERENCES = 2048
MAX_COMPARISONS = 50000


def capture(project, inventory, protected):
    """Re-read only inventoried checkout instruction files, with digest verification."""
    result = dict(profile=PROFILE, status='incomplete', files=[], unavailable=0, coverage='unknown',
                  observed_loading='unknown', enforcement='unknown', publication_allowed=False)
    if inventory.status != 'declared': return result
    sources = [node for node in inventory.nodes if node.origin == 'checkout' and node.kind == 'instruction']
    result['unavailable'] = sum(refusal.root == 'checkout' and refusal.reason != 'vcs_metadata'
                                for refusal in inventory.refusals)
    if len(sources) > MAX_SOURCES:
        result['unavailable'] += len(sources)
        return result
    references, comparisons = 0, 0
    try:
        with Folder(project, time.monotonic() + 30) as folder:
            for index, node in enumerate(sources):
                if node.declared_state != 'present' or node.parse_status != 'parsed':
                    result['unavailable'] += 1
                    continue
                if folder.bytes >= MAX_TOTAL_BYTES:
                    result['unavailable'] += len(sources) - index
                    break
                try:
                    raw = folder.read(node.path, min(constraint_references.MAX_BYTES, MAX_TOTAL_BYTES - folder.bytes))
                    if raw is None: raise ValueError('instruction unavailable')
                    entry = constraint_references.report(inventory, node.path, raw, protected)
                    references += len(entry['constraints'])
                    comparisons += len(entry['constraints']) * len(protected['paths'])
                    if references > MAX_REFERENCES or comparisons > MAX_COMPARISONS:
                        result['unavailable'] += len(sources) - index
                        break
                    result['files'].append(entry)
                except (ValueError, OSError, TimeoutError):
                    result['unavailable'] += 1
    except (ValueError, OSError, TimeoutError):
        result['unavailable'] += 1
    if result['unavailable'] == 0: result['status'] = 'completed'
    return result
