"""Model-free static harness audit: a per-dimension conformance report over one read-only harness inventory.

audit() turns a sources.harness_inventory.HarnessInventory into one row per DIMENSIONS entry. Each inventory node,
and each facet a COMBINED runtime file also holds (the config and plugin parts of .claude/settings.json), is one
surface: CHECKS names the check its kind gets, which reads only the facts the inventory parser already recorded.
For Codex, chain.codex() follows the project-doc budget across the AGENTS.md directory chain from the same facts
and replaces the verdict of each file the default budget truncates or skips; it never adds a pass.
Nothing is reread, executed, loaded, written or sent to a model. A surface is 'unknown' when it was not read
(private_excluded), not parsed, failed to parse, has no check or no capability at the pin; a refused entry
(link, special file, hardlink) is 'unknown' in the dimension it would belong to. A dimension is 'nonconformant'
when any surface is, 'conformant' only when it has surfaces and every one conforms, and 'unknown' otherwise, so
an unread or excluded surface can never become a pass. A blocked or unsupported inventory gives every dimension
'unknown' and the report its status, never a partial pass. Every row is a convention check, not evidence that the
harness performs better or worse, and no aggregate score is emitted.
"""
from dataclasses import asdict, dataclass
import importlib
from agent_harness_lab import surfaces
from agent_harness_lab.audit import chain, skills
from agent_harness_lab.sources import agents_inventory, harness_inventory
from agent_harness_lab.sources.schema import digest, fingerprint, label
from agent_harness_lab.sources.skill_headers import TOKENIZER

PROFILE = 'ahl-static-audit-v1'
DIMENSIONS = ('instructions', 'rules', 'hooks', 'workflows', 'agents', 'commands', 'plugins', 'config')
STATUSES = ('conformant', 'nonconformant', 'unknown')
EVIDENCE = ('count', 'lexical', 'structural')
COSTS = ('deterministic_edit', 'content_edit', 'behavioral_evaluation_required')  # cheapest first
CHECK = __name__ + '.checks:'
NOTICE = 'convention_check_not_performance_evidence'

# (inventory kind, dimension, CAPABILITIES surface, evidence kind, 'module:function' or None). One self-contained
# row per kind, sorted by kind, so a later check replaces its kind's None or appends its own line.
CHECKS = (
    ('agent', 'agents', 'agents', 'structural', CHECK + 'agent'),
    ('command', 'commands', 'commands_skills', 'structural', CHECK + 'command'),
    ('config', 'config', 'settings', 'structural', CHECK + 'config'),
    ('exec_policy', 'rules', 'path_rules', 'structural', None),
    ('hook', 'hooks', 'hooks', 'structural', CHECK + 'hook'),
    ('instruction', 'instructions', 'instructions', 'count', CHECK + 'instruction'),
    ('path_rule', 'rules', 'path_rules', 'structural', CHECK + 'path_rule'),
    ('plugin', 'plugins', 'plugins', 'structural', CHECK + 'plugin'),
    ('skill', 'commands', 'commands_skills', 'structural', CHECK + 'skill'),
    ('workflow', 'workflows', 'workflows', 'lexical', CHECK + 'workflow'),
)
# Remediation for each nonconformant reason: (cost class, fixed remediation label). Unknown rows carry none.
REMEDIATIONS = {
    'agent_field_above_version_pin': ('deterministic_edit', 'remove_fields_above_the_runtime_pin'),
    'agent_rejected_by_runtime': ('content_edit', 'fix_the_role_file_the_runtime_rejects'),
    'agent_skipped_by_runtime': ('content_edit', 'add_frontmatter_name_and_description'),
    'agent_unknown_fields': ('deterministic_edit', 'remove_undocumented_agent_fields'),
    'command_name_key_ignored': ('deterministic_edit', 'remove_the_ignored_name_key'),
    'command_paths_key_ignored': ('deterministic_edit', 'remove_the_ignored_paths_key_or_use_a_skill'),
    'hook_handler_skipped': ('content_edit', 'replace_the_handler_type_the_runtime_skips'),
    'hook_on_failure_invalid': ('deterministic_edit', 'set_onfailure_to_continue_or_block'),
    'hook_plugin_hooks_wrapper_missing': ('deterministic_edit', 'wrap_events_in_a_top_level_hooks_key'),
    'hook_unknown_event': ('deterministic_edit', 'rename_or_remove_the_undocumented_event'),
    'hook_unknown_field': ('deterministic_edit', 'remove_undocumented_hook_fields'),
    'hook_unknown_handler_type': ('deterministic_edit', 'use_a_documented_handler_type'),
    chain.SKIPPED: ('content_edit', 'shorten_the_instruction_chain_or_raise_project_doc_max_bytes'),
    chain.TRUNCATED: ('content_edit', 'shorten_the_instruction_chain_or_raise_project_doc_max_bytes'),
    'instruction_empty': ('content_edit', 'add_instructions_or_remove_the_empty_file'),
    'instruction_exceeds_launch_bytes': ('content_edit', 'shorten_or_split_the_instruction_file'),
    'instruction_exceeds_project_doc_bytes': ('content_edit', 'shorten_or_split_the_instruction_file'),
    'plugin_manifest_name_missing': ('deterministic_edit', 'declare_the_plugin_name'),
    'plugin_marketplace_entries_missing': ('deterministic_edit', 'declare_a_plugins_list'),
    'skill_body_without_references': ('content_edit', 'move_detail_into_a_references_folder'),
    'skill_description_lacks_trigger_phrasing': ('content_edit', 'state_when_to_use_the_skill_in_the_description'),
    'skill_description_missing': ('content_edit', 'add_a_description_of_what_the_skill_does_and_when_to_use_it'),
    'skill_description_not_text': ('content_edit', 'write_the_description_as_text'),
    'skill_description_too_long': ('content_edit', 'shorten_the_description_to_1024_characters'),
    'skill_name_differs_from_directory': ('deterministic_edit', 'make_the_skill_name_match_its_directory'),
    'skill_name_invalid': ('deterministic_edit', 'use_a_lowercase_hyphenated_name_of_at_most_64_characters'),
    'skill_name_reserved_word': ('deterministic_edit', 'drop_the_reserved_word_from_the_skill_name'),
    'skill_reference_missing': ('content_edit', 'add_the_linked_file_or_remove_the_link'),
    'skill_routing_collision': ('behavioral_evaluation_required', 'separate_the_trigger_phrases_then_check_routing'),
    'workflow_lint_issues': ('content_edit', 'move_meta_first_and_drop_forbidden_calls'),
}


def registry():
    kinds = [row[0] for row in CHECKS]
    if (kinds != sorted(set(kinds)) or any(len(row) != 5 or row[1] not in DIMENSIONS or row[2] not in surfaces.SURFACES
            or row[3] not in EVIDENCE for row in CHECKS) or {row[1] for row in CHECKS} != set(DIMENSIONS)):
        raise ValueError('audit checks must be unique, sorted and name a dimension, surface and evidence kind')
    return {row[0]: row[1:] for row in CHECKS}


def _resolve(reference):
    module, _, name = reference.partition(':')
    return getattr(importlib.import_module(module), name)


def source(surface, runtime):
    """The first pinned public doc row for the surface, or None when the runtime documents none."""
    return next((row.doc_url for row in surfaces.CAPABILITIES if row.surface == surface and row.runtime == runtime), None)


@dataclass(frozen=True)
class Finding:
    id: str
    dimension: str
    status: str
    reason: str
    path: str
    path_sha256: str
    kind: str
    evidence_kind: str
    cost_class: str | None = None
    remediation: str | None = None
    source: str | None = None
    notice: str = NOTICE
    measures: tuple = ()  # sorted (label, non-negative int) pairs, such as the bytes a budget drops
    related: tuple = ()  # sorted checkout paths of the other skills a routing collision names

    def __post_init__(self):
        label(self.reason); label(self.kind); fingerprint(self.path_sha256); fingerprint(self.id)
        if type(self.measures) is not tuple or any(type(row) is not tuple or len(row) != 2 or type(row[1]) is not int
                or row[1] < 0 for row in self.measures):
            raise ValueError('invalid audit finding measures')
        for key, _ in self.measures: label(key)
        if type(self.related) is not tuple or list(self.related) != sorted(set(self.related)) or not all(type(p) is str for p in self.related):
            raise ValueError('audit finding related paths must be sorted unique text')
        if [row[0] for row in self.measures] != sorted({row[0] for row in self.measures}):
            raise ValueError('audit finding measures must be sorted and unique')
        if (self.dimension not in DIMENSIONS or self.status not in ('nonconformant', 'unknown')
                or self.evidence_kind not in EVIDENCE or self.notice != NOTICE
                or (self.status == 'nonconformant') != (self.cost_class is not None)
                or (self.cost_class is None) != (self.remediation is None)
                or self.cost_class is not None and self.cost_class not in COSTS):
            raise ValueError('invalid audit finding')


@dataclass(frozen=True)
class Dimension:
    dimension: str
    status: str
    surfaces: int
    conformant: int
    nonconformant: int
    unknown: int
    reasons: tuple
    source: str | None
    notice: str = NOTICE

    def __post_init__(self):
        counts = (self.conformant, self.nonconformant, self.unknown)
        if (self.dimension not in DIMENSIONS or self.status not in STATUSES or any(type(n) is not int or n < 0 for n in counts)
                or self.surfaces != sum(counts) or self.status != _verdict(*counts) or type(self.reasons) is not tuple
                or list(self.reasons) != sorted(set(self.reasons)) or self.notice != NOTICE):
            raise ValueError('invalid audit dimension')
        for reason in self.reasons: label(reason)


def _verdict(conformant, nonconformant, unknown):
    return 'nonconformant' if nonconformant else 'unknown' if unknown or not conformant else 'conformant'


def _views(node, runtime):
    """(kind, parse_status, facts) for the node's own kind and each COMBINED facet it carries or would carry."""
    facts = dict(node.facts)
    combined = harness_inventory.COMBINED.get(runtime, {}).get(node.path, ())
    others = sorted(kind for kind in combined if kind != node.kind)
    yield node.kind, node.parse_status, {k: v for k, v in facts.items() if not k.startswith(tuple(o + '.' for o in others))}
    for kind in others:
        prefix = kind + '.'
        status = facts.get(prefix + 'parse_status', 'not_read' if node.declared_state != 'present' else 'not_parsed')
        yield kind, status, {k[len(prefix):]: v for k, v in facts.items() if k.startswith(prefix) and k != prefix + 'parse_status'}


def _judge(node, kind, status, facts, runtime, row):
    dimension, surface, evidence, reference = row
    if node.declared_state != 'present': return 'unknown', 'private_excluded'
    if source(surface, runtime) is None: return 'unknown', 'no_pinned_capability'
    if status != 'parsed': return 'unknown', status
    if reference is None: return 'unknown', 'not_checked'
    if kind == 'agent': facts = dict(facts, layer=agents_inventory._layer(node, runtime, {})[0])  # where it would load
    verdict, reason = _resolve(reference)(facts, runtime)
    if verdict not in STATUSES or verdict == 'nonconformant' and reason not in REMEDIATIONS: raise ValueError('invalid check result')
    return verdict, reason


# The harness folders each runtime reads; another runtime's folder is never one of its surfaces.
HARNESS_FOLDERS = {'claude-code': ('.claude',), 'codex': ('.codex', '.agents')}


def _refused(refusal, runtime, table):
    """Dimensions a refused entry would belong to: its classified kinds, or every dimension inside a harness folder
    of the audited runtime."""
    found = surfaces.classify(refusal.path, runtime)
    if surfaces.VENDORED_FACET in found.facets: return ()
    dimensions = tuple(dict.fromkeys(table[kind][0] for kind in found.facets if kind in table))
    if dimensions: return dimensions
    parts = refusal.path.split('/')
    return DIMENSIONS if any(part in HARNESS_FOLDERS[runtime] for part in parts) else ()


def audit(inventory, *, task_cwd=None, collision_threshold=skills.COLLISION_THRESHOLD):
    """One conformance row per dimension over a declared inventory; blocked or unsupported is unknown everywhere.

    task_cwd, which must match the inventory's digest, settles the Codex project-doc chain; without it the chain is
    recovered from the listed paths or left unjudged. collision_threshold, in (0, 1], is the description token
    similarity at which two skills that load together are reported as a routing collision."""
    if type(inventory) is not harness_inventory.HarnessInventory: raise ValueError('audit needs a harness inventory')
    if task_cwd is not None and digest(task_cwd) != inventory.task_cwd_sha256: raise ValueError('task_cwd differs from the inventory')
    table, runtime = registry(), inventory.runtime
    budget = chain.codex(inventory, task_cwd)
    context = skills.Context(inventory, task_cwd) if inventory.status == 'declared' else None
    collisions = context.collisions(collision_threshold) if context else {}
    rows = {dimension: [] for dimension in DIMENSIONS}  # (status, reason) per surface
    findings, vendored, unattributed = [], 0, 0

    def record(dimension, verdict, reason, path, kind, evidence, surface, measures=(), cited=None, related=()):
        rows[dimension].append((verdict, reason))
        if verdict == 'conformant': return
        cost, remediation = REMEDIATIONS[reason] if verdict == 'nonconformant' else (None, None)
        findings.append(Finding(digest([dimension, path, kind, reason]), dimension, verdict, reason, path, digest(path), kind,
            evidence, cost, remediation, cited or source(surface, runtime), measures=measures, related=related))

    if inventory.status == 'declared':
        for node in inventory.nodes:
            if dict(node.facts).get(surfaces.VENDORED_FACET) is True: vendored += 1; continue
            if context.supporting(node): continue
            for kind, status, facts in _views(node, runtime):
                if kind not in table: continue
                if kind == 'skill' and node.path in context.skills: facts = dict(facts, **context.facts(node))
                verdict, reason = _judge(node, kind, status, facts, runtime, table[kind])
                dimension, surface, evidence, _ = table[kind]
                measures, cited, related = (), None, ()
                if kind == 'skill' and verdict == 'conformant' and node.path in collisions:  # a header pass never hides one
                    verdict, reason = 'nonconformant', 'skill_routing_collision'
                    related, measures = collisions[node.path]
                if kind == 'skill' and verdict == 'nonconformant':
                    cited = skills.cite(reason, runtime)
                    if reason in skills.ROUTING: evidence = 'lexical'
                if kind == 'instruction' and verdict != 'unknown' and node.origin == 'checkout' and node.path in budget:  # the chain outranks one file
                    verdict, evidence, cited, (reason, measures) = 'nonconformant', 'structural', chain.SOURCE, budget[node.path]
                record(dimension, verdict, reason, node.path, kind, evidence, surface, measures, cited, related)
        for refusal in inventory.refusals:
            if refusal.reason == 'vcs_metadata': continue  # .git is never a harness surface
            dimensions = _refused(refusal, runtime, table)
            unattributed += not dimensions
            for dimension in dimensions:
                surface = next(row[1] for row in table.values() if row[0] == dimension)
                record(dimension, 'unknown', 'refused_' + refusal.reason, refusal.path, 'refused', 'structural', surface)
    blocked = None if inventory.status == 'declared' else inventory.blocked_reason or inventory.status
    dimensions = []
    for dimension in DIMENSIONS:
        surface = next(row[1] for row in table.values() if row[0] == dimension)
        verdicts = [verdict for verdict, _ in rows[dimension]]
        counts = [verdicts.count(status) for status in STATUSES]
        reasons = {reason for verdict, reason in rows[dimension] if verdict != 'conformant'}
        if blocked: reasons.add('inventory_' + blocked)
        elif not verdicts: reasons.add('no_surface_declared')
        dimensions.append(Dimension(dimension, _verdict(*counts), len(verdicts), *counts, tuple(sorted(reasons)), source(surface, runtime)))
    rank = {cost: index for index, cost in enumerate((*COSTS, None))}
    findings.sort(key=lambda row: (row.status != 'nonconformant', rank[row.cost_class], DIMENSIONS.index(row.dimension), row.path, row.kind, row.reason))
    return Report(runtime, inventory.version, inventory.sha256, inventory.status, blocked,
        tuple(dimensions), tuple(findings), vendored, unattributed,
        skill_collision_threshold_permille=round(collision_threshold * 1000))


@dataclass(frozen=True)
class Report:
    runtime: str
    version: str
    harness_inventory_sha256: str
    inventory_status: str
    blocked_reason: str | None
    dimensions: tuple
    findings: tuple
    vendored_surfaces: int
    unattributed_refusals: int
    skill_collision_rule: str = skills.COLLISION_RULE
    skill_collision_threshold_permille: int = round(skills.COLLISION_THRESHOLD * 1000)
    skill_tokenizer: str = TOKENIZER
    schema_version: int = 1

    def __post_init__(self):
        label(self.runtime); label(self.version); fingerprint(self.harness_inventory_sha256)
        label(self.skill_collision_rule); label(self.skill_tokenizer)
        if (self.inventory_status not in ('declared', 'blocked', 'unsupported')
                or tuple(row.dimension for row in self.dimensions) != DIMENSIONS
                or not all(type(row) is Dimension for row in self.dimensions)
                or not all(type(row) is Finding for row in self.findings)
                or (self.inventory_status == 'declared') != (self.blocked_reason is None)
                or self.inventory_status != 'declared' and (self.findings or any(row.status != 'unknown' for row in self.dimensions))):
            raise ValueError('invalid audit report')

    @property
    def status(self): return 'completed' if self.inventory_status == 'declared' else self.inventory_status

    @property
    def sha256(self): return digest(asdict(self))

    def document(self):
        """The full private report: paths stay local, model and execution counts are fixed at zero."""
        return dict(asdict(self), profile=PROFILE, status=self.status, audit_sha256=self.sha256, notice=NOTICE,
            aggregate_score=None, model_calls=0, executed_files=0, files_written=0, network_used=False,
            publication_allowed=False, performance_evidence=False)


def run(checkout, runtime, *, task_cwd='.', project_trust='unknown', collision_threshold=skills.COLLISION_THRESHOLD):
    """Inventory the checkout at the runtime's pinned version and audit it; the checkout is only read."""
    if runtime not in surfaces.RUNTIMES: raise ValueError('unknown audit runtime')
    profile = dict(schema_version=1, runtime=runtime, version=surfaces.PINS[runtime], task_cwd=task_cwd)
    if project_trust != 'unknown': profile['project_trust'] = project_trust
    return audit(harness_inventory.capture(checkout, profile), task_cwd=task_cwd, collision_threshold=collision_threshold)
