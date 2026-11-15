"""Harness layers selected by `ahl improve --scope`: the project by default, user-level skills only on request.

Selecting a layer is not authorization. Only an explicit command-line --scope or the user's own saved default
(user_config, an owner-only file outside every project) adds the user-level layer; a project ahl.json can never name
one (config.validate). A user-level layer is a runtime's documented skill directory, named explicitly with --agent or
the saved default's own runtime and never inferred from a project file or the environment. Nothing here reads a
credential, a managed location or a file body.
"""
from dataclasses import dataclass
import os
from pathlib import Path

from . import user_config
from .audit.precedence import CLAUDE, CODEX, LOAD_ORDER
from .read_only import require_writable

SCOPES = ('project', 'global', 'project+global')
RUNTIMES = ('claude', 'codex')
# Documented load-order names that this command can address; the rest (managed, local) are never read or written.
LAYER_NAMES = {'user': 'global', 'project': 'project', 'project_root_to_task_directory': 'project'}
# Worst first. A status outside this list (an input or execution failure) outranks every entry.
STATUS_ORDER = ('requires_intervention', 'execution_error', 'budget_exhausted', 'blocked', 'inconclusive', 'applied', 'no_change')
NOT_ATTEMPTED = 'not_attempted'
BLOCKED_REASON = 'global_scope_model_evaluation_unavailable'


@dataclass(frozen=True)
class Layer:
    label: str
    root: Path
    allowed: object = None  # root-relative harness paths; None means default discovery


def selected(args):
    name = getattr(args, 'scope', 'project')
    if name not in SCOPES:
        raise ValueError('--scope must be project, global or project+global')
    return name


def apply_user_default(args, explicit):
    """Take the scope from the user's saved default only when the command line names none, and record where it came from."""
    if '--scope' in explicit:
        args._scope_source = 'command_line'
        return
    args._scope_source = 'default'
    saved = user_config.load_scope()
    if saved['scope'] is not None:
        args.scope, args._scope_source, args._user_scope_agent = saved['scope'], 'user_configuration', saved['agent']


def global_root(runtime):
    """The user-level location whose skills/ directory is the runtime's documented user skill directory."""
    if runtime == 'claude':
        configured = os.environ.get('CLAUDE_CONFIG_DIR')
        return Path(configured) if configured else Path.home() / '.claude'
    return Path.home() / '.agents'


def overlaps(first, second):
    first, second = Path(first).resolve(), Path(second).resolve()
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def runtime_of(args):
    """The runtime named on the command line, else the one saved beside the user's default scope. A project-configured,
    remembered or detected runtime is not a choice of a user-level location, so it never selects one."""
    explicit = getattr(args, '_configuration_explicit', None)
    agent, named = getattr(args, 'agent', 'auto'), explicit is None or '--agent' in explicit
    if agent in RUNTIMES and named:
        return agent
    if not named and getattr(args, '_user_scope_agent', None):
        return args._user_scope_agent
    why = 'the responses runtime has no user-level harness' if agent == 'responses' and named else 'the runtime was not named'
    raise ValueError(f'--scope {selected(args)} edits the user-level harness of one runtime and {why}; '
                     'pass --agent codex or --agent claude (a project file or the environment is never used to choose)')


def layer_order(runtime):
    """The runtime's documented load order, restricted to the layers this command can address."""
    return list(dict.fromkeys(LAYER_NAMES[name] for name in LOAD_ORDER[runtime] if name in LAYER_NAMES))


def user_skill_files(root):
    """Only the SKILL.md of each real (not linked) bundle: a repair needs nothing else, so no other file is opened."""
    from .workflow import skill_roots
    return tuple(sorted(str((skill / 'SKILL.md').relative_to(root)) for skill in skill_roots(root) if skill.parent == root / 'skills'))


def user_layer(runtime):
    root = global_root(runtime)
    skills = root / 'skills'
    if root.is_symlink() or skills.is_symlink():
        raise ValueError(f'the user-level {runtime} skill location {root} is a symlink; select the real directory')
    root = root.resolve()
    skills = root / 'skills'
    if not skills.is_dir():
        hint = ' or point CLAUDE_CONFIG_DIR at it' if runtime == 'claude' else ''
        raise ValueError(f'the user-level {runtime} skill directory {skills} does not exist; create it{hint}, '
                         'or evaluate the project alone with the default --scope project')
    require_writable(root)
    if not os.access(skills, os.W_OK | os.X_OK):
        raise ValueError(f'the user-level {runtime} skill directory {skills} is read-only for this user; AHL does not edit it')
    return Layer('global', root, user_skill_files(root))


def layers(args):
    """Layers in the runtime's documented load order. Raises before any state is written."""
    scope = selected(args)
    settings = getattr(args, 'project_settings', None)
    project = Layer('project', args.project, settings['harness_paths'] if settings else None)
    if scope == 'project':
        if any(overlaps(args.project, global_root(runtime)) for runtime in RUNTIMES):
            raise ValueError('the project directory overlaps a user-level harness location, which the default scope never edits; '
                             'select that location explicitly with --scope global and --agent')
        return [project]
    runtime = runtime_of(args)
    layer = user_layer(runtime)
    if scope == 'global':
        return [layer]
    if overlaps(args.project, layer.root):
        raise ValueError('the project directory and the user-level harness location overlap; evaluate one layer at a time')
    chosen = dict(project=project, **{'global': layer})
    return [chosen[name] for name in layer_order(runtime)]


def row(label, result, root=None):
    status = result['status']
    value = dict(layer=label, evaluated=status != NOT_ATTEMPTED, status=status, changed_files=list(result.get('changed_files', ())),
                 recommended_files=list(result.get('recommended_files', ())), reason=result.get('reason'))
    value.update({key: result[key] for key in ('plan', 'plan_sha256', 'deployment', 'journal', 'rollback', 'error') if key in result})
    if root is not None:
        value['root'] = str(root)
    return value


def flat(rows, key):
    return [name if item['layer'] == 'project' else f"{item['layer']}:{name}" for item in rows for name in item[key]]


def combine(args, results, base=None, roots=None):
    """One receipt for every scope: results are (layer, per-layer result) pairs in evaluation order. The per-layer
    results are not modified; the returned mapping is a new one that extends base."""
    roots = roots or {}
    rows = [row(label, result, roots.get(label)) for label, result in results]
    value = dict(base or {})
    attempted = [item for item in rows if item['evaluated']]
    worst = min(attempted, key=lambda item: STATUS_ORDER.index(item['status']) if item['status'] in STATUS_ORDER else -1, default=None)
    if worst is not None:
        value.update(status=worst['status'])
        if worst['reason'] is not None:
            value['reason'] = worst['reason']
    value.update(scope=selected(args), scope_source=getattr(args, '_scope_source', 'default'), layers=rows, layers_evaluated=[item['layer'] for item in rows if item['evaluated']],
                 layers_changed=[item['layer'] for item in rows if item['changed_files']],
                 changed_files=flat(rows, 'changed_files'), recommended_files=flat(rows, 'recommended_files'),
                 blocked=[dict(layer=item['layer'], status=item['status'], reason=item['reason'])
                          for item in rows if item['status'] in ('blocked', NOT_ATTEMPTED)])
    return value


def precedence(runtime, selection):
    """What the runtime documents about layer ordering; skills sharing a name across layers stay unresolved."""
    from .workflow import skill_roots
    order = LOAD_ORDER[runtime]
    names = [{skill.name for skill in skill_roots(layer.root)} for layer in selection]
    return dict(runtime=runtime, applies_to='instructions', documented_load_order=list(order),
                layer_order=layer_order(runtime),
                out_of_scope=[name for name in order if name not in LAYER_NAMES],
                source=CODEX if runtime == 'codex' else CLAUDE, same_name_skills='effective_layer_unknown',
                shared_skill_names=sorted(set.intersection(*names)))


def not_attempted(status):
    return dict(status=NOT_ATTEMPTED, reason=f'earlier_layer_{status}', changed_files=[])


def interrupted(args, result):
    """Fold a failure into a receipt that still names the layers an earlier step of this run completed."""
    current = getattr(args, '_scope_current', None)
    if current is None:
        return result
    done = list(getattr(args, '_scope_results', ()))
    return combine(args, [*done, (current, dict(result))], base=result, roots=getattr(args, '_scope_roots', None))


def blocked_result(args, output, remediation):
    """Model-driven evaluation of a user-level layer is not available: report every selected layer as not attempted."""
    selection = [layer.label for layer in layers(args)]
    if getattr(args, '_scope_source', None) == 'user_configuration':
        remediation += ' This scope came from your saved default: pass --scope project for one run, or run ahl config clear-scope to remove it.'
    pending = [(label, dict(status=NOT_ATTEMPTED, reason=BLOCKED_REASON, changed_files=[])) for label in selection]
    base = dict(status='blocked', reason=BLOCKED_REASON, output=str(output), changed_files=[], remediation=remediation)
    return combine(args, pending, base=base)
