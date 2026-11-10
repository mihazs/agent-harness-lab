"""Private lexical references to instruction constraints; never proof of enforcement."""
import hashlib
import re

from agent_harness_lab.audit import hook_candidates, permission_candidates
from agent_harness_lab.evaluation.integrity import relative
from agent_harness_lab.sources import harness_inventory

PROFILE = 'ahl-constraint-references-v1'
MAX_BYTES = 1024 * 1024
MAX_LINE = 4096
MAX_CONSTRAINTS = 512
MAX_COMPARISONS = 10000
_CONSTRAINT = re.compile(r'\b(?:never|must\s+not|do\s+not)\b', re.IGNORECASE)
_WRITE_CONSTRAINT = re.compile(r'\b(?:never|must\s+not|do\s+not)\s+(?:edit|write|modify|change)\b', re.IGNORECASE)


def report(inventory, path, raw, protected):
    """Use caller-supplied bytes only if they match a parsed checkout instruction node.

    No line text is returned. Exact protected-path references and permission
    candidates are lexical, separately captured observations, not enforcement.
    """
    if type(inventory) is not harness_inventory.HarnessInventory or inventory.status != 'declared':
        raise ValueError('declared harness inventory required')
    relative(path)
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError('bounded instruction bytes required')
    nodes = [n for n in inventory.nodes if n.origin == 'checkout' and n.kind == 'instruction' and n.path == path]
    source = hashlib.sha256(raw).hexdigest()
    if (len(nodes) != 1 or nodes[0].declared_state != 'present' or nodes[0].parse_status != 'parsed'
            or nodes[0].content_sha256 != source):
        raise ValueError('instruction bytes do not match parsed inventory')
    if not isinstance(protected, dict) or not isinstance(protected.get('paths'), list) or len(protected['paths']) > harness_inventory.MAX_ENTRIES:
        raise ValueError('bounded protected-path inventory required')
    permissions = permission_candidates.report(inventory, protected)
    hook = hook_candidates.first_write_candidate(inventory)
    paths = []
    for item in permissions['paths']:
        if (not isinstance(item, dict) or not isinstance(item.get('path'), str) or len(item['path']) > MAX_LINE
                or item.get('status') not in ('permission_candidate_present', 'unknown')
                or item.get('enforcement') != 'unknown'):
            raise ValueError('invalid permission candidate')
        relative(item['path'])
        paths.append((item['path'], item['status']))
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('instruction must be UTF-8') from None
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not _CONSTRAINT.search(line):
            continue
        if len(line) > MAX_LINE or len(rows) >= MAX_CONSTRAINTS or (len(rows) + 1) * len(paths) > MAX_COMPARISONS:
            raise ValueError('constraint reference bound exceeded')
        references = []
        for candidate, status in paths:
            if re.search(r'(?<![\w./-])' + re.escape(candidate) + r'(?![\w/-]|\.[\w])', line):
                references.append(dict(path=candidate, permission=status))
        candidate = hook if references and _WRITE_CONSTRAINT.search(line) else None
        rows.append(dict(line=number, line_sha256=hashlib.sha256(line.encode('utf-8')).hexdigest(),
                         protected_paths=references, hook_match='hook_candidate_present' if candidate else 'unknown',
                         hook_candidate=candidate, enforcement='unknown'))
    return dict(profile=PROFILE, source_path=path, source_sha256=source, constraints=rows,
                matching='lexical_hypothesis', observed_loading='unknown', enforcement='unknown',
                publication_allowed=False)
