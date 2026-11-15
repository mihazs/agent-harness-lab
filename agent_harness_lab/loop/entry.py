"""The loop switch of `ahl improve` and `ahl skills invoke`: the four flags and where a run's declaration layers come from.

A flag or a loop declaration (the project ahl.json `loop` object or the user file's) opts one run into the loop; with none
the command keeps its single pass. Every flag value is checked here with the loop's own reasons, before the run spends
anything, and a declaration never selects a scope, enables apply or lifts recommendation-only: the resolver refuses those keys.
"""
from agent_harness_lab import typed_exit

from .spec import resolve

FLAGS = (('accept_score', '--accept-score'), ('max_rounds', '--max-rounds'), ('plateau_rounds', '--plateau-rounds'),
         ('on_regression', '--on-regression'))
EXIT_TEXT = ('Loop exits (only with --accept-score, --max-rounds, --plateau-rounds, --on-regression or a loop declaration): '
             '0 accepted (accepted_without_change, acceptance_score_reached); 1 requires_intervention (budget_exhausted, interrupted, '
             'comparison_invalid); 2 invalid declaration; 3 not_accepted (max_rounds, plateau, regression, no_actionable_mechanism, '
             'evidence_insufficient) or blocked (loop_requires_frozen_bank). Accepted is not certification: the receipt always states '
             'certification unverified and nothing is applied.\n')


def add_options(command):
    """Add the loop flags to the improve parser; the shared parser serves `ahl skills invoke` too. Values stay text so a bad one is a typed loop reason."""
    command.add_argument('--accept-score', metavar='X', help='Opt into the evaluate > improve > evaluate loop and stop when the lower bound of the frozen development and regression success rate reaches X, a number above 0 and at most 1; needs a pinned bank in ahl.json; with no value the loop stops on a supported improvement over its own baseline')
    command.add_argument('--max-rounds', metavar='N', help='Opt into the loop with at most N improvement rounds, a whole number from 1 to 10 (default 3); each round is one look at the frozen bank, so alpha is split across them')
    command.add_argument('--plateau-rounds', metavar='N', help='Opt into the loop and stop after N consecutive rounds without a supported improvement, from 1 up to --max-rounds (default 2)')
    command.add_argument('--on-regression', metavar='stop|rollback_continue', help='Opt into the loop; what a candidate-caused regression within its margin does: stop (default) or roll back to the best snapshot and try another mechanism; a critical or per-area regression always stops')


def declaration(args):
    """The command-line layer of the loop declaration: only the flags that were given, as the text they carried."""
    return {name: getattr(args, name) for name, _ in FLAGS if getattr(args, name, None) is not None}


def _convert(layer):
    """The typed value of each flag text; a text that is not a number of the right kind is passed on unchanged for the resolver to refuse."""
    found = dict(layer)
    for name, parse in (('accept_score', float), ('max_rounds', int), ('plateau_rounds', int)):
        if name in found:
            try: found[name] = parse(found[name])
            except ValueError: pass  # the spec's own check names the reason
    return found


def layers(args):
    """The four declaration layers of one run: cli, user, project (each None when it declares nothing) and the declared area ids."""
    from agent_harness_lab import user_config
    from agent_harness_lab.evaluation.areas import parse_registry
    settings = getattr(args, 'project_settings', None) or {}
    areas = parse_registry(settings['areas']) if 'areas' in settings else ()
    return dict(cli=_convert(declaration(args)) or None, user=user_config.load_loop()['loop'], project=settings.get('loop'), areas=areas)


def requested(found):
    return any(found[name] is not None for name in ('cli', 'user', 'project'))


def limits(args, trials):
    """The ceilings the spec references: the whole-run seconds and the trial ceiling, and where the seconds came from."""
    source = 'cli' if '--timeout' in getattr(args, '_configuration_explicit', ()) else 'project' if getattr(args, 'project_settings', None) else 'default'
    return dict(seconds=args.timeout, trials=trials), source


def spec_for(args, found):
    """Resolve the declaration against this run's ceilings. A round reserves one preparation role from the shared trial ceiling."""
    layers_ = {key: found[key] for key in ('cli', 'user', 'project', 'areas')}
    rounds = resolve(limits=dict(seconds=args.timeout, trials=args.trial_limit), **layers_).max_rounds
    ceilings, source = limits(args, max(1, args.trial_limit - rounds))
    return resolve(limits=ceilings, limits_source=source, **layers_)


def prepare(args):
    """Decide whether this improve run is a loop run and check its declaration before any spend; sets args._loop to the layers or None.

    A declaration problem raises LoopError (exit 2 with its reason); a combination the loop cannot honor is a usage conflict.
    """
    args._loop = None
    found = layers(args)
    if not requested(found): return
    if getattr(args, 'repair_contracts', False):
        raise typed_exit.TypedExit('invalid_usage', '--repair-contracts applies deterministic repairs and does not combine with the loop options or a loop declaration')
    spec_for(args, found)  # every layer is checked now, so a bad value never costs a run
    args._loop = found
    return found
