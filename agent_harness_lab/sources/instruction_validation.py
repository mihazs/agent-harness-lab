"""Effective-context validation of scoped instruction and path-rule candidates (#285, a slice of #79).

The I3 instruction resolver (sources.instructions) and the I4 rule resolver (sources.claude_rules) run again on a
baseline closure and a candidate closure. A closure is the project's in-checkout instruction and rule files, read
through the read-only harness inventory, overlaid with the staged harness copy; the staged copy wins. Nothing is
executed or loaded, and every verdict is a declaration from documented semantics, so observed_loading and
effective stay 'unknown'.

A candidate is blocked when it edits an instruction file outside the governing scope (the deepest directory on
the task path that holds an applicable instruction file, or the task directory itself) or a rule that matches
no declared task path; when it adds an import cycle or a missing import; when it changes which file shadows
which without the plan declaring it; when a rule's paths edit drops a declared task path the plan keeps; or when
a required text leaves the active context. Bytes are a secondary measurement: a reduction made by moving text
out of the active context earns no credit, and length never selects a winner. Removing active guidance is
recommended only when the I13 behavioral controls, run in surface-faithful trial workspaces, preserve it.

The plan's task_cwd and task_paths come from the proposer, so the scope they yield is provisional
(scope.source 'proposer_declared_plan') until bind() checks them against the evaluation manifest's task cwd
values (#339), which the proposer does not author. A plan whose task_cwd is no evaluated task's cwd, or whose
task path lies below none of them, is blocked, and only a bound document can back a recommendation.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import time

from agent_harness_lab import surfaces
from agent_harness_lab.evaluation.integrity import read_regular, relative
from . import claude_instructions, claude_rules, harness_inventory, instructions
from .git_view import Folder
from .schema import digest, label

PROFILE = 'ahl-instruction-validation-v1'
PLAN_FIELDS = {'schema_version', 'profile', 'task_paths', 'required', 'shadowing_changes', 'removed_task_paths'}
KINDS = ('instruction', 'path_rule')
# The trial runtime whose instruction semantics a candidate is validated under; the controlled Responses
# executor loads the root-to-cwd AGENTS.md chain, which is the Codex resolver's model.
RUNTIMES = {'claude': 'claude-code', 'codex': 'codex', 'responses': 'codex'}
MAX_REQUIRED, MAX_TEXT, MAX_SHADOWING = 64, 512, 256
ESTIMATOR = 'utf8-bytes-v1'
# Where the validated scope came from: the proposer's plan alone, or that plan checked against the evaluated tasks.
DECLARED, BOUND = 'proposer_declared_plan', 'evaluation_manifest_task_cwds'
PROPOSER_GUIDANCE = ('Edit an instruction file only at its governing scope (the deepest directory on the task path '
    'that holds an applicable instruction file, or the task directory itself), and a path rule only when it matches '
    'a declared task path. The evaluation decides where tasks run: task_cwd must be a directory an evaluated task '
    'runs in (the checkout root "." unless the evaluation declares a task cwd), and each task path must lie below '
    'one of them; any other plan is refused. When you change a nested instruction or any rule file, add '
    '"instruction_plan" to the '
    'proposal: {"schema_version":1,"profile":<the runtime instruction profile with task_cwd>,"task_paths":[...],'
    '"required":[{"id":...,"text":...}],"shadowing_changes":[{"change":"add"|"remove","shadow":...,"shadowed":...}],'
    '"removed_task_paths":[...]}. Never move required text out of the active context to cut bytes; a shorter file '
    'is not automatically better.')


class _Blocked(Exception):
    """A condition that blocks validation before both closures resolve."""


def _norm(text):
    return ' '.join(text.split())


def parse_plan(plan):
    """Validate a proposer-declared instruction plan; raises ValueError for anything outside the schema."""
    if (not isinstance(plan, dict) or not {'schema_version', 'profile'} <= set(plan) <= PLAN_FIELDS
            or type(plan['schema_version']) is not int or plan['schema_version'] != 1):
        raise ValueError('invalid instruction plan')
    profile = plan['profile']
    instructions.validate(profile)
    task_paths = claude_rules._declared(plan.get('task_paths', []), None)[0]
    removed = claude_rules._declared(plan.get('removed_task_paths', []), None)[0]
    if not set(removed) <= set(task_paths): raise ValueError('removed task paths must be declared task paths')
    required, shadowing = plan.get('required', []), plan.get('shadowing_changes', [])
    if not isinstance(required, list) or len(required) > MAX_REQUIRED: raise ValueError('invalid required behaviours')
    for item in required:
        if not isinstance(item, dict) or set(item) != {'id', 'text'}: raise ValueError('invalid required behaviour')
        label(item['id'])
        text = item['text']
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or '\n' in text or '\r' in text:
            raise ValueError('invalid required behaviour text')
    if len({item['id'] for item in required}) != len(required): raise ValueError('duplicate required behaviour')
    if not isinstance(shadowing, list) or len(shadowing) > MAX_SHADOWING: raise ValueError('invalid shadowing changes')
    for item in shadowing:
        if not isinstance(item, dict) or set(item) != {'change', 'shadow', 'shadowed'} or item['change'] not in ('add', 'remove'):
            raise ValueError('invalid shadowing change')
        relative(item['shadow']); relative(item['shadowed'])
    return dict(profile=profile, task_paths=task_paths, removed=frozenset(removed),
                required=tuple((item['id'], _norm(item['text'])) for item in required),
                shadowing=frozenset((item['change'], item['shadow'], item['shadowed']) for item in shadowing))


def _kind(path, runtime, fallback=()):
    """The surface kind under runtime; a Codex fallback filename is an instruction although no row names it."""
    if runtime == 'codex' and PurePosixPath(path).name in fallback: return 'instruction'
    return surfaces.classify(path, runtime, None, in_checkout=True).kind


def _scope(path, runtime):
    """Instruction scope directory: Claude folds .claude/CLAUDE.md into its parent, Codex uses the file's folder."""
    if runtime == 'claude-code':
        scope = claude_instructions.scope_of(path)
        if scope is not None: return scope
    return str(PurePosixPath(path).parent)


def changed_paths(original, proposed):
    """Changed paths of any runtime's instruction or rule kind, from {path: state} inventories."""
    return sorted(name for name in original.keys() | proposed.keys() if original.get(name) != proposed.get(name)
                  and _kind(name, None) in KINDS)


def outside_scope(original, proposed, scope):
    """Changed instruction or rule paths a candidate may not touch.

    Without a validated scope, only root instructions and files the frozen baseline already holds may change, so a
    same-named file elsewhere never becomes writable through discovery. With one, an instruction must sit in a
    governing directory and a rule must match a declared task path."""
    changed = changed_paths(original, proposed)
    if scope is None: return [name for name in changed if name not in surfaces.ROOT_INSTRUCTIONS and name not in original]
    runtime, fallback = scope['runtime'], tuple(scope['fallback_filenames'])
    def within(name):
        kind = _kind(name, runtime, fallback)
        if kind == 'instruction': return _scope(name, runtime) in scope['instruction_dirs']
        return kind == 'path_rule' and name in scope['rules']
    return [name for name in changed if not within(name)]


def _files(root):
    files = {}
    if root is None: return files
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink(): raise ValueError('instruction closure contains a symlink')
        if path.is_file(): files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _project_files(project, profile):
    """In-checkout instruction and rule files from the read-only harness inventory; personal files stay out."""
    declared = {key: profile[key] for key in harness_inventory.PROFILE_FIELDS}
    try: inventory = harness_inventory.capture(project, declared)
    except ValueError as error: raise _Blocked('task_cwd_unavailable') from error
    if inventory.status != 'declared': raise _Blocked('project_inventory_' + inventory.status)
    files = {}
    for node in inventory.nodes:
        if (node.origin != 'checkout' or node.content_sha256 is None
                or node.kind not in KINDS and claude_instructions.scope_of(node.path) is None
                or surfaces.classify(node.path).writability == 'private_excluded'):
            continue
        raw = read_regular(os.path.join(os.path.abspath(project), node.path), harness_inventory.MAX_BYTES)
        if hashlib.sha256(raw).hexdigest() != node.content_sha256: raise ValueError('instruction source changed during reading')
        files[node.path] = raw
    return files


def _checkout_read(project, target, budget):
    """A regular in-checkout file reached without crossing a link or a nested repository; None otherwise."""
    current, parts = os.path.abspath(project), PurePosixPath(target).parts
    try:
        for part in parts[:-1]:
            current = os.path.join(current, part)
            if not stat.S_ISDIR(os.lstat(current).st_mode) or os.path.lexists(os.path.join(current, '.git')): return None
        return read_regular(os.path.join(current, parts[-1]), budget)
    except (OSError, ValueError): return None


def _imported(project, sources, known, cwd):
    """In-checkout targets of @path imports reachable from (path, bytes) sources, read-only and bounded; private files stay out.

    Imported project documents belong to the closure, so a missing import means the target is missing. Targets one
    hop past the resolver's limit are read too, so the resolver can label them rather than call them missing."""
    found, queue, budget = {}, [(name, raw, 0) for name, raw in sources], harness_inventory.MAX_BYTES
    seen = {name for name, _ in sources} | set(known)
    while queue:
        source, raw, hops = queue.pop()
        if hops > claude_instructions.IMPORT_HOPS: continue
        for token, _ in claude_instructions.imports(raw.decode('utf-8', 'replace')):
            target = claude_instructions.resolve(source, token, cwd)[0]
            if target is None or target in seen: continue
            seen.add(target)
            if surfaces.classify(target).writability == 'private_excluded': continue
            data = _checkout_read(project, target, budget)
            if data is None: continue
            budget -= len(data); found[target] = data; queue.append((target, data, hops + 1))
    return found


def _stale(project, staged):
    """Whether a staged instruction or rule file no longer matches the project's file at that path."""
    for name, raw in staged.items():
        if _kind(name, None) not in KINDS: continue
        path = os.path.join(os.path.abspath(project), name)
        try:
            if not os.path.lexists(path) or read_regular(path, harness_inventory.MAX_BYTES) != raw: return True
        except (OSError, ValueError): return True
    return False


def _states(files):
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}


def _strip_frontmatter(text):
    lines = text.splitlines()
    if lines and lines[0].strip() == '---':
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() in ('---', '...')), None)
        if end is not None: return '\n'.join(lines[end + 1:])
    return text


class _View:
    """One resolved closure: declared applicability per context, text per file and graph edges by path."""

    def __init__(self, root, name, files, plan, deadline):
        self.files, profile = files, plan['profile']
        self.runtime, self.cwd = profile['runtime'], profile['task_cwd']
        self.fallback = tuple(profile.get('fallback_filenames', ()))
        tree = root / name
        for relative_path, raw in files.items():
            (tree / relative_path).parent.mkdir(parents=True, exist_ok=True)
            (tree / relative_path).write_bytes(raw)
        (tree / self.cwd).mkdir(parents=True, exist_ok=True)
        with Folder(root, deadline, limit_total_bytes=False, limit_tree=False) as folder:
            self.inventory = instructions.capture(profile, folder, name)
        self.rules = None
        if self.runtime == 'claude-code' and self.inventory.resolution_status == 'declared':
            declared = {key: profile[key] for key in harness_inventory.PROFILE_FIELDS}
            self.rules = claude_rules.capture(tree, declared, task_paths=list(plan['task_paths']) or None)
        paths = {digest(path): path for path in files}
        self.nodes = {paths[n.path_sha256]: n for n in self.inventory.nodes if n.path_sha256 in paths}
        def pairs(kind):
            return {(paths[e.source_sha256], paths[e.target_sha256]) for e in self.inventory.edges
                    if e.kind == kind and e.source_sha256 in paths and e.target_sha256 in paths}
        self.shadows, self.cycles = pairs('shadows'), pairs('import_cycle')
        unresolved = {(n.path_sha256, n.reason) for n in self.inventory.nodes if n.path_sha256 not in paths}
        self.unresolved = {(source, reason) for source in files for reason in {r for _, r in unresolved}
                           if (digest(['unresolved_imports', source, reason]), reason) in unresolved}
        self.contexts = plan['task_paths'] or ('.',)
        chain = claude_rules.chain(self.cwd)
        self.matched = {}
        for node in (self.rules.nodes if self.rules is not None and self.rules.status == 'declared' else ()):
            status, patterns = claude_rules.declaration(files[node.path])
            for context in self.contexts:
                below = context != '.' and (node.base_dir == '.' or context.startswith(node.base_dir + '/'))
                matched = (node.classification == 'launch' or node.classification == 'on_demand_subdirectory' and below
                           or context != '.' and claude_rules.classify(node.base_dir, status, patterns, chain,
                                                                       (context,), None) == 'conditional_matched')
                if matched: self.matched.setdefault(context, set()).add(node.path)

    @property
    def status(self):
        if self.inventory.resolution_status != 'declared': return self.inventory.resolution_status
        return 'declared' if self.rules is None else self.rules.status

    def instructions(self, context):
        """Applicable instruction files for one context; on-demand Claude files count below their scope."""
        return {path for path, node in self.nodes.items() if node.applicability == 'declared_applicable'
                or node.applicability == 'unknown' and node.reason == claude_instructions.ON_DEMAND
                and context != '.' and context.startswith(_scope(path, self.runtime) + '/')}

    def active(self, context):
        return self.instructions(context) | self.matched.get(context, set())

    def text(self, path):
        text = self.files[path].decode('utf-8', 'replace')
        return _strip_frontmatter(text) if _kind(path, self.runtime) == 'path_rule' else text

    def lines(self, paths):
        return {_norm(line) for path in paths for line in self.text(path).splitlines() if _norm(line)}

    def holds(self, text, paths):
        return any(text in _norm(self.text(path)) for path in paths)


def _governing(view, plan):
    """Directories whose instruction files govern the task, and the rules that match a declared task path."""
    depth = lambda scope: 0 if scope == '.' else len(PurePosixPath(scope).parts)
    dirs, chain = {view.cwd}, claude_rules.chain(view.cwd)
    for context in view.contexts:
        scopes = [scope for scope in (_scope(path, view.runtime) for path in view.instructions(context)
                                      if _kind(path, view.runtime, view.fallback) == 'instruction')
                  if scope in chain or context != '.' and context.startswith(scope + '/')]
        if scopes: dirs.add(max(scopes, key=depth))
    return dirs, {path for context in view.contexts for path in view.matched.get(context, ())}


def _operations(staged_before, staged_after, runtime, fallback):
    operations = []
    for name in sorted(staged_before.keys() | staged_after.keys()):
        before, after = staged_before.get(name), staged_after.get(name)
        kind = _kind(name, runtime, fallback)
        if before == after or kind not in KINDS and _kind(name, None) not in KINDS: continue
        operations.append(dict(path=name, kind=kind, before_sha256=before, after_sha256=after,
                               operation='add' if before is None else 'remove' if after is None else 'update'))
    return operations


def _document(status, reasons, **fields):
    value = dict(schema_version=1, profile=PROFILE, status=status, reasons=sorted(set(reasons)), observed_loading='unknown',
                 effective='unknown', length_selects_winner=False, publication_allowed=False, adoption_authority=False)
    value.update(fields)
    return value


def validate(baseline, candidate, plan, *, runtime=None, project=None, deadline=None):
    """Validate a staged candidate against its baseline under a declared instruction plan.

    baseline and candidate are staged harness copies; project, when given, supplies the in-checkout instruction
    and rule files the copies do not stage, read-only. Returns a document whose status is valid, blocked or
    unsupported; it never raises for a candidate or plan defect."""
    if deadline is None: deadline = time.monotonic() + 60
    try: parsed = parse_plan(plan)
    except (ValueError, TypeError, KeyError):
        return _document('blocked', ['instruction_plan_required' if plan is None else 'instruction_plan_invalid'], scope=None)
    profile = parsed['profile']
    pair = (profile['runtime'], profile['version'])
    identity = dict(runtime=pair[0], version=pair[1], plan_sha256=digest(plan), task_cwd=profile['task_cwd'])
    if runtime is not None and runtime != pair[0]:
        return _document('blocked', ['plan_runtime_differs'], scope=None, **identity)
    if pair not in (instructions.CODEX, instructions.CLAUDE):
        return _document('unsupported', ['runtime_version_unsupported'], scope=None, **identity)
    fallback = tuple(profile.get('fallback_filenames', ()))
    staged_before, staged_after = _files(baseline), _files(candidate)
    try: ambient = _project_files(project, profile) if project is not None else {}
    except _Blocked as error: return _document('blocked', [str(error)], scope=None, **identity)
    if project is not None and pair[0] == 'claude-code':
        sources = [*ambient.items(), *staged_before.items(), *staged_after.items()]
        ambient.update(_imported(project, sources, staged_before, profile['task_cwd']))
    reasons = ['stale_baseline'] if project is not None and _stale(project, staged_before) else []
    ambient = {name: raw for name, raw in ambient.items() if name not in staged_before}
    with tempfile.TemporaryDirectory(prefix='ahl-instruction-validation-') as scratch:
        root = Path(scratch)
        try:
            before = _View(root, 'baseline', {**ambient, **staged_before}, parsed, deadline)
            after = _View(root, 'candidate', {**ambient, **staged_after}, parsed, deadline)
        except (ValueError, UnicodeDecodeError):
            return _document('blocked', ['resolution_failed'], scope=None, **identity)
        if 'unsupported' in (before.status, after.status):
            return _document('unsupported', ['runtime_version_unsupported'], scope=None, **identity)
        if before.status != 'declared' or after.status != 'declared':
            return _document('blocked', ['resolution_blocked'], scope=None, **identity)
        return _evaluate(before, after, parsed, staged_before, staged_after, fallback, reasons, identity,
                         project_sources_sha256=digest(_states(ambient)) if project is not None else None)


def _evaluate(before, after, plan, staged_before, staged_after, fallback, reasons, identity, **bindings):
    runtime = plan['profile']['runtime']
    dirs, rules = _governing(before, plan)
    rules |= _governing(after, plan)[1]
    # The scope derives from the proposer's plan (task_cwd, task_paths) and stays provisional until bind().
    scope = dict(runtime=runtime, fallback_filenames=list(fallback), instruction_dirs=sorted(dirs), rules=sorted(rules),
                 source=DECLARED)
    # Operations compare the closures, so an edit to an unstaged project file carries its real baseline hash.
    operations = _operations(_states(before.files), _states(after.files), runtime, fallback)
    findings = []
    def find(reason, path=None):
        reasons.append(reason); findings.append(dict(reason=reason, path=path))
    for operation in operations:
        name, kind = operation['path'], operation['kind']
        if kind not in KINDS: find('not_a_runtime_instruction_surface', name)
        elif kind == 'instruction' and _scope(name, runtime) not in dirs: find('outside_governing_scope', name)
        elif kind == 'path_rule' and name not in rules: find('rule_matches_no_task_path', name)
    for source, target in sorted(after.cycles - before.cycles): find('import_cycle', source)
    for source, reason in sorted(after.unresolved - before.unresolved):
        find('missing_import' if reason == 'import_missing' else 'import_unresolved', source)
    for change, pairs in (('add', after.shadows - before.shadows), ('remove', before.shadows - after.shadows)):
        for shadow, shadowed in sorted(pairs):
            if (change, shadow, shadowed) not in plan['shadowing']: find('undeclared_shadowing_change', shadowed)
    for context in plan['task_paths']:
        if context in plan['removed']: continue
        for path in sorted(before.matched.get(context, set()) - after.matched.get(context, set())):
            if path in after.files and _kind(path, runtime) == 'path_rule': find('rule_paths_dropped_task_path', path)
    required = []
    for identifier, text in plan['required']:
        states = []
        for context in before.contexts:
            was, now = before.holds(text, before.active(context)), after.holds(text, after.active(context))
            states.append((was, now))
            if was and not now:
                find('required_text_left_active_context' if after.holds(text, after.files) else 'required_text_removed')
        if not any(was or now for was, now in states): find('required_text_not_active')  # newly required, never active
        required.append(dict(id=identifier, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                             baseline_active=any(was for was, _ in states), candidate_active=any(now for _, now in states)))
    lost = set()
    for context in before.contexts:
        lost |= before.lines(before.active(context)) - after.lines(after.active(context))
    moved = lost & after.lines(after.files)
    for operation in operations:
        if operation['operation'] != 'remove' or operation['kind'] not in KINDS: continue
        where = [context for context in before.contexts if operation['path'] in before.active(context)]
        if where and all(before.lines({operation['path']}) <= after.lines(after.active(context)) for context in where):
            operation['operation'] = 'consolidate'
    def active_bytes(view):
        return sum(len(view.files[path]) for path in {p for c in view.contexts for p in view.active(c)})
    baseline_bytes, candidate_bytes = active_bytes(before), active_bytes(after)
    moved_bytes = sum(len(line.encode()) + 1 for line in moved)  # each moved line with its newline
    status = 'blocked' if reasons else 'valid'
    credited = max(0, baseline_bytes - candidate_bytes - moved_bytes) if status == 'valid' else 0
    return _document(status, reasons, scope=scope, operations=operations, findings=findings, required=required,
        measurements=dict(estimator=ESTIMATOR, baseline_active_bytes=baseline_bytes, candidate_active_bytes=candidate_bytes,
                          moved_out_of_active_context_bytes=moved_bytes, credited_reduction_bytes=credited,
                          selects_winner=False),
        removal=dict(required=bool(lost), lost_active_lines=len(lost), moved_out_of_active_context_lines=len(moved),
                     disposition='requires_behavioral_controls' if lost else 'not_applicable'),
        baseline=dict(instruction_inventory_sha256=before.inventory.sha256,
                      rules_receipt_sha256=before.rules.sha256 if before.rules is not None else None,
                      closure_sha256=digest(_states(before.files)), staged_sha256=digest(_states(staged_before))),
        candidate=dict(instruction_inventory_sha256=after.inventory.sha256,
                       rules_receipt_sha256=after.rules.sha256 if after.rules is not None else None,
                       closure_sha256=digest(_states(after.files)), staged_sha256=digest(_states(staged_after))),
        **identity, **bindings)


def task_cwds(manifest):
    """Directories the evaluated tasks run in: each task's cwd, or the checkout root for a task that declares none."""
    from agent_harness_lab.evaluation.instruction_context import chain_paths
    tasks = manifest.get('tasks') if isinstance(manifest, dict) else None
    if not isinstance(tasks, list) or not tasks or not all(isinstance(task, dict) for task in tasks):
        raise ValueError('evaluation manifest declares no tasks')
    cwds = set()
    for task in tasks:
        cwd = task.get('cwd', '.')
        chain_paths(cwd)
        cwds.add(cwd)
    return sorted(cwds)


def _below(path, directory):
    return directory == '.' or path == directory or path.startswith(directory + '/')


def bind(document, plan, manifest):
    """Bind a valid document's scope to the evaluated tasks, which the proposer does not author (#339).

    The plan's task_cwd must be one of the manifest's task cwds and each declared task path must lie below one of
    them; otherwise the document is blocked (task_cwd_not_evaluated, task_path_not_evaluated). Either way
    scope.source records the binding and scope.task_cwds the cwds it was checked against. A document that is not
    valid is returned unchanged; an unreadable manifest raises ValueError."""
    if document.get('status') != 'valid' or document.get('scope') is None: return document
    cwds, parsed = task_cwds(manifest), parse_plan(plan)
    cwd = parsed['profile']['task_cwd']
    findings = [] if cwd in cwds else [dict(reason='task_cwd_not_evaluated', path=cwd)]
    findings += [dict(reason='task_path_not_evaluated', path=path) for path in parsed['task_paths']
                 if not any(_below(path, evaluated) for evaluated in cwds)]
    bound = dict(document, scope=dict(document['scope'], source=BOUND, task_cwds=cwds),
                 findings=[*document.get('findings', ()), *findings])
    if findings: bound.update(status='blocked', reasons=sorted({*document['reasons'], *(f['reason'] for f in findings)}))
    return bound


def current(document, plan, baseline, candidate, *, project=None, deadline=None):
    """Reasons a validated document no longer describes the project and the evaluated candidate; [] when it does.
    A scope never bound to the evaluated tasks is 'scope_unbound', so it cannot back a recommendation."""
    if document.get('status') != 'valid': return ['not_valid']
    again = validate(baseline, candidate, plan, runtime=document['runtime'], project=project, deadline=deadline)
    reasons = [] if (document.get('scope') or {}).get('source') == BOUND else ['scope_unbound']
    if ('stale_baseline' in again['reasons'] or again.get('project_sources_sha256') != document.get('project_sources_sha256')
            or again.get('baseline', {}).get('staged_sha256') != document['baseline']['staged_sha256']):
        reasons.append('stale_baseline')
    if again.get('candidate', {}).get('staged_sha256') != document['candidate']['staged_sha256']:
        reasons.append('candidate_changed')
    return reasons


def _faithful(trial, operations):
    """The I13 trial workspace held each changed file exactly as its version has it, or lacked a removed one."""
    surfaces_record = trial.get('workspace_surfaces')
    if not isinstance(surfaces_record, dict) or not isinstance(surfaces_record.get('included'), list):
        return False
    included = {item.get('path'): item.get('sha256') for item in surfaces_record['included'] if isinstance(item, dict)}
    absent = set(surfaces_record.get('absent') or ())
    field = 'before_sha256' if trial.get('version') == 'baseline' else 'after_sha256'
    return all(included.get(op['path']) == op[field] if op[field] is not None
               else op['path'] in absent and op['path'] not in included for op in operations)


def removal_disposition(document, trials):
    """Whether removing active guidance may be recommended: only when regression controls pass for both versions
    in workspaces that staged each version faithfully. A recommendation still needs independent certification.
    trials=None means the evaluation exposes no trial records to this gate, so a removal stays inconclusive."""
    removal = dict(document.get('removal') or dict(required=False), certification='required', observed_loading='unknown')
    if not removal.get('required'): return dict(removal, disposition='not_applicable', reason='no_active_guidance_removed')
    if trials is None: return dict(removal, disposition='inconclusive', reason='trial_records_unavailable')
    controls = [t for t in trials if isinstance(t, dict) and t.get('split') == 'regression']
    versions = {version: [t for t in controls if t.get('version') == version] for version in ('baseline', 'candidate')}
    if not versions['baseline'] or not versions['candidate']:
        disposition, reason = 'inconclusive', 'behavioral_controls_missing'
    elif any(t.get('status') != 'passed' for t in versions['candidate']):
        disposition, reason = 'rejected', 'behavioral_control_failed'
    elif any(t.get('status') != 'passed' for t in versions['baseline']):
        disposition, reason = 'inconclusive', 'baseline_control_failed'
    elif not all(_faithful(t, document.get('operations', ())) for t in controls):
        disposition, reason = 'inconclusive', 'workspace_surfaces_unverified'
    else:
        disposition, reason = 'recommended', 'behavioral_controls_preserved'
    return dict(removal, disposition=disposition, reason=reason)


def hook(args, baseline, candidate, original, proposed, proposal, agent, *, deadline=None):
    """Workflow validation hook. None when the candidate changes no nested instruction or rule file and declares
    no plan, so root-only edits keep their previous path; otherwise a validation document. Sets the scope that
    one_call.check_scope enforces."""
    args._instruction_scope = None
    plan = proposal.get('instruction_plan') if isinstance(proposal, dict) else None
    nested = [name for name in changed_paths(original, proposed) if name not in surfaces.ROOT_INSTRUCTIONS]
    if plan is None and not nested: return None
    document = validate(baseline, candidate, plan, runtime=RUNTIMES.get(agent), project=args.project, deadline=deadline)
    args._instruction_scope = document.get('scope')
    return document
