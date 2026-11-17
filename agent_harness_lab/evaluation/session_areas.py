"""Advisory area classification of recorded sessions for `ahl score` (#799).

A session is described by a content-free signal summary: counts of tool names, file extensions and command families, never
a path, a command line or text. A fixed rule table maps those signals to the built-in areas (coding, ux-ui, docs, review);
no model is called. The result is advisory and observed-only: a session is not a controlled task, so nothing here can be
`scored`. A session that cannot be placed stays `unclassified` with a reason and is never given the nearest area.
"""
from collections import Counter
import re

PROFILE = 'ahl-session-areas-v1'
MAX_SESSIONS, MAX_KINDS, MAX_COUNT = 5000, 64, 1_000_000
MIN_SIGNALS = 3  # recognized signals a session needs before it can be placed
MIN_SHARE = (3, 5)  # the leading area needs at least 3/5 of the recognized signals
FIELDS = {'session': r'[A-Za-z0-9._:-]{1,64}', 'tools': r'[A-Za-z0-9_.:-]{1,64}',
          'extensions': r'[a-z0-9]{1,16}', 'commands': r'[a-z0-9][a-z0-9-]{0,31}'}
REASONS = ('no_signal', 'below_minimum_signal', 'ambiguous', 'area_not_declared')
# Tools that change files: a change makes review commands (git diff and friends) routine work, not evidence of a review.
CHANGE_TOOLS = frozenset(('Edit', 'MultiEdit', 'Write', 'NotebookEdit', 'apply_patch'))
EXTENSIONS = {'coding': 'py js mjs cjs ts go rs java kt c h cc cpp hpp cs rb php swift sql sh',
              'ux-ui': 'css scss sass less html htm vue svelte jsx tsx svg', 'docs': 'md mdx rst adoc txt'}
COMMANDS = {'coding': 'pytest python node npm pnpm yarn cargo go make mvn gradle dotnet tsc ruff mypy uv',
            'ux-ui': 'playwright cypress storybook stylelint lighthouse', 'docs': 'markdownlint vale mkdocs sphinx-build pandoc',
            'review': 'git-diff git-log git-show git-blame gh-pr'}
EXTENSION_AREA = {name: area for area, names in EXTENSIONS.items() for name in names.split()}
COMMAND_AREA = {name: area for area, names in COMMANDS.items() for name in names.split()}
RULED = tuple(dict.fromkeys((*EXTENSIONS, *COMMANDS)))  # the areas a rule can place a session in


def parse(value):
    """Check the sessions input: a list of at most 5000 summaries; errors name a position, never a value."""
    if not isinstance(value, list) or len(value) > MAX_SESSIONS:
        raise ValueError(f'sessions must be a JSON array of at most {MAX_SESSIONS} signal summaries')
    seen = set()
    for index, entry in enumerate(value):
        where = f'sessions[{index}]'
        if not isinstance(entry, dict) or not set(entry) <= set(FIELDS) or 'session' not in entry:
            raise ValueError(f'{where} must be an object with a session id and optional tools, extensions and commands')
        if not isinstance(entry['session'], str) or not re.fullmatch(FIELDS['session'], entry['session']):
            raise ValueError(f'{where}.session is not a valid session id')
        if entry['session'] in seen: raise ValueError(f'{where}.session repeats an earlier session id')
        seen.add(entry['session'])
        for field in ('tools', 'extensions', 'commands'):
            counts = entry.get(field, {})
            if (not isinstance(counts, dict) or len(counts) > MAX_KINDS
                    or any(not isinstance(k, str) or not re.fullmatch(FIELDS[field], k) for k in counts)
                    or any(type(n) is not int or not 0 < n <= MAX_COUNT for n in counts.values())):
                raise ValueError(f'{where}.{field} must map at most {MAX_KINDS} valid names to counts from 1 to {MAX_COUNT}')
    return value


def classify(entry, declared):
    """One session's placement: (area id or None, reason or None, recognized signals per area)."""
    changes = any(tool in CHANGE_TOOLS for tool in entry.get('tools', {}))
    hits = Counter()
    for name, count in entry.get('extensions', {}).items():
        if name in EXTENSION_AREA: hits[EXTENSION_AREA[name]] += count
    for name, count in entry.get('commands', {}).items():
        area = COMMAND_AREA.get(name)
        if area is not None and not (area == 'review' and changes): hits[area] += count
    total = sum(hits.values())
    if total == 0: return None, 'no_signal', {}
    if total < MIN_SIGNALS: return None, 'below_minimum_signal', dict(hits)
    lead, top = hits.most_common(1)[0]
    if top * MIN_SHARE[1] < total * MIN_SHARE[0]: return None, 'ambiguous', dict(hits)
    if lead not in declared: return None, 'area_not_declared', dict(hits)
    return lead, None, dict(hits)


def advisory(registry, sessions, areas=()):
    """The advisory section: sessions per declared area (narrowed to `areas`), the unclassified reasons and one row per session."""
    declared = {entry['id']: entry for entry in registry}
    placed = [(entry['session'], *classify(entry, declared)) for entry in sessions]
    count = Counter(area for _, area, _, _ in placed if area is not None)
    reasons = Counter(reason for _, area, reason, _ in placed if area is None)
    shown = [area for area in declared if not areas or area in areas]
    return dict(profile=PROFILE, label='advisory', evidence='observed_only', recorded=len(placed),
                areas=[dict(id=area, label=declared[area]['label'], state='advisory', classifiable=area in RULED,
                            sessions=count[area] if area in RULED else None) for area in shown],
                unclassified=dict(state='unclassified', sessions=sum(reasons.values()), reasons={r: reasons[r] for r in REASONS}),
                not_selected=sum(count[area] for area in declared if area not in shown),
                rows=[dict(session=name, state='advisory' if area else 'unclassified', area=area, reason=reason, signals=dict(sorted(hits.items())))
                      for name, area, reason, hits in placed])


def markdown(section):
    """The advisory section for people, below the scored table; deterministic."""
    lines = ['', '## Advisory: recorded sessions (observed only, never scored)', '',
             f"{section['recorded']} recorded sessions placed by content-free signals (tool names, file extensions, command families). "
             'A session is not a controlled task, so no row below is `scored`; this shows where recorded work looks like it happens, not how well the harness does there.', '',
             '| Area | State | Sessions |', '| --- | --- | --- |']
    for row in section['areas']:
        label = row['label'].replace('|', '\\|')
        lines.append(f"| {label} (`{row['id']}`) | advisory | {'n/a (no classification rule)' if row['sessions'] is None else row['sessions']} |")
    unplaced = section['unclassified']
    lines.append(f"| Unclassified | unclassified | {unplaced['sessions']} ({', '.join(f'{r} {n}' for r, n in unplaced['reasons'].items())}) |")
    if section['not_selected']: lines += ['', f"{section['not_selected']} sessions are placed in a declared area that --area did not select."]
    return '\n'.join(lines) + '\n'
